import json
from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.config import get_config
from fichaxebot.webapp_controller.calendar_vacations import handle_legacy_selection, handle_vacation_request

WEBAPP_CONTROLLERS = {
    "vacation_request": handle_vacation_request,
    "calendar_selection": handle_legacy_selection,
    "calendar_final": handle_legacy_selection,
    "calendar_final_submit": handle_legacy_selection,
}


async def dispatch_webapp_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.web_app_data:
        return
    if not update.effective_chat or str(update.effective_chat.id) != str(get_config().telegram_chat_id):
        return
    try:
        data = json.loads(msg.web_app_data.data)
    except (TypeError, ValueError):
        await msg.reply_text("No se pudo leer la selección. Abre /vacaciones de nuevo.")
        return
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        await msg.reply_text("La selección no tiene un formato válido.")
        return
    handler = WEBAPP_CONTROLLERS.get(data["type"])
    if not handler:
        await msg.reply_text("La selección no es compatible. Abre /vacaciones de nuevo.")
        return
    await handler(update, context, data)
