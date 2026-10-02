"""congreso_dieta workflow: Mini App actions, absence prompt and the daily document run."""
from __future__ import annotations

import asyncio
import shutil
from datetime import date, datetime, timedelta
from typing import Optional
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from fichaxebot.commands.vacations import _build_vacations_url
from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.absence_request import (
    AbsenceRequestCancelled, AbsenceRequestError, AbsenceRequestUncertain,
)
from fichaxebot.scrap_functions.commit import ReadOnlyStop
from fichaxebot.scrap_functions.congress_request import CongressRequestCancelled, CongressRequestError
from fichaxebot.utils import get_madrid_now
from fichaxebot.webapp_controller.vacation_confirmation import ACTIVE_KEY, STOPPING_KEY, PendingVacation
from plugins.congreso_dieta import dates, pdf, presentation, spreadsheet, usc
from plugins.congreso_dieta.cases import Absence, Case, CaseStore, Stage
from plugins.congreso_dieta.config import PluginConfig

logger = get_logger(__name__)

PROMPT_KIND = "congreso_dieta.absence_prompt"
DAILY_JOB = "congreso_dieta.daily"
CALLBACK_PATTERN = r"^cdieta_(?:absence_yes|absence_no|cancel_yes|cancel_no|action_yes|action_no):[0-9a-f]{32}$"
ABSENCES_URL = "https://fichaxe.usc.gal/pas/solicitudesPropias"
FAILURES_BEFORE_NOTICE = 3
KEEP_COMPLETED = timedelta(hours=24)  # finished procedures stay listed so the signature can be repeated

STAGE_LABELS = {
    Stage.NO_AUTH: "Sin autorización (procedimiento especial)",
    Stage.AWAITING_AUTH: "Esperando la autorización firmada",
    Stage.AUTH_RECEIVED: "Autorización recibida",
    Stage.GENERATED: "Documento generado; pendiente de firma",
    Stage.SIGNED: "Firmado; pendiente de entrega",
}
ABSENCE_LABELS = {
    Absence.SCHEDULED: "ausencia pendiente de preguntar",
    Absence.ASKING: "ausencia pendiente de tu respuesta",
    Absence.REQUESTING: "solicitando la ausencia",
    Absence.REQUESTED: "ausencia solicitada",
    Absence.UNCERTAIN: "ausencia sin confirmar (revisa USC)",
    Absence.SKIPPED: "ausencia no solicitada",
    Absence.SIMULATED: "ausencia simulada",
    Absence.NOT_REQUESTED: "ausencia no solicitada",
    Absence.NOT_REQUIRED: "ausencia no necesaria",
}


class PendingCongress(PendingVacation):
    callback_prefix = "congreso"
    filename = "solicitud-congreso.pdf"


class PendingPluginAbsence(PendingVacation):
    callback_prefix = "absence"
    filename = "solicitud-ausencia.png"


def span(case: Case) -> str:
    return f"{case.start_date:%d/%m} al {case.end_date:%d/%m/%Y}"


def describe(case: Case) -> str:
    stage = "Finalizado; documento firmado y entregado" if case.completed_at else STAGE_LABELS[case.stage]
    text = f"{stage} · {ABSENCE_LABELS[case.absence]}"
    return f"{text} · simulado" if case.simulated else text


def document_name(case: Case) -> str:
    return f"dieta_{case.start_date:%Y%m%d}_{case.end_date:%Y%m%d}"


def find_absence_type(catalog: dict, name: str) -> Optional[dict]:
    wanted = " ".join(name.split()).casefold()
    return next((kind for kind in catalog.get("types", [])
                 if " ".join(kind["name"].split()).casefold() == wanted), None)


def build_absence_request(case: Case, kind: dict, days: list[date], absence) -> dict:
    hours = {"startTime": absence.start_time, "endTime": absence.end_time} if kind["requiresHours"] else {}
    return {"year": case.start_date.year, "absenceTypeId": kind["id"],
            "periods": [{"date": day.isoformat(), **hours} for day in days],
            "observations": "", "attachments": []}


