from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes

from scheduler import SchedulerManager
from utils import cancel_reminder, execute_check_in_async, get_madrid_now

from .state import (
    AWAITING_RESPONSE_KEY,
    QUESTION_DATE_KEY,
    REMINDER_ATTEMPTS_KEY,
    REMINDER_JOB_KEY,
)


async def process_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    response = update.message.text.lower().strip()
    today = get_madrid_now().date()

    scheduler_manager: SchedulerManager = context.application.scheduler_manager
    if response in {"sí", "si"} and context.application.bot_data[AWAITING_RESPONSE_KEY]:
        if scheduler_manager.has_pending():
            await update.message.reply_text(
                "⚠️ Ya existen marcajes programados. Cancélalos con /cancelar si deseas reiniciar."
            )
            context.application.bot_data[AWAITING_RESPONSE_KEY] = False
            context.application.bot_data[QUESTION_DATE_KEY] = today
            cancel_reminder(
                context.application,
                REMINDER_JOB_KEY,
                REMINDER_ATTEMPTS_KEY,
            )
            return

        await update.message.reply_text("🔄 Intentando fichaje de entrada...")
        result = await execute_check_in_async("entrada", context)
        await update.message.reply_text(result.message)

        if result.success:
            exit_time = get_madrid_now() + timedelta(hours=7)
            try:
                scheduler_manager.schedule(context.application, "salida", exit_time)
                await update.message.reply_text(
                    f"🕐 Salida programada para las {exit_time.strftime('%H:%M')}"
                )
            except ValueError:
                await update.message.reply_text(
                    "⚠️ La hora calculada para la salida ya no es válida."
                )
        else:
            await update.message.reply_text(
                "🚫 No se programó la salida porque la entrada no se confirmó."
            )

        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        context.application.bot_data[QUESTION_DATE_KEY] = today
        cancel_reminder(
            context.application,
            REMINDER_JOB_KEY,
            REMINDER_ATTEMPTS_KEY,
        )
        return

    if response == "no" and context.application.bot_data[AWAITING_RESPONSE_KEY]:
        await update.message.reply_text("🚫 No se fichará hoy.")
        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        context.application.bot_data[QUESTION_DATE_KEY] = today
        cancel_reminder(
            context.application,
            REMINDER_JOB_KEY,
            REMINDER_ATTEMPTS_KEY,
        )
        return

    if response in {"marcar", "/marcar", "cancelar", "/cancelar"}:
        return

    if context.application.bot_data.get(AWAITING_RESPONSE_KEY):
        await update.message.reply_text("Por favor responde 'Sí' o 'No'.")
