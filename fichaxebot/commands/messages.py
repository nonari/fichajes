from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.tasks import marks
from fichaxebot.config import get_config
from fichaxebot.utils import cancel_reminder, execute_check_in_async, get_madrid_now

from fichaxebot.commands.state import (
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

    session = context.application.web_session
    appconfig = get_config()
    if response in {"sí", "si"} and context.application.bot_data[AWAITING_RESPONSE_KEY]:
        if marks.pending(context.application):
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
        result = await execute_check_in_async("entrada", session, context)
        await update.message.reply_text(result.message)

        if result.success:
            auto_delay = appconfig.auto_checkout_delay
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
