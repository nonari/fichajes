import os
import asyncio
import random
import signal
from datetime import datetime, timedelta, date, time as dtime
from typing import Final, Optional
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from fichador import fichar
from holidays_local import es_festivo_galicia
from logging_config import get_logger

load_dotenv()

TOKEN: Final[Optional[str]] = os.getenv("TELEGRAM_TOKEN")
CHAT_ID: Final[Optional[str]] = os.getenv("TELEGRAM_CHAT_ID")

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────
# Telegram application setup
# ─────────────────────────────────────────────────────────────
scheduled_jobs = {}  # {date: asyncio.Task}


# ─────────────────────────────────────────────────────────────
# Commands and message handlers
# ─────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bot de fichaje USC listo.\n"
        "Te preguntaré a las 09:00 en días laborables (no fines de semana ni festivos)."
    )


async def preguntar_fichaje(context: ContextTypes.DEFAULT_TYPE):
    hoy = date.today()
    if hoy.weekday() >= 5 or es_festivo_galicia(hoy):
        logger.info("No se pregunta en %s (fin de semana o festivo)", hoy)
        return

    logger.info("Enviando solicitud de fichaje para %s", hoy.isoformat())
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text="📅 Buenos días! ¿Quieres fichar hoy?",
        reply_markup=ReplyKeyboardMarkup(
            [["Sí", "No"]], one_time_keyboard=True, resize_keyboard=True
        ),
    )


async def procesar_respuesta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    respuesta = update.message.text.lower().strip()
    hoy = date.today()

    logger.info(
        "Respuesta recibida: '%s' de chat_id=%s usuario=%s",
        respuesta,
        update.effective_chat.id if update.effective_chat else "desconocido",
        update.effective_user.username if update.effective_user else "anonimo",
    )

    if hoy in scheduled_jobs:
        logger.info("Respuesta adicional para %s ignorada", hoy.isoformat())
        return

    if respuesta in {"sí", "si"}:
        await update.message.reply_text("🔄 Fichando ahora...")
        resultado = fichar()
        await update.message.reply_text(resultado)
        logger.info("Resultado del fichaje de entrada: %s", resultado)

        # programa segundo fichaje (7h ± 2min)
        minutos_extra = random.randint(-2, 2)
        delay = timedelta(hours=7, minutes=minutos_extra)
        hora_salida = datetime.now() + delay
        logger.info("Programando fichaje de salida para %s", hora_salida)

        async def fichar_salida():
            resultado2 = fichar()
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"🏁 Fichaje de salida completado ({resultado2})",
            )

        async def delayed_task():
            await asyncio.sleep(delay.total_seconds())
            await fichar_salida()

        asyncio.create_task(delayed_task())
        scheduled_jobs[hoy] = "fichar"

        await update.message.reply_text(
            f"🕐 Segundo fichaje programado para {hora_salida.strftime('%H:%M')} (±2 min)"
        )

    elif respuesta == "no":
        await update.message.reply_text("🚫 No se fichará hoy.")
        scheduled_jobs[hoy] = "no"
        logger.info("Fichaje cancelado por el usuario para %s", hoy.isoformat())
    else:
        await update.message.reply_text("Por favor responde 'Sí' o 'No'.")
        logger.info("Respuesta inválida recibida: %s", respuesta)


# ─────────────────────────────────────────────────────────────
# Main app and JobQueue scheduler
# ─────────────────────────────────────────────────────────────
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_respuesta))

    # Scheduler (Mon–Fri 09:00)
    app.job_queue.run_daily(
        preguntar_fichaje,
        time=dtime(hour=9, minute=0),
        days=(0, 1, 2, 3, 4),
    )

    # Immediate run if started after 09:00
    now = datetime.now().time()
    if now >= dtime(hour=9, minute=0) and datetime.today().weekday() < 5:
        logger.info("🤖 Bot iniciado después de las 9:00. Se preguntará el fichaje.")
        app.job_queue.run_once(preguntar_fichaje, when=0)
    else:
        logger.info("🤖 Bot iniciado. Esperando horario de preguntas.")
    # graceful shutdown support
    stop_event = asyncio.Event()

    def handle_stop(*_):
        print("🛑 Stopping bot...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, handle_stop)

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("🤖 Bot running. Press Ctrl+C to stop.")

    await stop_event.wait()

    # graceful teardown
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
