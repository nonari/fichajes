import asyncio
import signal
from datetime import timedelta, time as dtime

from telegram import ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from commands import (
    AWAITING_RESPONSE_KEY,
    QUESTION_DATE_KEY,
    REMINDER_ATTEMPTS_KEY,
    REMINDER_JOB_KEY,
    cancel,
    mark,
    process_response,
    show_pending,
    show_records,
    start,
)
from config import get_config
from utils import (
    MADRID_TZ,
    cancel_reminder,
    get_madrid_now,
    is_galicia_holiday,
)
from fichador import get_today_records
from logging_config import get_logger
from scheduler import SchedulerManager

logger = get_logger(__name__)

config = get_config()
TOKEN = config.telegram_token
CHAT_ID = config.telegram_chat_id

MAX_REMINDERS = 3
REMINDER_INTERVAL = timedelta(minutes=5)


async def ask_for_check_in(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = get_madrid_now().date()
    scheduler_manager: SchedulerManager = context.application.scheduler_manager
    if scheduler_manager.has_pending():
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


async def main():
    appconfig = get_config()
    scheduler_manager = SchedulerManager(appconfig.telegram_chat_id)
    app = ApplicationBuilder().token(TOKEN).build()
    app.scheduler_manager = scheduler_manager
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("marcar", mark))
    app.add_handler(CommandHandler("cancelar", cancel))
    app.add_handler(CommandHandler("marcajes", show_records))
    app.add_handler(CommandHandler("pendientes", show_pending))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_response))

    app.job_queue.run_daily(
        ask_for_check_in,
        time=dtime(hour=9, minute=0, tzinfo=MADRID_TZ),
        days=(0, 1, 2, 3, 4),
    )

    restaurados = scheduler_manager.load_from_disk(app)

    now = get_madrid_now()
    question_time = now.replace(hour=9, minute=0, second=0, microsecond=0)

    if (
        not scheduler_manager.has_pending()
        and now >= question_time
        and now.weekday() < 5
        and not is_galicia_holiday(now.date())
    ):
        logger.info("🤖 Bot started after 9:00 without scheduled marks. Triggering question.")
        app.job_queue.run_once(ask_for_check_in, when=0)
    else:
        logger.info("🤖 Bot started. Waiting for question schedule.")

    stop_event = asyncio.Event()

    def handle_stop(*_):
        print("🛑 Stopping bot...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, handle_stop)

    await app.initialize()
    await app.start()

    if restaurados:
        lineas = []
        for mark in restaurados:
            fecha = mark.when.astimezone(MADRID_TZ)
            lineas.append(f"• {mark.action.capitalize()} el {fecha.strftime('%d/%m %H:%M')}")
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text="♻️ Bot reiniciado. Marcajes restaurados:\n" + "\n".join(lineas),
        )

    try:
        records = await asyncio.to_thread(get_today_records)
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
    print("🤖 Bot running. Press Ctrl+C to stop.")

    await stop_event.wait()

    await app.updater.stop()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