class CongresoDieta:
    def __init__(self, application, config: PluginConfig, store: CaseStore, *,
                 clock=get_madrid_now, generate=spreadsheet.generate_pdf, join=pdf.join_pdfs,
                 sign=pdf.sign_pdf) -> None:
        self.app = application
        self.config = config
        self.store = store
        self.clock = clock
        self._generate_pdf = generate
        self._join_pdfs = join
        self._sign_pdf = sign
        self.launch_token: Optional[str] = None
        self.cancel_requests: dict[str, str] = {}
        self.action_requests: dict[str, tuple[str, str, str]] = {}
        self.document_busy: set[str] = set()
        self.generation_lock = asyncio.Lock()

    # Helpers -----------------------------------------------------------------

    @property
    def scheduler(self):
        return self.app.scheduler

    @property
    def session(self):
        return self.app.web_session

    def alive(self, case: Case) -> bool:
        return self.store.get(case.id) is case

    async def notify(self, text: str, **kwargs) -> None:
        await self.app.bot.send_message(chat_id=get_config().telegram_chat_id, text=text, **kwargs)

    def take_token(self, data: dict) -> bool:
        if self.launch_token is None or data.get("token") != self.launch_token:
            return False
        self.launch_token = None
        return True

    def expired(self, case: Case) -> bool:
        return (case.completed_at is not None
                and self.clock() >= datetime.fromisoformat(case.completed_at) + KEEP_COMPLETED)

    def purge_completed(self) -> None:
        for case in self.store.open_cases():
            if case.id not in self.document_busy and self.expired(case):
                self.store.remove(case.id)

    def cancel_prompts(self, case: Case) -> None:
        self.scheduler.cancel(PROMPT_KIND, lambda task: task.payload.get("case") == case.id)

    @staticmethod
    def prompt_deadline(case: Case):
        # Without authorization the congress may already be over; the absence is asked until answered.
        return None if case.no_auth else dates.prompt_deadline(case.start_date)

    def _schedule_prompt_at(self, case: Case, when) -> None:
        deadline = self.prompt_deadline(case)
        self.scheduler.schedule(PROMPT_KIND, min(when, deadline) if deadline else when, {"case": case.id})

    def schedule_prompt(self, case: Case, now) -> None:
        config = self.config
        if case.no_auth:
            when = dates.next_slot(now, config.prompt)
        else:
            when = dates.first_prompt(case.start_date, config.days_before, config.prompt, now)
        self._schedule_prompt_at(case, when)

    def schedule_reminder(self, case: Case) -> None:
        config = self.config
        self._schedule_prompt_at(case, dates.next_reminder(self.clock(), config.prompt))

    # Mini App ----------------------------------------------------------------

    def case_view(self, case: Case) -> dict:
        problems = [text for text in (case.absence_problem, case.last_problem) if text]
        return {"id": case.id, "start": case.start, "end": case.end, "status": describe(case),
                "completed": case.completed_at is not None, "problem": '\n'.join(problems) or None,
                "steps": presentation.steps(case, self.clock().date(), busy=case.id in self.document_busy)}

    async def open_app(self, update, context) -> None:
        if not update.message:
            return
        self.purge_completed()
        today = self.clock().date()
        self.launch_token = uuid4().hex
        payload = {
            "token": self.launch_token,
            "today": today.isoformat(),
            "minStart": dates.earliest_start(today).isoformat(),
            "minStartNoAuth": dates.earliest_start_without_auth(today).isoformat(),
            "cases": [self.case_view(case) for case in self.store.open_cases()],
        }
        url = _build_vacations_url(self.config.webapp_url, payload)
        keyboard = ReplyKeyboardMarkup([[KeyboardButton("Congreso y dieta", web_app=WebAppInfo(url=url))]],
                                       resize_keyboard=True)
        await update.message.reply_text("Elige las fechas del congreso o gestiona los trámites en curso.",
                                        reply_markup=keyboard)

    def check_range(self, start: date, end: date, today: date, *, no_auth: bool = False) -> Optional[str]:
        earliest = dates.earliest_start_without_auth(today) if no_auth else dates.earliest_start(today)
        if start < earliest:
            return f"El congreso debe empezar como pronto el {earliest:%d/%m/%Y}."
        if end < start:
            return "La fecha de fin no puede ser anterior a la de inicio."
        if start.year != end.year:
            return "El congreso no puede cruzar el cambio de año."
        if self.store.overlaps(start, end):
            return "Las fechas se solapan con un trámite en curso."
        return None

    async def handle_new(self, update, context, data: dict) -> None:
        message = update.effective_message
        if not self.take_token(data):
            await message.reply_text("Esta selección ya no es válida. Abre /congreso_dieta de nuevo.")
            return
        try:
            start, end = date.fromisoformat(data.get("start")), date.fromisoformat(data.get("end"))
        except (TypeError, ValueError):
            await message.reply_text("Las fechas no son válidas. Abre /congreso_dieta de nuevo.")
            return
        no_auth = data.get("noAuth") is True
        problem = self.check_range(start, end, self.clock().date(), no_auth=no_auth)
        if problem:
            await message.reply_text(problem)
            return
        if no_auth:
            case = Case.new(start, end, no_auth=True)
            self.store.add(case)
            self.schedule_prompt(case, self.clock())
            await message.reply_text(f"✅ Trámite sin autorización de congreso creado para el {span(case)}. No se "
                                     "envía nada a USC; te preguntaré por la ausencia y el documento de dieta se "
                                     "generará al terminar el congreso.")
            return
        if self.app.bot_data.get(ACTIVE_KEY) or self.app.bot_data.get(STOPPING_KEY):
            await message.reply_text("Hay otra solicitud en curso. Espera a que termine.")
            return
        global_config = get_config()
        pending = None
        if global_config.congress_confirmation_enabled:
            pending = PendingCongress(self.app, chat_id=update.effective_chat.id, user_id=update.effective_user.id,
                                      timeout=global_config.congress_confirmation_timeout_seconds)
            self.app.bot_data[ACTIVE_KEY] = pending
        task = self.app.create_task(self._submit_congress(message, pending, start, end), update=update)
        if pending:
            pending.task = task

    async def _submit_congress(self, message, pending, start: date, end: date) -> None:
        status = None
        try:
            status = await message.reply_text("🔄 Preparando la solicitud de congreso en USC…")
            request = {**self.config.congress, "start_date": start.isoformat(), "end_date": end.isoformat()}
            try:
                if pending:
                    await pending.run(self.session.submit_congress_request, request)
                else:
                    await asyncio.to_thread(self.session.submit_congress_request, request)
                simulated = False
            except ReadOnlyStop:
                simulated = True
            case = Case.new(start, end, simulated=simulated)
            self.store.add(case)
            self.schedule_prompt(case, self.clock())
            if simulated:
                text = ("🧪 Modo de solo lectura: la solicitud de congreso llegó al paso final, pero no se envió "
                        f"a USC. Trámite simulado creado para el {span(case)}.")
            else:
                # TODO(request id): store the id returned by the submission once the user provides that step.
                text = (f"✅ Solicitud de congreso enviada para el {span(case)}. USC aún no confirma el envío: "
                        f"revísala en {usc.REQUESTS_LIST_URL}")
            await status.edit_text(text)
        except CongressRequestCancelled as exc:
            if status:
                await status.edit_text(pending.reason if pending and pending.decision.done() else str(exc))
        except CongressRequestError as exc:
            logger.warning("Congress request not submitted: %s", exc, exc_info=exc)
            if status:
                await status.edit_text(f"❌ {exc}")
        except Exception:
            logger.exception("Could not submit the congress request")
            if status:
                await status.edit_text("❌ No se pudo completar la solicitud de congreso. Comprueba USC antes "
                                       f"de repetirla: {usc.REQUESTS_LIST_URL}")
        finally:
            if pending:
                pending.abort("Solicitud cancelada. No se envió a USC.")
                await pending.finish()

    async def handle_cancel(self, update, context, data: dict) -> None:
        message = update.effective_message
        if not self.take_token(data):
            await message.reply_text("Esta selección ya no es válida. Abre /congreso_dieta de nuevo.")
            return
        case = self.store.get(data.get("case")) if isinstance(data.get("case"), str) else None
        if case is None:
            await message.reply_text("Ese trámite ya no existe.")
            return
        token = uuid4().hex
        self.cancel_requests[token] = case.id
        if case.completed_at:
            yes, text = "Sí, quitar", f"¿Quitar de la lista el trámite finalizado del {span(case)}?"
        else:
            yes = "Sí, cancelar"
            text = f"¿Cancelar el trámite del {span(case)}? Se dejará de seguir; lo ya enviado a USC no se anula."
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(yes, callback_data=f"cdieta_cancel_yes:{token}")],
            [InlineKeyboardButton("No", callback_data=f"cdieta_cancel_no:{token}")],
        ])
        await message.reply_text(text, reply_markup=buttons)

    # Callbacks -----------------------------------------------------------------

    def _action_allowed(self, case: Case, action: str) -> bool:
        if case.id in self.document_busy or self.app.bot_data.get(STOPPING_KEY):
            return False
        if action == 'absence':
            return presentation.can_request_absence(case) and not self.app.bot_data.get(ACTIVE_KEY)
        if case.absence == Absence.REQUESTING:
            return False
        if action == 'resign':
            return presentation.can_resign(case) and not self.expired(case)
        return action == 'sign' and presentation.can_sign(case, self.clock().date())

    @staticmethod
    def action_state(case: Case, action: str):
        """The state a confirmation was offered for; any change invalidates it."""
        if action == 'absence':
            return case.absence
        return case.completed_at if action == 'resign' else case.stage

    async def handle_action(self, update, context, data: dict) -> None:
        message = update.effective_message
        if not self.take_token(data):
            await message.reply_text("Esta selección ya no es válida. Abre /congreso_dieta de nuevo.")
            return
        case = self.store.get(data.get('case')) if isinstance(data.get('case'), str) else None
        action = data.get('action')
        if case is None or not self._action_allowed(case, action):
            await message.reply_text("Esta acción no está disponible ahora. Abre /congreso_dieta para ver el estado actual.")
            return
        token = uuid4().hex
        state = self.action_state(case, action)
        self.action_requests[token] = (case.id, action, state)
        if action == 'absence':
            text = f"¿Solicitar ahora la ausencia del {span(case)}?"
            if case.absence == Absence.UNCERTAIN:
                text += (" El envío anterior quedó sin confirmar. Comprueba antes USC: si ya aparece presentada, "
                         f"no continúes porque crearías una solicitud duplicada. {ABSENCES_URL}")
        elif action == 'resign':
            text = (f"¿Repetir la firma de la dieta del {span(case)}? Se firmará de nuevo el documento ya generado, "
                    "se reemplazará el PDF guardado y se enviará otra vez.")
        else:
            text = f"¿Generar y firmar ahora la dieta del {span(case)}, sin esperar a la ejecución diaria?"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton('Sí, continuar', callback_data=f'cdieta_action_yes:{token}')],
            [InlineKeyboardButton('No', callback_data=f'cdieta_action_no:{token}')],
        ])
        await message.reply_text(text, reply_markup=buttons)

    async def _answer_action(self, update, query, action: str, token: str) -> None:
        request = self.action_requests.pop(token, None)
        case = self.store.get(request[0]) if request else None
        if case is None:
            await query.answer("Esta confirmación ya no está disponible.")
            return
        if action == 'cdieta_action_no':
            await query.answer('Acción cancelada.')
            await query.edit_message_text('No se ha realizado ninguna acción.')
            return
        _, operation, expected_state = request
        state = self.action_state(case, operation)
        if state != expected_state or not self._action_allowed(case, operation):
            await query.answer('El estado cambió. Abre /congreso_dieta de nuevo.')
            return
        self.action_requests = {key: value for key, value in self.action_requests.items() if value[0] != case.id}
        if operation == 'absence':
            self.cancel_prompts(case)
            case.absence, case.absence_problem = Absence.REQUESTING, None
            self.store.save()
            task = self._request_absence(case, update.effective_chat.id, update.effective_user.id)
        else:
            if operation == 'resign':
                # Back to the signing step: the generated document is signed and delivered again.
                case.stage, case.document_delivered, case.completed_at = Stage.GENERATED, False, None
                self.store.save()
            self.document_busy.add(case.id)
            task = self._run_document(case, self.clock().date(), force=True)
        # Start before awaiting Telegram so delivery failures cannot leave an unstarted operation.
        self.app.create_task(task, update=update)
        await query.answer('Solicitud iniciada.' if operation == 'absence' else 'Preparando la dieta…')
        await query.edit_message_text('Solicitando la ausencia…' if operation == 'absence' else 'Generando y firmando la dieta…')

    async def handle_callback(self, update, context) -> None:
        query = update.callback_query
        action, token = query.data.split(":", 1)
        if action in ('cdieta_action_yes', 'cdieta_action_no'):
            await self._answer_action(update, query, action, token)
            return
        if action in ("cdieta_cancel_yes", "cdieta_cancel_no"):
            await self._answer_cancel(query, action, token)
            return
        await self._answer_absence(update, query, action, token)

    async def _answer_cancel(self, query, action: str, token: str) -> None:
        case_id = self.cancel_requests.pop(token, None)
        case = self.store.get(case_id) if case_id else None
        if case is None:
            await query.answer("Esta cancelación ya no está disponible.")
            return
        if action == "cdieta_cancel_no":
            await query.answer("Se mantiene el trámite.")
            await query.edit_message_text(f"El trámite del {span(case)} sigue en curso.")
            return
        if case.id in self.document_busy or case.absence == Absence.REQUESTING:
            await query.answer('Hay una operación en curso. Espera a que termine antes de cancelar.')
            return
        self.cancel_prompts(case)
        self.store.remove(case.id)
        if case.completed_at:
            await query.answer("Trámite quitado.")
            await query.edit_message_text(f"🗑️ Trámite finalizado del {span(case)} quitado de la lista.")
            return
        sent = []
        if not case.simulated and not case.no_auth:
            sent.append(f"la solicitud de congreso ({usc.REQUESTS_LIST_URL})")
        if case.absence in (Absence.REQUESTED, Absence.UNCERTAIN):
            sent.append(f"la ausencia ({ABSENCES_URL})")
        text = f"🗑️ Trámite del {span(case)} cancelado."
        if sent:
            text += " Ya se había enviado a USC " + " y ".join(sent) + "; anúlalo allí si es necesario."
        await query.answer("Trámite cancelado.")
        await query.edit_message_text(text)

    async def _answer_absence(self, update, query, action: str, token: str) -> None:
        case = self.store.by_prompt_token(token)
        if case is None or case.absence != Absence.ASKING:
            await query.answer("Esta pregunta ya no está disponible.")
            return
        self.cancel_prompts(case)
        if action == "cdieta_absence_no":
            case.absence, case.absence_problem = Absence.SKIPPED, None
            self.store.save()
            await query.answer("No se solicitará la ausencia.")
            return
        case.absence, case.absence_problem = Absence.REQUESTING, None
        self.store.save()
        await query.answer("Solicitando la ausencia…")
        self.app.create_task(
            self._request_absence(case, update.effective_chat.id, update.effective_user.id), update=update)

    async def _ask_again(self, case: Case, text: str) -> None:
        if self.alive(case):
            case.absence = Absence.ASKING
            case.absence_problem = text
            self.store.save()
            self.schedule_reminder(case)
        await self.notify(text)

    async def _request_absence(self, case: Case, chat_id: int, user_id: int) -> None:
        config = self.config
        try:
            catalog = await asyncio.to_thread(self.session.fetch_absence_selection_data)
            kind = find_absence_type(catalog, config.absence.type.display_name)
            if kind is None:
                raise AbsenceRequestError(f"USC no ofrece el tipo de ausencia «{config.absence.type.display_name}».")
            entries = await asyncio.to_thread(self.session.fetch_calendar_summary)
        except Exception as exc:  # noqa: BLE001 - nothing was sent yet; asking again is safe
            logger.exception("Could not prepare the congress absence request")
            await self._ask_again(case, f"❌ No se pudo preparar la ausencia: {exc} Te lo volveré a preguntar.")
            return
        if not self.alive(case):
            return
        days = dates.absence_days(case.start_date, case.end_date, dates.parse_non_working(entries))
        if not days:
            case.absence, case.absence_problem = Absence.NOT_REQUIRED, None
            self.store.save()
            await self.notify(f"ℹ️ Del {span(case)} no hay días laborables: no hace falta solicitar ausencia.")
            return
        request = build_absence_request(case, kind, days, config.absence)
        global_config = get_config()
        try:
            if global_config.absence_confirmation_enabled:
                await self._submit_absence_confirmed(request, chat_id, user_id,
                                                     global_config.absence_confirmation_timeout_seconds)
            else:
                await asyncio.to_thread(self.session.submit_absence_request, request)
        except ReadOnlyStop:
            outcome, text = Absence.SIMULATED, "🧪 Modo de solo lectura: la ausencia llegó al paso final, pero no se envió a USC."
        except AbsenceRequestCancelled as exc:
            logger.info("Congress absence request cancelled: %s", exc)
            await self._ask_again(case, f"{exc} Te lo volveré a preguntar.")
            return
        except AbsenceRequestError as exc:
            logger.warning("Congress absence request rejected: %s", exc, exc_info=exc)
            await self._ask_again(case, f"❌ {exc} Te lo volveré a preguntar.")
            return
        except AbsenceRequestUncertain as exc:
            logger.warning("Could not verify congress absence submission", exc_info=True)
            outcome, text = Absence.UNCERTAIN, f"⚠️ {exc} {ABSENCES_URL}"
        except Exception:
            logger.exception("Could not submit the congress absence request")
            outcome, text = Absence.UNCERTAIN, f"⚠️ No se pudo confirmar la ausencia. Comprueba USC antes de repetirla: {ABSENCES_URL}"
        else:
            outcome, text = Absence.REQUESTED, f"✅ Ausencia solicitada del {span(case)}."
        if self.alive(case):
            case.absence = outcome
            case.absence_problem = None if outcome == Absence.REQUESTED else text
            self.store.save()
        await self.notify(text)

    async def _submit_absence_confirmed(self, request: dict, chat_id: int, user_id: int, timeout: int) -> None:
        if self.app.bot_data.get(ACTIVE_KEY) or self.app.bot_data.get(STOPPING_KEY):
            raise AbsenceRequestCancelled("Hay otra solicitud en curso.")
        pending = PendingPluginAbsence(self.app, chat_id=chat_id, user_id=user_id, timeout=timeout)
        self.app.bot_data[ACTIVE_KEY] = pending
        pending.task = asyncio.current_task()
        try:
            await pending.run(self.session.submit_absence_request, request)
        finally:
            pending.abort("Solicitud cancelada. No se envió a USC.")
            await pending.finish()

    async def run_absence_prompt(self, context, task) -> None:
        case = self.store.get(task.payload.get("case"))
        if case is None or case.absence not in (Absence.SCHEDULED, Absence.ASKING):
            return
        config = self.config
        now = self.clock()
        deadline = self.prompt_deadline(case)
        if deadline and now >= deadline:
            case.absence = Absence.NOT_REQUESTED
            self.store.save()
            await self.notify(f"ℹ️ No se solicitó la ausencia del {span(case)}: el congreso ya ha empezado.")
            return
        slot = dates.next_slot(now, config.prompt)
        if slot > now:
            self._schedule_prompt_at(case, slot)
            return
        first = case.absence == Absence.SCHEDULED
        case.prompt_token = case.prompt_token or uuid4().hex
        case.absence = Absence.ASKING
        self.store.save()
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("Solicitar ausencia", callback_data=f"cdieta_absence_yes:{case.prompt_token}"),
            InlineKeyboardButton("Ahora no", callback_data=f"cdieta_absence_no:{case.prompt_token}"),
        ]])
        question = "📋 ¿Solicito la ausencia" if first else "⏰ Recordatorio: ¿solicito la ausencia"
        await self.notify(f"{question} del {span(case)} para el congreso?", reply_markup=buttons)
        self.schedule_reminder(case)

    # Daily run -----------------------------------------------------------------

    async def run_daily(self, context) -> None:
        self.purge_completed()
        today = self.clock().date()
        for case in list(self.store.open_cases()):
            if case.id not in self.document_busy and not case.completed_at:
                self.document_busy.add(case.id)
                await self._run_document(case, today)

    async def _run_document(self, case: Case, today: date, *, force: bool = False) -> None:
        try:
            if self.alive(case) and (not force or presentation.can_sign(case, today)):
                await self._advance(case, today, force=force)
        except Exception:  # noqa: BLE001 - retain the case for the next daily or manual attempt
            logger.exception("Unexpected error advancing congress case %s", case.id)
            await self._problem(case, "Error inesperado; se reintentará mañana.")
        finally:
            self.document_busy.discard(case.id)

    async def _problem(self, case: Case, text: str) -> None:
        if self.alive(case):
            case.last_problem = text
            self.store.save()
        await self.notify(f"⚠️ {text}")

    async def _advance(self, case: Case, today: date, *, force: bool = False) -> None:
        # A past congress must not be delivered (and forgotten) while its absence question is still open.
        if case.stage == Stage.NO_AUTH and today > case.end_date and (case.absence.settled or force):
            await self._generate(case, today)
        if case.stage == Stage.AWAITING_AUTH:
            if case.simulated or not case.request_id:
                return  # TODO(request id): see "Known gaps" in the spec
            await self._check_authorization(case, today)
        if case.stage == Stage.AUTH_RECEIVED and today >= dates.generation_day(
                case.end_date, date.fromisoformat(case.auth_date)):
            await self._generate(case, today)
        if case.stage == Stage.GENERATED:
            await self._sign(case)
        if case.stage == Stage.SIGNED:
            await self._deliver(case)

    async def _check_authorization(self, case: Case, today: date) -> None:
        try:
            status = await asyncio.to_thread(self.session.run, usc.fetch_request_status, case.request_id)
            document = None
            if status.signed:
                document = await asyncio.to_thread(self.session.run, usc.download_authorization,
                                                   status.authorization_url)
        except Exception:  # noqa: BLE001 - read-only check; retried tomorrow
            logger.exception("Could not check the congress authorization for case %s", case.id)
            case.check_failures += 1
            case.last_problem = "No se pudo consultar la autorización en USC."
            self.store.save()
            if case.check_failures == FAILURES_BEFORE_NOTICE:
                await self.notify(f"⚠️ No se pudo consultar la autorización del congreso del {span(case)} durante "
                                  f"{FAILURES_BEFORE_NOTICE} días seguidos. Seguiré intentándolo.")
            return
        case.check_failures = 0
        if status.rejected:
            first_notice = case.notified_state != status.state
            case.notified_state = status.state
            case.last_problem = f"USC: {status.state}"
            self.store.save()
            if first_notice:
                await self.notify(f"⚠️ La solicitud de congreso del {span(case)} está en estado «{status.state}». "
                                  "Cancela el trámite con /congreso_dieta si ya no sigue adelante.")
            return
        if document is None:
            case.last_problem, case.notified_state = None, None
            self.store.save()
            return
        (self.store.directory(case) / "autorizacion.pdf").write_bytes(document)
        case.stage, case.auth_date, case.last_problem = Stage.AUTH_RECEIVED, today.isoformat(), None
        self.store.save()
        await self.notify(f"📄 Autorización firmada recibida para el congreso del {span(case)}.")

    async def _generate(self, case: Case, today: date) -> None:
        config = self.config
        folder = self.store.directory(case)
        workdir = folder / "hoja"
        shutil.rmtree(workdir, ignore_errors=True)
        sheet_dates = spreadsheet.SheetDates(case.start_date, case.end_date, today)
        try:
            async with self.generation_lock:
                sheet = await asyncio.to_thread(self._generate_pdf, config.spreadsheet_template, workdir, sheet_dates)
            if case.no_auth:
                await asyncio.to_thread(shutil.copyfile, sheet, folder / "unido.pdf")
            else:
                await asyncio.to_thread(self._join_pdfs, sheet, folder / "autorizacion.pdf", folder / "unido.pdf")
        except (spreadsheet.SpreadsheetError, pdf.PdfError, OSError) as exc:
            logger.exception("Could not generate document for congress case %s", case.id)
            await self._problem(case, f"Error al generar el documento: {exc} Se reintentará mañana.")
            return
        case.stage, case.last_problem = Stage.GENERATED, None
        self.store.save()

    async def _sign(self, case: Case) -> None:
        config = self.config
        folder = self.store.directory(case)
        try:
            await asyncio.to_thread(self._sign_pdf, folder / "unido.pdf", folder / "firmado.pdf", config.signing)
        except pdf.PdfError as exc:
            logger.exception("Could not sign document for congress case %s", case.id)
            if not case.unsigned_saved:
                try:
                    shutil.copyfile(folder / "unido.pdf", config.output_dir / f"{document_name(case)}_SIN_FIRMAR.pdf")
                    case.unsigned_saved = True
                except OSError:
                    logger.exception("Could not save the unsigned document")
            await self._problem(case, f"Error al firmar: {exc} El documento sin firmar está en "
                                      f"{config.output_dir}. Se reintentará mañana.")
            return
        case.stage, case.last_problem = Stage.SIGNED, None
        self.store.save()

    def _complete(self, case: Case) -> None:
        case.completed_at = self.clock().isoformat()
        self.store.save()

    async def _deliver(self, case: Case) -> None:
        if case.document_delivered:
            if case.absence.settled:
                self._complete(case)
            return
        config = self.config
        signed = self.store.directory(case) / "firmado.pdf"
        target = config.output_dir / f"{document_name(case)}.pdf"
        try:
            shutil.copyfile(signed, target)
            with signed.open("rb") as handle:
                await self.app.bot.send_document(
                    chat_id=get_config().telegram_chat_id, document=handle, filename=target.name,
                    caption=f"✅ Documento de dieta firmado del congreso del {span(case)}. Guardado en {target}.")
        except Exception as exc:  # noqa: BLE001 - delivery is retried tomorrow
            logger.exception("Could not deliver congress case %s", case.id)
            await self._problem(case, f"No se pudo entregar el documento firmado: {exc} Se reintentará mañana.")
            return
        (config.output_dir / f"{document_name(case)}_SIN_FIRMAR.pdf").unlink(missing_ok=True)
        case.document_delivered, case.last_problem = True, None
        self.store.save()
        if case.absence.settled:
            self._complete(case)
