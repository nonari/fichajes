from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.scheduler import SchedulerManager


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    scheduler_manager: SchedulerManager = context.application.scheduler_manager
    if not scheduler_manager.has_pending():
        await update.message.reply_text("No hay marcajes programados actualmente.")
        return

    scheduler_manager.cancel_all()
    await update.message.reply_text("🗓️ Todos los marcajes programados han sido cancelados.")
