import asyncio
import signal
from datetime import datetime, timedelta, date, time as dtime

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
    PREGUNTA_FECHA_KEY,
    REMINDER_ATTEMPTS_KEY,
    REMINDER_JOB_KEY,
    cancelar_recordatorio,
    comando_cancelar,
    comando_marcar,
    comando_marcajes,
    comando_pendientes,
    procesar_respuesta,
    start,
)
from config import get_config
from core import MADRID_TZ, ahora_madrid
from fichador import obtener_marcajes_hoy
from holidays_local import es_festivo_galicia
from logging_config import get_logger
from scheduler import SchedulerManager

logger = get_logger(__name__)

config = get_config()
TOKEN = config.telegram_token
CHAT_ID = config.telegram_chat_id

MAX_REMINDERS = 3
REMINDER_INTERVAL = timedelta(minutes=5)

async def preguntar_fichaje(context: ContextTypes.DEFAULT_TYPE):
    hoy = ahora_madrid().date()
    scheduler_manager: SchedulerManager = context.application.scheduler_manager
    if scheduler_manager.has_pending():
        logger.info("Se omite la pregunta diaria porque ya hay marcajes programados.")
        cancelar_recordatorio(context.application)
        return

    if hoy.weekday() >= 5 or es_festivo_galicia(hoy):
        logger.info("No se pregunta en %s (fin de semana o festivo)", hoy)
        cancelar_recordatorio(context.application)
        return

    logger.info("Enviando solicitud de fichaje para %s", hoy.isoformat())
    context.application.bot_data[PREGUNTA_FECHA_KEY] = hoy
    context.application.bot_data[AWAITING_RESPONSE_KEY] = True
    cancelar_recordatorio(context.application)
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text="📅 Buenos días! ¿Quieres fichar hoy?",
        reply_markup=ReplyKeyboardMarkup(
            [["Sí", "No"]], one_time_keyboard=True, resize_keyboard=True
        ),
    )

    context.application.bot_data[REMINDER_ATTEMPTS_KEY] = 0
    reminder_job = context.job_queue.run_repeating(
        enviar_recordatorio_pregunta,
        interval=REMINDER_INTERVAL.total_seconds(),
        first=REMINDER_INTERVAL.total_seconds(),
        name="recordatorio_pregunta",
    )
    context.application.bot_data[REMINDER_JOB_KEY] = reminder_job


async def enviar_recordatorio_pregunta(context: ContextTypes.DEFAULT_TYPE):
    if not context.application.bot_data.get(AWAITING_RESPONSE_KEY):
        cancelar_recordatorio(context.application)
        return

    intentos = context.application.bot_data.get(REMINDER_ATTEMPTS_KEY, 0) + 1

    if intentos > MAX_REMINDERS:
        logger.info("Se alcanzó el máximo de recordatorios. Se detienen los avisos.")
        cancelar_recordatorio(context.application)
        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        return

    context.application.bot_data[REMINDER_ATTEMPTS_KEY] = intentos
    logger.info("Enviando recordatorio %s/%s de fichaje", intentos, MAX_REMINDERS)
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
    app.add_handler(CommandHandler("marcar", comando_marcar))
    app.add_handler(CommandHandler("cancelar", comando_cancelar))
    app.add_handler(CommandHandler("marcajes", comando_marcajes))
    app.add_handler(CommandHandler("pendientes", comando_pendientes))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_respuesta))

    app.job_queue.run_daily(
        preguntar_fichaje,
        time=dtime(hour=9, minute=0, tzinfo=MADRID_TZ),
        days=(0, 1, 2, 3, 4),
    )

    restaurados = scheduler_manager.load_from_disk(app)

    ahora = ahora_madrid()
    hora_pregunta = ahora.replace(hour=9, minute=0, second=0, microsecond=0)

    if (
        not scheduler_manager.has_pending()
        and ahora >= hora_pregunta
        and ahora.weekday() < 5
        and not es_festivo_galicia(ahora.date())
    ):
        logger.info("🤖 Bot iniciado después de las 9:00 sin marcajes programados. Lanzando pregunta.")
        app.job_queue.run_once(preguntar_fichaje, when=0)
    else:
        logger.info("🤖 Bot iniciado. Esperando horario de preguntas.")

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
        marcajes = await asyncio.to_thread(obtener_marcajes_hoy)
    except Exception as exc:  # noqa: BLE001
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ No se pudieron consultar los marcajes actuales: {exc}",
        )
    else:
        if marcajes:
            resumen = "\n".join(
                f"• Entrada: {item['entrada']} | Salida: {item['salida']}" for item in marcajes
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
