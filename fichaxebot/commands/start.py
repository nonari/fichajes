from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    await update.message.reply_text(
        "👋 Bot de fichaje USC listo.\n"
        "Usa /ayuda para ver todos los comandos disponibles."
    )
