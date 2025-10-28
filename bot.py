import os
import asyncio
import random
from datetime import datetime, timedelta, date
from typing import Final, Optional
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from fichador import fichar
from holidays import es_festivo_galicia
from logging_config import get_logger

load_dotenv()

TOKEN: Final[Optional[str]] = os.getenv("TELEGRAM_TOKEN")
CHAT_ID: Final[Optional[str]] = os.getenv("TELEGRAM_CHAT_ID")

logger = get_logger(__name__)

application = ApplicationBuilder().token(TOKEN).build()
scheduled_jobs = {}  # {date: asyncio.Task}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot de fichaje USC listo. Te preguntaré a las 09:00 en días laborables.")


async def preguntar_fichaje(context: ContextTypes.DEFAULT_TYPE):
    hoy = date.today()
    if hoy.weekday() >= 5 or es_festivo_galicia(hoy):
        return  # Sábado, domingo o festivo

    logger.info("Enviando solicitud de fichaje para %s", hoy.isoformat())
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text="📅 Buenos días! ¿Quieres fichar hoy?",
        reply_markup=ReplyKeyboardMarkup([["Sí", "No"]], one_time_keyboard=True, resize_keyboard=True)
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
        # ya hubo respuesta hoy, ignora duplicados
        logger.info("Respuesta adicional para %s ignorada", hoy.isoformat())
        return

    if respuesta == "sí" or respuesta == "si":
        await update.message.reply_text("🔄 Fichando ahora...")
        resultado = fichar()
        logger.info("Resultado del fichaje de entrada: %s", resultado)
        await update.message.reply_text(resultado)

        # programa segundo fichaje (7h ± 2min)
        minutos_extra = random.randint(-2, 2)
        delay = timedelta(hours=7, minutes=minutos_extra)
        hora_salida = datetime.now() + delay

        logger.info(
            "Programando fichaje de salida para %s (delay=%s)",
            hora_salida.isoformat(),
            delay,
        )

        async def fichar_salida():
            resultado2 = fichar()
            logger.info("Resultado del fichaje de salida: %s", resultado2)
            await context.bot.send_message(chat_id=CHAT_ID, text=f"🏁 Fichaje de salida completado ({resultado2})")

        job = asyncio.create_task(asyncio.sleep(delay.total_seconds(), result=None))
        job.add_done_callback(lambda _: asyncio.create_task(fichar_salida()))
        scheduled_jobs[hoy] = job

    elif respuesta == "no":
        await update.message.reply_text("🚫 No se fichará hoy.")
        logger.info("Fichaje cancelado por el usuario para %s", hoy.isoformat())
        scheduled_jobs[hoy] = None
    else:
        await update.message.reply_text("Por favor responde 'Sí' o 'No'.")
        logger.info("Respuesta inválida recibida: %s", respuesta)


async def programar_pregunta():
    """Programa la pregunta diaria a las 09:00."""
    now = datetime.now()
    target = datetime.combine(now.date(), datetime.strptime("09:00", "%H:%M").time())
    if now > target:
        target += timedelta(days=1)

    delay = (target - now).total_seconds()
    await asyncio.sleep(delay)
    await preguntar_fichaje(application.bot.create_context())


async def main():
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_respuesta))

    # tarea recurrente diaria
    async def scheduler():
        while True:
            await programar_pregunta()
            await asyncio.sleep(24 * 3600)

    asyncio.create_task(scheduler())
    logger.info("🤖 Bot iniciado. Esperando horario de preguntas.")
    await application.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
