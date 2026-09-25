from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.tasks import marks
from fichaxebot.config import get_config
from fichaxebot.utils import (
    MADRID_TZ,
    execute_check_in_async,
    get_madrid_now,
    parse_hour_minute,
)


async def mark(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    appconfig = get_config()
    if not context.args:
        await update.message.reply_text("Uso: /marcar entrada|salida [HH:MM]")
        return

    action = context.args[0].lower().strip()
    if action not in {"entrada", "salida"}:
        await update.message.reply_text("Acción no reconocida. Usa 'entrada' o 'salida'.")
        return

    scheduled_time: Optional[datetime] = None
    if len(context.args) >= 2:
        hour_arg = context.args[1]
        parsed_time = parse_hour_minute(hour_arg)
        if parsed_time is None:
            await update.message.reply_text(
                "Formato de hora inválido. Usa HH:MM en formato 24 horas."
            )
            return

        now = get_madrid_now()
        scheduled_time = datetime.combine(now.date(), parsed_time, tzinfo=MADRID_TZ)
        if scheduled_time <= now:
            await update.message.reply_text(
                "La hora indicada ya ha pasado hoy. Indica una hora futura."
            )
            return

    if scheduled_time:
        try:
            marks.schedule(context.application, action, scheduled_time)
        except ValueError as exc:  # pragma: no cover - validated earlier
            await update.message.reply_text(str(exc))
            return

        await update.message.reply_text(
            "🗓️ Marcaje programado de {} para las {}.".format(
                action, scheduled_time.strftime("%H:%M")
            )
        )
        return

    session = context.application.web_session
    result = await execute_check_in_async(action, session, context)
    await update.message.reply_text(result.message)

    if action == "entrada":
        if result.success:
            auto_delay = appconfig.auto_checkout_delay
            if auto_delay:
                try:
                    auto_mark = marks.schedule_auto_checkout(context.application)
                except ValueError:
                    await update.message.reply_text(
                        "⚠️ No se programó la salida porque la hora calculada no es válida."
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
        return
    else:
        if result.success:
            removed = marks.cancel(context.application, "salida")
            if removed:
                await update.message.reply_text(
                    "🗓️ Se cancelaron {} marcajes de salida programados.".format(removed)
                )
