"""Launch the absence Mini App with an authenticated, single-use catalog."""
import asyncio
from uuid import uuid4

from telegram import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from fichaxebot.commands.vacations import _build_vacations_url
from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.webapp_controller.absences import SELECTION_KEY

logger = get_logger(__name__)


async def show_absences(update, context):
    config = get_config()
    if not update.message or str(update.effective_chat.id) != str(config.telegram_chat_id):
        return
    if not config.absences_webapp_url:
        await update.message.reply_text("Configura 'absences_webapp_url' para abrir la selección de ausencias.")
        return
    status = await update.message.reply_text('Consultando los tipos de ausencia en USC…')
    try:
        payload = await asyncio.to_thread(context.application.web_session.fetch_absence_selection_data)
    except Exception:
        logger.exception('Could not load absence catalog')
        await status.edit_text('No se pudieron consultar las ausencias en USC. Inténtalo de nuevo.')
        return
    payload = {**payload, 'requestId': uuid4().hex, 'confirmationRequired': config.absence_confirmation_enabled}
    context.user_data[SELECTION_KEY] = payload
    await update.message.reply_text('Elige el tipo de ausencia, las fechas y si quieres adjuntar documentos.',
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton('Solicitar ausencia', web_app=WebAppInfo(
            url=_build_vacations_url(config.absences_webapp_url, payload)))]], resize_keyboard=True))
    await status.delete()
