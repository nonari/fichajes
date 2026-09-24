"""Handle the single final message sent by the vacation Mini App."""
import asyncio

from fichaxebot.commands.vacations import VACATION_SELECTION_KEY
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.vacation_request import (
    VacationRequestError, VacationRequestUncertain, validate_selection,
)
from fichaxebot.utils import get_madrid_now

logger = get_logger(__name__)


async def handle_vacation_request(update, context, data):
    msg = update.effective_message
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
    status = await msg.reply_text("🔄 Comprobando el saldo y enviando la solicitud a USC...")
    try:
        result = await asyncio.to_thread(context.application.web_session.submit_vacation_request, selection)
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
