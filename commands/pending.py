from telegram import Update
from telegram.ext import ContextTypes

from scheduler import SchedulerManager
from utils import MADRID_TZ


async def show_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    scheduler_manager: SchedulerManager = context.application.scheduler_manager
    pending = scheduler_manager.list_pending()
    if not pending:
        await update.message.reply_text("No hay marcajes programados en el scheduler.")
        return

    lines = []
    for mark in pending:
        when = mark.when.astimezone(MADRID_TZ)
        lines.append(f"• {mark.action.capitalize()} el {when.strftime('%d/%m a las %H:%M')}")

    await update.message.reply_text("Marcajes pendientes:\n" + "\n".join(lines))
