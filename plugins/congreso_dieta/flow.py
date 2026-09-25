"""congreso_dieta workflow: Mini App actions, absence prompt and the daily document run."""
from __future__ import annotations

import asyncio
import shutil
from datetime import date
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
from plugins.congreso_dieta import dates, pdf, spreadsheet, usc
from plugins.congreso_dieta.cases import Case, CaseStore
from plugins.congreso_dieta.config import PluginConfig, parse_config

logger = get_logger(__name__)

PROMPT_KIND = "congreso_dieta.absence_prompt"
DAILY_JOB = "congreso_dieta.daily"
CALLBACK_PATTERN = r"^cdieta_(?:absence_yes|absence_no|cancel_yes|cancel_no):[0-9a-f]{32}$"
ABSENCES_URL = "https://fichaxe.usc.gal/pas/solicitudesPropias"
FAILURES_BEFORE_NOTICE = 3

STAGE_LABELS = {
    "awaiting_auth": "Esperando la autorización firmada",
    "auth_received": "Autorización recibida",
    "generated": "Documento generado; pendiente de firma",
    "signed": "Firmado; pendiente de entrega",
}
ABSENCE_LABELS = {
    "scheduled": "ausencia pendiente de preguntar",
    "asking": "ausencia pendiente de tu respuesta",
    "requesting": "solicitando la ausencia",
    "requested": "ausencia solicitada",
    "uncertain": "ausencia sin confirmar (revisa USC)",
    "skipped": "ausencia no solicitada",
    "simulated": "ausencia simulada",
    "not_requested": "ausencia no solicitada",
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
    text = f"{STAGE_LABELS[case.stage]} · {ABSENCE_LABELS[case.absence]}"
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
    def __init__(self, application, config: PluginConfig, raw_config: dict, store: CaseStore, *,
                 clock=get_madrid_now, generate=spreadsheet.generate_pdf, join=pdf.join_pdfs,
                 sign=pdf.sign_pdf) -> None:
        self.app = application
        self.config = config
        self.raw_config = raw_config
        self.store = store
        self.clock = clock
        self._generate_pdf = generate
        self._join_pdfs = join
        self._sign_pdf = sign
        self.launch_token: Optional[str] = None
        self.cancel_requests: dict[str, str] = {}
        self.generation_lock = asyncio.Lock()

    # Helpers -----------------------------------------------------------------

    @property
    def scheduler(self):
        return self.app.scheduler

    @property
    def session(self):
        return self.app.web_session

    def case_config(self, case: Case) -> PluginConfig:
        return parse_config(case.config, today=self.clock().date(), check_files=False)

    def alive(self, case: Case) -> bool:
        return self.store.get(case.id) is case

    async def notify(self, text: str, **kwargs) -> None:
        await self.app.bot.send_message(chat_id=get_config().telegram_chat_id, text=text, **kwargs)

    def take_token(self, data: dict) -> bool:
        if self.launch_token is None or data.get("token") != self.launch_token:
            return False
        self.launch_token = None
        return True

    def cancel_prompts(self, case: Case) -> None:
        self.scheduler.cancel(PROMPT_KIND, lambda task: task.payload.get("case") == case.id)

    def schedule_prompt(self, case: Case, now) -> None:
        config = self.case_config(case)
        when = dates.first_prompt(case.start_date, config.days_before, config.prompt, now)
        self.scheduler.schedule(PROMPT_KIND, min(when, dates.prompt_deadline(case.start_date)), {"case": case.id})

    def schedule_reminder(self, case: Case) -> None:
        config = self.case_config(case)
        when = min(dates.next_reminder(self.clock(), config.prompt), dates.prompt_deadline(case.start_date))
        self.scheduler.schedule(PROMPT_KIND, when, {"case": case.id})

    # Mini App ----------------------------------------------------------------

    async def open_app(self, update, context) -> None:
        if not update.message:
            return
        today = self.clock().date()
        self.launch_token = uuid4().hex
        payload = {
            "token": self.launch_token,
            "today": today.isoformat(),
            "minStart": dates.earliest_start(today).isoformat(),
            "cases": [{"id": case.id, "start": case.start, "end": case.end, "status": describe(case),
                       "problem": case.last_problem} for case in self.store.open_cases()],
        }
        url = _build_vacations_url(self.config.webapp_url, payload)
        keyboard = ReplyKeyboardMarkup([[KeyboardButton("Congreso y dieta", web_app=WebAppInfo(url=url))]],
                                       resize_keyboard=True)
        await update.message.reply_text("Elige las fechas del congreso o gestiona los trámites en curso.",
                                        reply_markup=keyboard)

    def check_range(self, start: date, end: date, today: date) -> Optional[str]:
        earliest = dates.earliest_start(today)
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
        problem = self.check_range(start, end, self.clock().date())
        if problem:
            await message.reply_text(problem)
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
            case = Case.new(start, end, self.raw_config, simulated=simulated)
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
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("Sí, cancelar", callback_data=f"cdieta_cancel_yes:{token}")],
            [InlineKeyboardButton("No", callback_data=f"cdieta_cancel_no:{token}")],
        ])
        await message.reply_text(f"¿Cancelar el trámite del {span(case)}? Se dejará de seguir; lo ya enviado a USC "
                                  "no se anula.", reply_markup=buttons)

    # Callbacks -----------------------------------------------------------------

    async def handle_callback(self, update, context) -> None:
        query = update.callback_query
        action, token = query.data.split(":", 1)
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
        self.cancel_prompts(case)
        self.store.remove(case.id)
        sent = []
        if not case.simulated:
            sent.append(f"la solicitud de congreso ({usc.REQUESTS_LIST_URL})")
        if case.absence in ("requested", "uncertain"):
            sent.append(f"la ausencia ({ABSENCES_URL})")
        text = f"🗑️ Trámite del {span(case)} cancelado."
        if sent:
            text += " Ya se había enviado a USC " + " y ".join(sent) + "; anúlalo allí si es necesario."
        await query.answer("Trámite cancelado.")
        await query.edit_message_text(text)

    async def _answer_absence(self, update, query, action: str, token: str) -> None:
        case = self.store.by_prompt_token(token)
        if case is None or case.absence != "asking":
            await query.answer("Esta pregunta ya no está disponible.")
            return
        self.cancel_prompts(case)
        if action == "cdieta_absence_no":
            case.absence = "skipped"
            self.store.save()
            await query.answer("No se solicitará la ausencia.")
            return
        case.absence = "requesting"
        self.store.save()
        await query.answer("Solicitando la ausencia…")
        self.app.create_task(
            self._request_absence(case, update.effective_chat.id, update.effective_user.id), update=update)

    async def _ask_again(self, case: Case, text: str) -> None:
        if self.alive(case):
            case.absence = "asking"
            self.store.save()
            self.schedule_reminder(case)
        await self.notify(text)

    async def _request_absence(self, case: Case, chat_id: int, user_id: int) -> None:
        config = self.case_config(case)
        try:
            catalog = await asyncio.to_thread(self.session.fetch_absence_selection_data)
            kind = find_absence_type(catalog, config.absence.type_name)
            if kind is None:
                raise AbsenceRequestError(f"USC no ofrece el tipo de ausencia «{config.absence.type_name}».")
            entries = await asyncio.to_thread(self.session.fetch_calendar_summary)
        except Exception as exc:  # noqa: BLE001 - nothing was sent yet; asking again is safe
            logger.exception("Could not prepare the congress absence request")
            await self._ask_again(case, f"❌ No se pudo preparar la ausencia: {exc} Te lo volveré a preguntar.")
            return
        if not self.alive(case):
            return
        days = dates.absence_days(case.start_date, case.end_date, dates.parse_non_working(entries))
        if not days:
            case.absence = "not_requested"
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
            outcome, text = "simulated", "🧪 Modo de solo lectura: la ausencia llegó al paso final, pero no se envió a USC."
        except AbsenceRequestCancelled as exc:
            logger.info("Congress absence request cancelled: %s", exc)
            await self._ask_again(case, f"{exc} Te lo volveré a preguntar.")
            return
        except AbsenceRequestError as exc:
            logger.warning("Congress absence request rejected: %s", exc, exc_info=exc)
            await self._ask_again(case, f"❌ {exc} Te lo volveré a preguntar.")
            return
        except AbsenceRequestUncertain as exc:
            outcome, text = "uncertain", f"⚠️ {exc} {ABSENCES_URL}"
        except Exception:
            logger.exception("Could not submit the congress absence request")
            outcome, text = "uncertain", f"⚠️ No se pudo confirmar la ausencia. Comprueba USC antes de repetirla: {ABSENCES_URL}"
        else:
            outcome, text = "requested", f"✅ Ausencia solicitada del {span(case)}."
        if self.alive(case):
            case.absence = outcome
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
        if case is None or case.absence not in ("scheduled", "asking"):
            return
        config = self.case_config(case)
        now = self.clock()
        deadline = dates.prompt_deadline(case.start_date)
        if now >= deadline:
            case.absence = "not_requested"
            self.store.save()
            await self.notify(f"ℹ️ No se solicitó la ausencia del {span(case)}: el congreso ya ha empezado.")
            return
        slot = dates.next_slot(now, config.prompt)
        if slot > now:
            self.scheduler.schedule(PROMPT_KIND, min(slot, deadline), {"case": case.id})
            return
        first = case.absence == "scheduled"
        case.prompt_token = case.prompt_token or uuid4().hex
        case.absence = "asking"
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
        today = self.clock().date()
        for case in list(self.store.open_cases()):
            try:
                await self._advance(case, today)
            except Exception:  # noqa: BLE001 - one broken case must not block the others
                logger.exception("Unexpected error advancing congress case %s", case.id)
                await self._problem(case, "Error inesperado; se reintentará mañana.")

    async def _problem(self, case: Case, text: str) -> None:
        if self.alive(case):
            case.last_problem = text
            self.store.save()
        await self.notify(f"⚠️ {text}")

    async def _advance(self, case: Case, today: date) -> None:
        if case.stage == "awaiting_auth":
            if case.simulated or not case.request_id:
                return  # TODO(request id): see "Known gaps" in the spec
            await self._check_authorization(case, today)
        if case.stage == "auth_received" and today >= dates.generation_day(
                case.end_date, date.fromisoformat(case.auth_date)):
            await self._generate(case, today)
        if case.stage == "generated":
            await self._sign(case)
        if case.stage == "signed":
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
            self.store.save()
            return
        (self.store.directory(case) / "autorizacion.pdf").write_bytes(document)
        case.stage, case.auth_date, case.last_problem = "auth_received", today.isoformat(), None
        self.store.save()
        await self.notify(f"📄 Autorización firmada recibida para el congreso del {span(case)}.")

    async def _generate(self, case: Case, today: date) -> None:
        config = self.case_config(case)
        folder = self.store.directory(case)
        workdir = folder / "hoja"
        shutil.rmtree(workdir, ignore_errors=True)
        sheet_dates = spreadsheet.SheetDates(case.start_date, case.end_date, today)
        try:
            async with self.generation_lock:
                sheet = await asyncio.to_thread(self._generate_pdf, config.spreadsheet_template, workdir, sheet_dates)
            await asyncio.to_thread(self._join_pdfs, sheet, folder / "autorizacion.pdf", folder / "unido.pdf")
        except (spreadsheet.SpreadsheetError, pdf.PdfError, OSError) as exc:
            await self._problem(case, f"Error al generar el documento: {exc} Se reintentará mañana.")
            return
        case.stage, case.last_problem = "generated", None
        self.store.save()

    async def _sign(self, case: Case) -> None:
        config = self.case_config(case)
        folder = self.store.directory(case)
        try:
            await asyncio.to_thread(self._sign_pdf, folder / "unido.pdf", folder / "firmado.pdf", config.signing)
        except pdf.PdfError as exc:
            if not case.unsigned_saved:
                try:
                    shutil.copyfile(folder / "unido.pdf", config.output_dir / f"{document_name(case)}_SIN_FIRMAR.pdf")
                    case.unsigned_saved = True
                except OSError:
                    logger.exception("Could not save the unsigned document")
            await self._problem(case, f"Error al firmar: {exc} El documento sin firmar está en "
                                      f"{config.output_dir}. Se reintentará mañana.")
            return
        case.stage, case.last_problem = "signed", None
        self.store.save()

    async def _deliver(self, case: Case) -> None:
        config = self.case_config(case)
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
        self.store.remove(case.id)
