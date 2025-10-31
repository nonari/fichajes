from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    await update.message.reply_text(
        "👋 Bot de fichaje USC listo.\n"
        "Preguntaré cada día laborable a las 09:00 (hora de Madrid).\n"
        "Comandos: /marcar entrada|salida [HH:MM], /marcajes, /pendientes y /cancelar."
    )
