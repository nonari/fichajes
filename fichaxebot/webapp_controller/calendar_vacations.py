"""Handle the single final message sent by the vacation Mini App."""
import asyncio

from fichaxebot.commands.vacations import VACATION_SELECTION_KEY
from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.commit import ReadOnlyStop
from fichaxebot.scrap_functions.vacation_request import (
    VacationRequestCancelled, VacationRequestError, VacationRequestUncertain, validate_selection,
)
from fichaxebot.utils import get_madrid_now
from fichaxebot.webapp_controller.vacation_confirmation import ACTIVE_KEY, STOPPING_KEY, PendingVacation

logger = get_logger(__name__)


async def handle_vacation_request(update, context, data):
    msg = update.effective_message
    if context.application.bot_data.get(ACTIVE_KEY) or context.application.bot_data.get(STOPPING_KEY):
        await msg.reply_text("Hay una solicitud en curso o el bot se está apagando. Inténtalo más tarde.")
        return
    snapshot = context.user_data.get(VACATION_SELECTION_KEY)
    if not snapshot or data.get("requestId") != snapshot.get("requestId"):
        await msg.reply_text("Esta selección ha caducado. Abre /vacaciones de nuevo.")
        return
    try:
        selection = validate_selection(data, snapshot, snapshot["entries"], get_madrid_now().date())
    except VacationRequestError as exc:
        await msg.reply_text(str(exc))
        return
    # Consume the launch token before awaiting browser work to prevent duplicate processing.
    context.user_data.pop(VACATION_SELECTION_KEY, None)
    config = get_config()
    if config.vacation_confirmation_enabled:
        pending = PendingVacation(
            context.application, chat_id=update.effective_chat.id, user_id=update.effective_user.id,
            timeout=config.vacation_confirmation_timeout_seconds,
        )
        context.application.bot_data[ACTIVE_KEY] = pending
        pending.task = context.application.create_task(
            _run_confirmed_request(msg, context.application.web_session, selection, pending), update=update,
        )
        return
    status = await msg.reply_text("🔄 Comprobando el saldo y enviando la solicitud a USC...")
    await _submit_and_report(status, context.application.web_session, selection)


async def _run_confirmed_request(msg, session, selection, pending):
    try:
        status = await msg.reply_text("🔄 Preparando el resumen de USC para confirmar...")
        await _submit_and_report(status, session, selection, pending)
    finally:
        pending.abort('Solicitud cancelada. No se envió a USC.')
        await pending.finish()


async def _submit_and_report(status, session, selection, pending=None):
    try:
        if pending:
            result = await pending.run(session.submit_vacation_request, selection)
        else:
            result = await asyncio.to_thread(session.submit_vacation_request, selection)
    except VacationRequestCancelled as exc:
        await status.edit_text(pending.reason if pending else str(exc))
        return
    except ReadOnlyStop:
        await status.edit_text(
            "🧪 Modo de solo lectura: la solicitud llegó al paso final, pero no se envió a USC."
        )
        return
    except PermissionError as exc:
        await status.edit_text(f"❌ {exc}")
        return
    except VacationRequestError as exc:
        await status.edit_text(f"❌ {exc} Abre /vacaciones para actualizar los datos.")
        return
    except VacationRequestUncertain as exc:
        await status.edit_text(f"⚠️ {exc} https://fichaxe.usc.gal/pas/solicitudesPropias")
        return
    except Exception:
        logger.exception("Could not complete vacation request")
        await status.edit_text(
            "❌ No se pudo confirmar el envío. Comprueba tus solicitudes en USC antes de repetirlo. "
            "https://fichaxe.usc.gal/pas/solicitudesPropias"
        )
        return
    dates = ", ".join(result["days"])
    await status.edit_text(
        f"Solicitud {result['id']} · Estado en USC: {result['state']}.\n"
        f"{result['vacationTypeName']} · Saldo {result['year']} · {len(result['days'])} días\n"
        f"Fechas: {dates}"
    )
