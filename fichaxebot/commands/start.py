from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.config import get_config


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    appconfig = get_config()
    ask_time = appconfig.daily_question_time.strftime("%H:%M")

    await update.message.reply_text(
        "👋 Bot de fichaje USC listo.\n"
        f"Preguntaré cada día laborable a las {ask_time} (hora de Madrid).\n"
        "Usa /ayuda para ver todos los comandos disponibles."
    )
