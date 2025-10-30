import os
import asyncio
import signal
from datetime import datetime, timedelta, date, time as dtime
from pathlib import Path
from typing import Final, Optional
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    Job,
    filters,
)
from fichador import fichar, FichajeResultado
from holidays_local import es_festivo_galicia
from logging_config import get_logger

load_dotenv()

TOKEN: Final[Optional[str]] = os.getenv("TELEGRAM_TOKEN")
CHAT_ID: Final[Optional[str]] = os.getenv("TELEGRAM_CHAT_ID")

logger = get_logger(__name__)

MADRID_TZ = ZoneInfo("Europe/Madrid")
SCHEDULE_FILE = Path(".schedule.data")

exit_job: Optional[Job] = None
scheduled_exit_time: Optional[datetime] = None

PREGUNTA_FECHA_KEY = "pregunta_fecha"
AWAITING_RESPONSE_KEY = "awaiting_respuesta"
REMINDER_JOB_KEY = "recordatorio_pregunta_job"
REMINDER_ATTEMPTS_KEY = "recordatorio_pregunta_intentos"
MAX_REMINDERS = 3
REMINDER_INTERVAL = timedelta(minutes=5)


def ahora_madrid() -> datetime:
    return datetime.now(MADRID_TZ)


def cargar_programacion_desde_fichero() -> Optional[datetime]:
    if not SCHEDULE_FILE.exists():
        return None

    contenido = SCHEDULE_FILE.read_text().strip()
    if not contenido:
        return None

    try:
        programado = datetime.fromisoformat(contenido)
    except ValueError:
        logger.warning("Formato inválido en %s: %s", SCHEDULE_FILE, contenido)
        return None

    if programado.tzinfo is None:
        programado = programado.replace(tzinfo=MADRID_TZ)
    else:
        programado = programado.astimezone(MADRID_TZ)

    return programado


def guardar_programacion_en_fichero(moment: datetime) -> None:
    SCHEDULE_FILE.write_text(moment.astimezone(MADRID_TZ).isoformat())


def limpiar_fichero_programacion() -> None:
    if SCHEDULE_FILE.exists():
        SCHEDULE_FILE.write_text("")


def cancelar_programacion(clear_file: bool = True) -> None:
    global exit_job, scheduled_exit_time

    if exit_job:
        exit_job.schedule_removal()
        exit_job = None

    scheduled_exit_time = None

    if clear_file:
        limpiar_fichero_programacion()


def finalizar_programacion(clear_file: bool = True) -> None:
    global exit_job, scheduled_exit_time

    exit_job = None
    scheduled_exit_time = None

    if clear_file:
        limpiar_fichero_programacion()


def cancelar_recordatorio(app) -> None:
    job = app.bot_data.pop(REMINDER_JOB_KEY, None)
    if job:
        job.schedule_removal()

    app.bot_data.pop(REMINDER_ATTEMPTS_KEY, None)


def hay_programacion_pendiente() -> bool:
    return scheduled_exit_time is not None


def parsear_hora_minuto(valor: str) -> Optional[dtime]:
    partes = valor.strip().split(":")
    if len(partes) != 2:
        return None

    try:
        hora = int(partes[0])
        minuto = int(partes[1])
    except ValueError:
        return None

    if not (0 <= hora < 24 and 0 <= minuto < 60):
        return None

    return dtime(hour=hora, minute=minuto)


def programar_cierre(app, hora: datetime) -> None:
    global exit_job, scheduled_exit_time

    cancelar_programacion(clear_file=False)
    hora = hora.astimezone(MADRID_TZ)
    exit_job = app.job_queue.run_once(
        fichaje_salida_programado,
        when=hora,
        name="cierre_programado",
    )
    scheduled_exit_time = hora
    guardar_programacion_en_fichero(hora)
    logger.info("Cierre programado para %s", hora)


async def ejecutar_fichaje_async(
    accion: str, context: ContextTypes.DEFAULT_TYPE
) -> FichajeResultado:
    resultado = await asyncio.to_thread(fichar, accion)
    logger.info("Resultado del fichaje de %s: %s", accion, resultado.message)
    return resultado


async def fichaje_salida_programado(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Ejecutando fichaje de salida programado")
    resultado = await ejecutar_fichaje_async("salida", context)
    await context.bot.send_message(chat_id=CHAT_ID, text=f"🏁 {resultado.message}")
    finalizar_programacion()


# ─────────────────────────────────────────────────────────────
# Commands and message handlers
# ─────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text(
        "👋 Bot de fichaje USC listo.\n"
        "Preguntaré cada día laborable a las 09:00 (hora de Madrid).\n"
        "Comandos disponibles: /marcar entrada|salida [HH:MM] y /cancelar."
    )


async def preguntar_fichaje(context: ContextTypes.DEFAULT_TYPE):
    hoy = ahora_madrid().date()

    if hay_programacion_pendiente():
        logger.info("Se omite la pregunta diaria porque ya hay un cierre programado.")
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


