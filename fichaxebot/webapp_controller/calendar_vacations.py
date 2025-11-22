import json
from logging import getLogger


async def handle_calendar_selection(update, context, data):
    msg = update.effective_message
    days = data.get("days", [])

    if not days:
        return await msg.reply_text(json.dumps({"error": "Debes seleccionar días"}))

    # accepted → WebApp switches to second screen
    return await msg.reply_text(json.dumps({"ok": True}))


async def handle_calendar_final(update, context, data):
    logger = getLogger(__name__)
    msg = update.effective_message

    days = data.get("days")
    option = data.get("option")

    if option not in ("op1", "op2", "op3"):
        return await msg.reply_text(json.dumps({"error": "Opción inválida"}))

    # Save to DB or whatever
    logger.info("User submitted final calendar data: %s", data)

    return await msg.reply_text(
        json.dumps({"ok": True, "message": "Datos guardados correctamente"}, ensure_ascii=False)
    )