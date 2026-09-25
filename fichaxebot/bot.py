import asyncio
import signal
from telegram import ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from fichaxebot.commands import (
    AWAITING_RESPONSE_KEY,
    QUESTION_DATE_KEY,
    REMINDER_ATTEMPTS_KEY,
    REMINDER_JOB_KEY,
    cancel,
    mark as mark_command,
    process_response,
    show_pending,
    show_records,
    show_calendar,
    show_vacations,
    show_vacations_info,
    start,
)
from fichaxebot.commands.absences import show_absences
from fichaxebot.webapp_controller.absences import register_absences
from fichaxebot.config import get_config
from fichaxebot.access_control import restrict_to_chat
from fichaxebot.plugins import register_plugins
from fichaxebot.utils import (
    cancel_reminder,
    get_madrid_now,
    is_galicia_holiday,
)
from fichaxebot.usc_api import UscWebSession
from fichaxebot.logging_config import get_logger
from fichaxebot.scheduler import TaskScheduler
from fichaxebot.tasks import marks
from fichaxebot.webapp_controller.router import dispatch_webapp_reply
from fichaxebot.webapp_controller.vacation_confirmation import (
    register_vacation_confirmation, stop_vacation_confirmation,
)

logger = get_logger(__name__)

config = get_config()
TOKEN = config.telegram_token
CHAT_ID = config.telegram_chat_id

MAX_REMINDERS = config.max_reminders
REMINDER_INTERVAL = config.reminder_interval
QUESTION_TIME = config.daily_question_time


async def ask_for_check_in(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = get_madrid_now().date()
    if marks.pending(context.application):
        logger.info("Skipping daily question because there are already scheduled marks.")
        cancel_reminder(context.application, REMINDER_JOB_KEY, REMINDER_ATTEMPTS_KEY)
        return

    if today.weekday() >= 5 or is_galicia_holiday(today):
        logger.info("Skipping question on %s (weekend or holiday)", today)
        cancel_reminder(context.application, REMINDER_JOB_KEY, REMINDER_ATTEMPTS_KEY)
        return

    logger.info("Sending check-in request for %s", today.isoformat())
    context.application.bot_data[QUESTION_DATE_KEY] = today
    context.application.bot_data[AWAITING_RESPONSE_KEY] = True
    cancel_reminder(context.application, REMINDER_JOB_KEY, REMINDER_ATTEMPTS_KEY)
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text="📅 Buenos días! ¿Quieres fichar hoy?",
        reply_markup=ReplyKeyboardMarkup(
            [["Sí", "No"]], one_time_keyboard=True, resize_keyboard=True
        ),
    )

    context.application.bot_data[REMINDER_ATTEMPTS_KEY] = 0
    if MAX_REMINDERS > 0:
        reminder_job = context.job_queue.run_repeating(
            send_check_in_reminder,
            interval=REMINDER_INTERVAL.total_seconds(),
            first=REMINDER_INTERVAL.total_seconds(),
            name="recordatorio_pregunta",
        )
        context.application.bot_data[REMINDER_JOB_KEY] = reminder_job


async def send_check_in_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.application.bot_data.get(AWAITING_RESPONSE_KEY):
        cancel_reminder(context.application, REMINDER_JOB_KEY, REMINDER_ATTEMPTS_KEY)
        return

    attempts = context.application.bot_data.get(REMINDER_ATTEMPTS_KEY, 0) + 1

    if attempts > MAX_REMINDERS:
        logger.info("Maximum number of reminders reached. Stopping notifications.")
        cancel_reminder(context.application, REMINDER_JOB_KEY, REMINDER_ATTEMPTS_KEY)
        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        return

    context.application.bot_data[REMINDER_ATTEMPTS_KEY] = attempts
    logger.info("Sending check-in reminder %s/%s", attempts, MAX_REMINDERS)
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text="⏰ Recordatorio: ¿Quieres fichar hoy? Responde 'Sí' o 'No'.",
        reply_markup=ReplyKeyboardMarkup(
            [["Sí", "No"]], one_time_keyboard=True, resize_keyboard=True
        ),
    )


async def _run_bot() -> None:
    appconfig = get_config()
    scheduler = TaskScheduler(appconfig.telegram_chat_id)
    marks.register(scheduler)
    scheduler.register_daily(
        "daily_question", ask_for_check_in,
        at=QUESTION_TIME, weekdays=range(5), catch_up=True,
    )
    app = ApplicationBuilder().token(TOKEN).build()
    restrict_to_chat(app, appconfig.telegram_chat_id)
    register_vacation_confirmation(app)
    register_absences(app)
    app.scheduler = scheduler

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("marcar", mark_command))
    app.add_handler(CommandHandler("cancelar", cancel))
    app.add_handler(CommandHandler("marcajes", show_records))
    app.add_handler(CommandHandler("pendientes", show_pending))
    app.add_handler(CommandHandler("calendario", show_calendar))
    app.add_handler(CommandHandler("vacaciones", show_vacations))
    app.add_handler(CommandHandler("ausencias", show_absences))
    app.add_handler(CommandHandler("vacaciones_info", show_vacations_info))
    register_plugins(app, appconfig.plugins)

    web_session = UscWebSession()
    app.web_session = web_session

    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, dispatch_webapp_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_response))

    stop_event = asyncio.Event()

    def handle_stop(*_):
        logger.info("🛑 Stopping bot...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, handle_stop)

    await app.initialize()
    await app.start()

    report = scheduler.start(app)
    startup_message = scheduler.startup_message(report)
    if startup_message:
        await app.bot.send_message(chat_id=CHAT_ID, text=startup_message)
    logger.info("🤖 Bot started.")

    try:
        records = await asyncio.to_thread(web_session.get_today_records)
    except Exception as exc:  # noqa: BLE001
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ No se pudieron consultar los marcajes actuales: {exc}",
        )
    else:
        if records:
            resumen = "\n".join(
                f"• Entrada: {item['entrada']} | Salida: {item['salida']}" for item in records
            )
        else:
            resumen = "ℹ️ No hay marcajes registrados hoy."
        await app.bot.send_message(chat_id=CHAT_ID, text=resumen)

    await app.updater.start_polling()
    logger.info("🤖 Bot running. Press Ctrl+C to stop.")

    await stop_event.wait()

    await app.updater.stop()
    await stop_vacation_confirmation(app)
    await app.stop()
    await app.shutdown()

    web_session.close()

def main() -> None:
    asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
