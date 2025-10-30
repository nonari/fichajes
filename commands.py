from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, time as dtime
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from core import MADRID_TZ, ahora_madrid, ejecutar_fichaje_async
from fichador import obtener_marcajes_hoy
from scheduler import scheduler_manager

PREGUNTA_FECHA_KEY = "pregunta_fecha"
AWAITING_RESPONSE_KEY = "awaiting_respuesta"
REMINDER_JOB_KEY = "recordatorio_pregunta_job"
REMINDER_ATTEMPTS_KEY = "recordatorio_pregunta_intentos"


def cancelar_recordatorio(app) -> None:
    job = app.bot_data.pop(REMINDER_JOB_KEY, None)
    if job:
        job.schedule_removal()
    app.bot_data.pop(REMINDER_ATTEMPTS_KEY, None)


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text(
        "👋 Bot de fichaje USC listo.\n"
        "Preguntaré cada día laborable a las 09:00 (hora de Madrid).\n"
        "Comandos: /marcar entrada|salida [HH:MM], /marcajes, /pendientes y /cancelar."
    )


async def procesar_respuesta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    respuesta = update.message.text.lower().strip()
    hoy = ahora_madrid().date()

    if respuesta in {"sí", "si"} and context.application.bot_data[AWAITING_RESPONSE_KEY]:
        if scheduler_manager.has_pending():
            await update.message.reply_text(
                "⚠️ Ya existen marcajes programados. Cancélalos con /cancelar si deseas reiniciar."
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
            try:
                scheduler_manager.schedule(context.application, "salida", hora_salida)
                await update.message.reply_text(
                    f"🕐 Salida programada para las {hora_salida.strftime('%H:%M')}"
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
        context.application.bot_data[PREGUNTA_FECHA_KEY] = hoy
        cancelar_recordatorio(context.application)
        return

    if respuesta == "no" and context.application.bot_data[AWAITING_RESPONSE_KEY]:
        await update.message.reply_text("🚫 No se fichará hoy.")
        context.application.bot_data[AWAITING_RESPONSE_KEY] = False
        context.application.bot_data[PREGUNTA_FECHA_KEY] = hoy
        cancelar_recordatorio(context.application)
        return

    if respuesta in {"marcar", "/marcar", "cancelar", "/cancelar"}:
        return

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

    momento_programado: Optional[datetime] = None
    if len(context.args) >= 2:
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

    if momento_programado:
        try:
            scheduler_manager.schedule(context.application, accion, momento_programado)
        except ValueError as exc:  # pragma: no cover - ya validado
            await update.message.reply_text(str(exc))
            return

        await update.message.reply_text(
            "🗓️ Marcaje programado de {} para las {}.".format(
                accion, momento_programado.strftime("%H:%M")
            )
        )
        return

    resultado = await ejecutar_fichaje_async(accion, context)
    await update.message.reply_text(resultado.message)

    if accion == "entrada":
        if resultado.success:
            hora_salida = ahora_madrid() + timedelta(hours=7)
            try:
                scheduler_manager.schedule(context.application, "salida", hora_salida)
                await update.message.reply_text(
                    f"🕐 Salida programada para las {hora_salida.strftime('%H:%M')}"
                )
            except ValueError:
                await update.message.reply_text(
                    "⚠️ No se programó la salida porque la hora calculada no es válida."
                )
        else:
            await update.message.reply_text(
                "🚫 No se programó la salida porque la entrada no se confirmó."
            )
        return

    if resultado.success:
        eliminados = scheduler_manager.cancel_by_action("salida")
        if eliminados:
            await update.message.reply_text(
                "🗓️ Se cancelaron {} marcajes de salida programados.".format(eliminados)
            )


async def comando_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not scheduler_manager.has_pending():
        await update.message.reply_text("No hay marcajes programados actualmente.")
        return

    scheduler_manager.cancel_all()
    await update.message.reply_text("🗓️ Todos los marcajes programados han sido cancelados.")


async def comando_marcajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text("🔍 Consultando marcajes de hoy...")
    try:
        marcajes = await asyncio.to_thread(obtener_marcajes_hoy)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"❌ No se pudo obtener la información: {exc}")
        return

    if not marcajes:
        await update.message.reply_text("ℹ️ No se encontraron marcajes registrados hoy.")
        return

    lineas = [
        f"• Entrada: {item['entrada']} | Salida: {item['salida']}" for item in marcajes
    ]
    await update.message.reply_text("\n".join(lineas))


async def comando_pendientes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    pendientes = scheduler_manager.list_pending()
    if not pendientes:
        await update.message.reply_text("No hay marcajes programados en el scheduler.")
        return

    lineas = []
    for mark in pendientes:
        fecha = mark.when.astimezone(MADRID_TZ)
        lineas.append(
            f"• {mark.action.capitalize()} el {fecha.strftime('%d/%m a las %H:%M')}"
        )

    await update.message.reply_text("Marcajes pendientes:\n" + "\n".join(lineas))