async def procesar_respuesta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    respuesta = update.message.text.lower().strip()
    hoy = ahora_madrid().date()

    logger.info(
        "Respuesta recibida: '%s' de chat_id=%s usuario=%s",
        respuesta,
        update.effective_chat.id if update.effective_chat else "desconocido",
        update.effective_user.username if update.effective_user else "anonimo",
    )

    if respuesta in {"sí", "si"}:
        if hay_programacion_pendiente():
            await update.message.reply_text(
                "⚠️ Ya existe un cierre programado. Cancélalo con /cancelar si deseas reiniciar."
            )
            context.application.bot_data[AWAITING_RESPONSE_KEY] = False
            context.application.bot_data[PREGUNTA_FECHA_KEY] = hoy
            cancelar_recordatorio(context.application)
            return

        await update.message.reply_text("🔄 Intentando fichaje de entrada...")
        resultado = await ejecutar_fichaje_async("entrada", context)
        await update.message.reply_text(resultado.message)

        if resultado.success:
            hora_salida = ahora_madrid() + timedelta(hours=7)
            programar_cierre(context.application, hora_salida)
            await update.message.reply_text(
                f"🕐 Cierre programado para las {hora_salida.strftime('%H:%M')}"
            )
        else:
            await update.message.reply_text(
                "🚫 No se programó el cierre al no confirmarse la apertura."
            )

        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        context.application.bot_data[PREGUNTA_FECHA_KEY] = hoy
        cancelar_recordatorio(context.application)
        return

    if respuesta == "no":
        await update.message.reply_text("🚫 No se fichará hoy.")
        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        context.application.bot_data[PREGUNTA_FECHA_KEY] = hoy
        cancelar_recordatorio(context.application)
        return

    if respuesta in {"marcar", "/marcar", "cancelar", "/cancelar"}:
        return  # serán gestionados por los comandos

    if context.application.bot_data.get(AWAITING_RESPONSE_KEY):
        await update.message.reply_text("Por favor responde 'Sí' o 'No'.")


async def comando_marcar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text("Uso: /marcar entrada|salida [HH:MM]")
        return

    accion = context.args[0].lower().strip()
    if accion not in {"entrada", "salida"}:
        await update.message.reply_text("Acción no reconocida. Usa 'entrada' o 'salida'.")
        return

    if accion == "salida" and len(context.args) >= 2:
        hora_arg = context.args[1]
        hora_programada = parsear_hora_minuto(hora_arg)
        if hora_programada is None:
            await update.message.reply_text(
                "Formato de hora inválido. Usa HH:MM en formato 24 horas."
            )
            return

        ahora = ahora_madrid()
        momento_programado = datetime.combine(ahora.date(), hora_programada).replace(
            tzinfo=MADRID_TZ
        )

        if momento_programado <= ahora:
            await update.message.reply_text(
                "La hora indicada ya ha pasado hoy. Indica una hora futura."
            )
            return

        programar_cierre(context.application, momento_programado)
        await update.message.reply_text(
            f"🗓️ Salida programada para las {momento_programado.strftime('%H:%M')}."
        )
        return

    resultado = await ejecutar_fichaje_async(accion, context)
    await update.message.reply_text(resultado.message)

    if accion == "entrada":
        if resultado.success:
            hora_salida = ahora_madrid() + timedelta(hours=7)
            programar_cierre(context.application, hora_salida)
            await update.message.reply_text(
                f"🕐 Cierre programado para las {hora_salida.strftime('%H:%M')}"
            )
        else:
            await update.message.reply_text(
                "🚫 No se programó el cierre porque la entrada no se confirmó."
            )
        return

    # accion == "salida"
    if resultado.success:
        if hay_programacion_pendiente():
            cancelar_programacion()
            await update.message.reply_text("🗓️ Programación de cierre cancelada tras registrar la salida.")
        else:
            finalizar_programacion()


async def comando_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not hay_programacion_pendiente():
        await update.message.reply_text("No hay ningún cierre programado actualmente.")
        return

    cancelar_programacion()
    await update.message.reply_text("🗓️ Cierre programado cancelado.")


# ─────────────────────────────────────────────────────────────
# Main app and JobQueue scheduler
# ─────────────────────────────────────────────────────────────
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("marcar", comando_marcar))
    app.add_handler(CommandHandler("cancelar", comando_cancelar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_respuesta))

    app.job_queue.run_daily(
        preguntar_fichaje,
        time=dtime(hour=9, minute=0, tzinfo=MADRID_TZ),
        days=(0, 1, 2, 3, 4),
    )

    # Cargar programación previa si existe
    pendiente = cargar_programacion_desde_fichero()
    mensaje_reinicio: Optional[str] = None
    ahora = ahora_madrid()
    hora_pregunta = ahora.replace(hour=9, minute=0, second=0, microsecond=0)
    if pendiente:
        if pendiente > ahora:
            programar_cierre(app, pendiente)
            mensaje_reinicio = (
                "♻️ Bot reiniciado. El cierre ya estaba programado para las "
                f"{pendiente.strftime('%H:%M')} y se respetará."
            )
        else:
            logger.info("Se encontró una programación de salida caducada. Se limpia el fichero.")
            finalizar_programacion()

    # Preguntar inmediatamente si corresponde
    if (
        not hay_programacion_pendiente()
        and ahora >= hora_pregunta
        and ahora.weekday() < 5
        and not es_festivo_galicia(ahora.date())
    ):
        logger.info("🤖 Bot iniciado después de las 9:00 sin cierre programado. Lanzando pregunta.")
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

    if mensaje_reinicio:
        await app.bot.send_message(chat_id=CHAT_ID, text=mensaje_reinicio)

    await app.updater.start_polling()
    print("🤖 Bot running. Press Ctrl+C to stop.")

    await stop_event.wait()

    await app.updater.stop()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
