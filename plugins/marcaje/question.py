"""Daily '¿Quieres fichar hoy?' question, its reminders and the Sí/No answer."""
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.utils import execute_check_in_async, get_madrid_now, is_galicia_holiday
from plugins.marcaje import marks
from plugins.marcaje.config import plugin_config

logger = get_logger(__name__)

# Not namespaced: the name predates the plugin, and its saved last-run date keeps the question once a day.
DAILY_JOB = "daily_question"

QUESTION_DATE_KEY = "question_date"
AWAITING_RESPONSE_KEY = "awaiting_response"
REMINDER_JOB_KEY = "reminder_job"
REMINDER_ATTEMPTS_KEY = "reminder_attempts"


def cancel_reminder(app) -> None:
    job = app.bot_data.pop(REMINDER_JOB_KEY, None)
    if job:
        job.schedule_removal()
    app.bot_data.pop(REMINDER_ATTEMPTS_KEY, None)


async def ask_for_check_in(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = get_madrid_now().date()
    if marks.pending(context.application):
        logger.info("Skipping daily question because there are already scheduled marks.")
        cancel_reminder(context.application)
        return

    if today.weekday() >= 5 or is_galicia_holiday(today):
        logger.info("Skipping question on %s (weekend or holiday)", today)
        cancel_reminder(context.application)
        return

    logger.info("Sending check-in request for %s", today.isoformat())
    context.application.bot_data[QUESTION_DATE_KEY] = today
    context.application.bot_data[AWAITING_RESPONSE_KEY] = True
    cancel_reminder(context.application)
    await context.bot.send_message(
        chat_id=get_config().telegram_chat_id,
        text="📅 Buenos días! ¿Quieres fichar hoy?",
        reply_markup=ReplyKeyboardMarkup(
            [["Sí", "No"]], one_time_keyboard=True, resize_keyboard=True
        ),
    )

    context.application.bot_data[REMINDER_ATTEMPTS_KEY] = 0
    config = plugin_config(context.application)
    if config.max_reminders > 0:
        reminder_job = context.job_queue.run_repeating(
            send_check_in_reminder,
            interval=config.reminder_interval.total_seconds(),
            first=config.reminder_interval.total_seconds(),
            name="recordatorio_pregunta",
        )
        context.application.bot_data[REMINDER_JOB_KEY] = reminder_job


async def send_check_in_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.application.bot_data.get(AWAITING_RESPONSE_KEY):
        cancel_reminder(context.application)
        return

    attempts = context.application.bot_data.get(REMINDER_ATTEMPTS_KEY, 0) + 1
    max_reminders = plugin_config(context.application).max_reminders

    if attempts > max_reminders:
        logger.info("Maximum number of reminders reached. Stopping notifications.")
        cancel_reminder(context.application)
        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        return

    context.application.bot_data[REMINDER_ATTEMPTS_KEY] = attempts
    logger.info("Sending check-in reminder %s/%s", attempts, max_reminders)
    await context.bot.send_message(
        chat_id=get_config().telegram_chat_id,
        text="⏰ Recordatorio: ¿Quieres fichar hoy? Responde 'Sí' o 'No'.",
        reply_markup=ReplyKeyboardMarkup(
            [["Sí", "No"]], one_time_keyboard=True, resize_keyboard=True
        ),
    )


async def process_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    response = update.message.text.lower().strip()
    today = get_madrid_now().date()

    session = context.application.web_session
    config = plugin_config(context.application)
    awaiting = context.application.bot_data.get(AWAITING_RESPONSE_KEY)
    if response in {"sí", "si"} and awaiting:
        if marks.pending(context.application):
            await update.message.reply_text(
                "⚠️ Ya existen marcajes programados. Cancélalos con /cancelar si deseas reiniciar."
            )
            context.application.bot_data[AWAITING_RESPONSE_KEY] = False
            context.application.bot_data[QUESTION_DATE_KEY] = today
            cancel_reminder(context.application)
            return

        await update.message.reply_text("🔄 Intentando fichaje de entrada...")
        result = await execute_check_in_async("entrada", session, context)
        await update.message.reply_text(result.message)

        if result.success:
            auto_delay = config.auto_checkout_delay
            if auto_delay:
                try:
                    auto_mark = marks.schedule_auto_checkout(context.application)
                except ValueError:
                    await update.message.reply_text(
                        "⚠️ La hora calculada para la salida ya no es válida."
                    )
                else:
                    await update.message.reply_text(
                        "🕐 Salida programada para las {}".format(
                            auto_mark.when.strftime("%H:%M")
                        )
                    )
            else:
                await update.message.reply_text(
                    "ℹ️ La salida automática está desactivada en la configuración."
                )
        else:
            await update.message.reply_text(
                "🚫 No se programó la salida porque la entrada no se confirmó."
            )

        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        context.application.bot_data[QUESTION_DATE_KEY] = today
        cancel_reminder(context.application)
        return

    if response == "no" and awaiting:
        await update.message.reply_text("🚫 No se fichará hoy.")
        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        context.application.bot_data[QUESTION_DATE_KEY] = today
        cancel_reminder(context.application)
        return

    if response in {"marcar", "/marcar", "cancelar", "/cancelar"}:
        return

    if awaiting:
        await update.message.reply_text("Por favor responde 'Sí' o 'No'.")
