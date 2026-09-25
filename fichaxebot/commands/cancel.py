from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.tasks import marks


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    if not marks.pending(context.application):
        await update.message.reply_text("No hay marcajes programados actualmente.")
        return

    marks.cancel(context.application)
    await update.message.reply_text("🗓️ Todos los marcajes programados han sido cancelados.")
