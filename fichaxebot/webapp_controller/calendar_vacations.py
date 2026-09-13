"""Handle the single final message sent by the vacation Mini App."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from fichaxebot.config import get_config

from fichaxebot.commands.vacations import VACATION_SELECTION_KEY
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.vacation_request import (
    VacationRequestError, VacationRequestUncertain, validate_selection,
)
from fichaxebot.utils import get_madrid_now

logger = get_logger(__name__)
VACATION_DRAFTS_KEY = "vacation_drafts"


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
    status = await msg.reply_text("🔄 Comprobando el saldo actual y preparando las fechas en USC...")
    try:
        result = await asyncio.to_thread(context.application.web_session.prepare_vacation_request, selection)
    except VacationRequestError as exc:
        await status.edit_text(f"❌ {exc} Abre /vacaciones para actualizar los datos.")
        return
    except VacationRequestUncertain as exc:
        await status.edit_text(f"⚠️ {exc} https://fichaxe.usc.gal/pas/solicitudesPropias")
        return
    except Exception:
        logger.exception("Could not prepare vacation request")
        await status.edit_text("❌ No se pudo confirmar el borrador. Revisa tus solicitudes en USC antes de repetirlo.")
        return
    context.user_data.setdefault(VACATION_DRAFTS_KEY, {})[result["id"]] = {"draft": result, "status": "pending"}
    dates = ", ".join(result["days"])
    await status.edit_text(
        f"📋 Borrador {result['id']} guardado en USC.\n"
        f"{result['vacationTypeName']} · Saldo {result['year']} · {len(result['days'])} días\n"
        f"Fechas: {dates}\nLa solicitud todavía no se ha enviado.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Revisar en USC", url=result["reviewUrl"])],
            [InlineKeyboardButton("Solicitar en USC", callback_data=f"vacation_submit:{result['id']}")],
        ]),
    )


async def confirm_vacation_request(update, context):
    query = update.callback_query
    if not query or not update.effective_chat or str(update.effective_chat.id) != str(get_config().telegram_chat_id):
        return
    identifier = (query.data or "").removeprefix("vacation_submit:")
    record = context.user_data.get(VACATION_DRAFTS_KEY, {}).get(identifier)
    if not record:
        await query.answer("Esta confirmación ha caducado. Revisa el borrador en USC.", show_alert=True)
        return
    if record["status"] != "pending":
        await query.answer("Este envío ya se ha procesado. Consulta el estado en USC.", show_alert=True)
        return
    record["status"] = "processing"
    await query.answer("Solicitando en USC...")
    draft = record["draft"]
    try:
        state = await asyncio.to_thread(context.application.web_session.submit_vacation_request, draft)
    except (VacationRequestError, VacationRequestUncertain) as exc:
        record["status"] = "check_usc"
        await query.edit_message_text(f"⚠️ {exc}\n{draft['reviewUrl']}")
        return
    except Exception:
        record["status"] = "check_usc"
        logger.exception("Could not confirm submitted vacation request")
        await query.edit_message_text(f"No se pudo confirmar el envío. Revisa la solicitud en USC antes de repetirlo.\n{draft['reviewUrl']}")
        return
    record["status"] = "done"
    await query.edit_message_text(
        f"Solicitud {draft['id']} · Estado en USC: {state}.\n"
        f"{draft['vacationTypeName']} · Saldo {draft['year']} · {len(draft['days'])} días\n"
        f"{draft['reviewUrl']}"
    )


async def handle_legacy_selection(update, context, data):
    await update.effective_message.reply_text("La selección ha cambiado. Abre /vacaciones de nuevo.")
