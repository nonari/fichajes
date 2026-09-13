import json
from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.webapp_controller.calendar_vacations import handle_calendar_selection, handle_calendar_final

WEBAPP_CONTROLLERS = {
    "calendar_selection": handle_calendar_selection,
    "calendar_final": handle_calendar_final
}


async def dispatch_webapp_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Generic entry point for ALL WebApp-based replies.
    Routes message to specific controller based on payload["type"].
    """
    msg = update.effective_message
    if not msg or not msg.web_app_data:
        return

    try:
        data = json.loads(msg.web_app_data.data)
    except Exception:
        return await msg.reply_text(json.dumps({"error": "Invalid JSON"}, ensure_ascii=False))

    msg_type = data.get("type")
    if not msg_type:
        return await msg.reply_text(json.dumps({"error": "Missing 'type' field"}, ensure_ascii=False))

    handler = WEBAPP_CONTROLLERS.get(msg_type)
    if not handler:
        return await msg.reply_text(json.dumps({"error": f"Unknown type '{msg_type}'"}, ensure_ascii=False))

    # route to controller
    return await handler(update, context, data)