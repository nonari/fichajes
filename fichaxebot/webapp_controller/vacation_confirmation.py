"""Keep a live USC transaction waiting for a Telegram decision, in memory only."""
from io import BytesIO
import re
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, TypeHandler

from fichaxebot.logging_config import get_logger
from fichaxebot.confirmation import Confirmation

logger = get_logger(__name__)
ACTIVE_KEY = 'active_vacation_request'
STOPPING_KEY = 'vacation_requests_stopping'
CALLBACK_PATTERN = r'^vacation_(confirm|cancel):[0-9a-f]{32}$'


class PendingVacation(Confirmation):
    def __init__(self, application, *, chat_id, user_id, timeout):
        self.application = application
        self.chat_id = chat_id
        self.user_id = user_id
        super().__init__(self._deliver, timeout=timeout,
                         delivery_failure='No se pudo entregar la captura. No se envió la solicitud a USC.')
        self.token = uuid4().hex
        self.message = None
        self.task = None

    async def _deliver(self, png):
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton('Confirmar y enviar', callback_data=f'vacation_confirm:{self.token}'),
            InlineKeyboardButton('Cancelar', callback_data=f'vacation_cancel:{self.token}'),
        ]])
        self.message = await self.application.bot.send_document(
            chat_id=self.chat_id, document=BytesIO(png), filename='solicitud-vacaciones.png',
            caption=f'Revisa el resumen de USC. Tienes {self.timeout} segundos para confirmar el envío.',
            reply_markup=buttons,
        )

    async def finish(self):
        if self.application.bot_data.get(ACTIVE_KEY) is self:
            self.application.bot_data.pop(ACTIVE_KEY)
        if self.message:
            try:
                await self.message.edit_reply_markup(reply_markup=None)
            except Exception:
                # The token is already inactive, even if Telegram cannot remove the buttons.
                logger.exception('Could not remove vacation confirmation buttons')


async def _handle_confirmation(update, context):
    query = update.callback_query
    pending = context.application.bot_data.get(ACTIVE_KEY)
    action, token = query.data.split(':', 1)
    if (pending is None or token != pending.token or update.effective_chat is None
            or update.effective_chat.id != pending.chat_id
            or update.effective_user is None or update.effective_user.id != pending.user_id):
        await query.answer('Esta confirmación no está disponible para ti.')
        return
    if pending.deadline is None:
        await query.answer('La captura aún se está enviando. Inténtalo de nuevo.')
        return
    if not pending.resolve(action == 'vacation_confirm'):
        await query.answer('Esta confirmación ya ha terminado.')
        return
    if action == 'vacation_confirm':
        await query.answer('Enviando la solicitud a USC…')
    else:
        await query.answer('Solicitud cancelada.')


async def _reject_while_busy(update, context):
    state = context.application.bot_data
    if not state.get(ACTIVE_KEY) and not state.get(STOPPING_KEY):
        return
    query = update.callback_query
    if not state.get(STOPPING_KEY) and query and re.fullmatch(CALLBACK_PATTERN, query.data or ''):
        return
    text = ('El bot se está apagando.' if state.get(STOPPING_KEY) else
            'Hay una solicitud de vacaciones en curso. Termina su confirmación o espera a que finalice.')
    try:
        if query:
            await query.answer(text)
        elif update.effective_message:
            await update.effective_message.reply_text(text)
    except Exception:
        logger.exception('Could not deliver busy response')
    # PTB continues to later handler groups after ordinary exceptions: always stop.
    raise ApplicationHandlerStop


def register_vacation_confirmation(application):
    # Authorization runs in group -2; this gate must precede any browser command.
    application.add_handler(TypeHandler(Update, _reject_while_busy), group=-1)
    application.add_handler(CallbackQueryHandler(_handle_confirmation, pattern=CALLBACK_PATTERN))


async def stop_vacation_confirmation(application):
    application.bot_data[STOPPING_KEY] = True
    pending = application.bot_data.get(ACTIVE_KEY)
    if pending:
        await pending.stop(pending.task)
