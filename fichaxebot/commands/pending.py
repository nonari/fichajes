from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.tasks import marks
from fichaxebot.utils import MADRID_TZ


async def show_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    pending = marks.pending(context.application)
    if not pending:
        await update.message.reply_text("No hay marcajes programados en el scheduler.")
        return

    lines = []
    for task in pending:
        when = task.when.astimezone(MADRID_TZ)
        lines.append(f"• {task.payload['action'].capitalize()} el {when.strftime('%d/%m a las %H:%M')}")

    await update.message.reply_text("Marcajes pendientes:\n" + "\n".join(lines))
