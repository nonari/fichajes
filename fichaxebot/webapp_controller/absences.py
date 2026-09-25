"""Absence Mini App replies, optional chat PDFs, and screenshot confirmation."""
import asyncio
from pathlib import Path
import re
import tempfile

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.commit import ReadOnlyStop
from fichaxebot.scrap_functions.absence_request import (
    AbsenceRequestCancelled, AbsenceRequestError, AbsenceRequestUncertain,
    MAX_ATTACHMENTS, MAX_FILE_BYTES, validate_pdf, validate_selection,
)
from fichaxebot.webapp_controller.vacation_confirmation import ACTIVE_KEY, STOPPING_KEY, PendingVacation

logger = get_logger(__name__)
SELECTION_KEY = 'absence_selection'
ATTACHMENT_PATTERN = r'^absence_(ready|skip|discard):[0-9a-f]{32}$'
COLLECTION_SECONDS = 15 * 60


class PendingAbsence(PendingVacation):
    callback_prefix = 'absence'
    filename = 'solicitud-ausencia.png'

    def __init__(self, application, *, chat_id, user_id, timeout, selection, message):
        super().__init__(application, chat_id=chat_id, user_id=user_id, timeout=timeout)
        self.selection = selection
        self.origin_message = message
        self.phase = 'collecting'
        self.directory = tempfile.TemporaryDirectory(prefix='usc-absence-')
        self.files_lock = asyncio.Lock()
        self.expiry = None
        self.prompt = None

    def owns(self, update):
        return (update.effective_chat is not None and update.effective_user is not None
                and update.effective_chat.id == self.chat_id and update.effective_user.id == self.user_id)

    def accepts_update(self, update):
        if self.phase != 'collecting' or not self.owns(update):
            return False
        query = update.callback_query
        return bool((query and re.fullmatch(ATTACHMENT_PATTERN, query.data or ''))
                    or getattr(update.effective_message, 'document', None))

    def keyboard(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('Continuar', callback_data=f'absence_ready:{self.token}')],
            [InlineKeyboardButton('Continuar sin documentos', callback_data=f'absence_skip:{self.token}')],
            [InlineKeyboardButton('Cancelar', callback_data=f'absence_discard:{self.token}')],
        ])

    async def ask_for_files(self):
        self.prompt = await self.origin_message.reply_text(
            'Envía los justificantes como documentos PDF en este chat (máximo 10, de 1 MiB cada uno). '
            'Después pulsa Continuar. Tienes 15 minutos.\n'
            'USC indica que no se adjunten datos de salud ni diagnósticos; '
            'los justificantes de asistencia pueden indicar fecha y hora.', reply_markup=self.keyboard())
        self.expiry = self.loop.call_later(COLLECTION_SECONDS,
            lambda: self.application.create_task(self.expire()))

    async def expire(self):
        async with self.files_lock:
            if self.phase != 'collecting':
                return
            await self.finish()
        await self.origin_message.reply_text('La espera de documentos ha caducado. Abre /ausencias de nuevo.')

    async def finish(self):
        self.phase = 'finished'
        if self.expiry:
            self.expiry.cancel()
        self.directory.cleanup()
        if self.prompt:
            try:
                await self.prompt.edit_reply_markup(reply_markup=None)
            except Exception:
                logger.warning('Could not remove absence attachment controls')
        await super().finish()

    async def stop(self, task):
        async with self.files_lock:
            if self.phase == 'collecting':
                self.abort('El bot se está apagando. No se envió la solicitud.')
                await self.finish()
                return
        await super().stop(task)


def _pending(update, context):
    pending = context.application.bot_data.get(ACTIVE_KEY)
    if not isinstance(pending, PendingAbsence) or not pending.owns(update):
        return None
    return pending


async def handle_absence_request(update, context, data):
    msg = update.effective_message
    state = context.application.bot_data
    if state.get(ACTIVE_KEY) or state.get(STOPPING_KEY):
        await msg.reply_text('Hay una solicitud en curso o el bot se está apagando. Inténtalo más tarde.')
        return
    snapshot = context.user_data.get(SELECTION_KEY)
    if not snapshot or data.get('requestId') != snapshot.get('requestId'):
        await msg.reply_text('Esta selección ha caducado. Abre /ausencias de nuevo.')
        return
    try:
        if type(data.get('attachDocuments')) is not bool or 'attachments' in data:
            raise AbsenceRequestError('La selección no es válida. Los documentos se envían en el chat.')
        selection = validate_selection(data, snapshot)
    except AbsenceRequestError as exc:
        await msg.reply_text(str(exc))
        return
    context.user_data.pop(SELECTION_KEY, None)
    config = get_config()
    pending = PendingAbsence(context.application, chat_id=update.effective_chat.id,
        user_id=update.effective_user.id, timeout=config.absence_confirmation_timeout_seconds,
        selection=selection, message=msg)
    state[ACTIVE_KEY] = pending
    try:
        if data['attachDocuments']:
            await pending.ask_for_files()
        else:
            _start_submission(pending, update)
    except BaseException:
        await pending.finish()
        raise


async def handle_document(update, context):
    pending = _pending(update, context)
    if not pending:
        await update.effective_message.reply_text('No hay una solicitud tuya esperando documentos. Abre /ausencias.')
        return
    async with pending.files_lock:
        if pending.phase != 'collecting':
            return
        document = update.effective_message.document
        paths = pending.selection['attachments']
        name = Path((document.file_name or '').replace('\\', '/')).name
        if (len(paths) >= MAX_ATTACHMENTS or not name.lower().endswith('.pdf')
                or not document.file_size or document.file_size > MAX_FILE_BYTES):
            await update.effective_message.reply_text('Se permiten hasta 10 PDF, no vacíos y de como máximo 1 MiB cada uno.')
            return
        target_dir = Path(pending.directory.name) / str(len(paths))
        target_dir.mkdir(exist_ok=True)
        target = target_dir / name
        try:
            remote = await document.get_file()
            await remote.download_to_drive(custom_path=target)
            path = validate_pdf(target)
        except asyncio.CancelledError:
            target.unlink(missing_ok=True)
            raise
        except Exception:
            target.unlink(missing_ok=True)
            await update.effective_message.reply_text('No se pudo recibir un PDF válido. Vuelve a adjuntarlo.')
            return
        paths.append(path)
        await pending.prompt.edit_text('Documentos recibidos:\n' + '\n'.join(Path(path).name for path in paths),
                                       reply_markup=pending.keyboard())


async def handle_attachment_action(update, context):
    query = update.callback_query
    pending = _pending(update, context)
    action, token = query.data.split(':', 1)
    if pending is None or token != pending.token:
        await query.answer('Esta solicitud no está disponible para ti.')
        return
    async with pending.files_lock:
        if pending.phase != 'collecting':
            await query.answer('Esta selección ya ha terminado.')
            return
        if action == 'absence_discard':
            await pending.finish()
            await query.answer('Solicitud cancelada.')
            return
        if action == 'absence_ready' and not pending.selection['attachments']:
            await query.answer('Adjunta un PDF o pulsa Continuar sin documentos.')
            return
        if action not in ('absence_ready', 'absence_skip'):
            return
        if action == 'absence_skip':
            pending.selection['attachments'] = []
        _start_submission(pending, update)
        await query.answer('Preparando la solicitud…')


def _start_submission(pending, update):
    pending.phase = 'preparing'
    if pending.expiry:
        pending.expiry.cancel()
    pending.task = pending.application.create_task(_submit_and_report(pending), update=update)


async def _submit_and_report(pending):
    status = None
    try:
        status = await pending.origin_message.reply_text('Preparando la solicitud de ausencia en USC…')
        session = pending.application.web_session
        if get_config().absence_confirmation_enabled:
            result = await pending.run(session.submit_absence_request, pending.selection)
        else:
            # Join the worker even on task cancellation before removing its files.
            worker = asyncio.create_task(asyncio.to_thread(session.submit_absence_request, pending.selection))
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                await pending._join(worker)
                raise
        await status.edit_text(f"Solicitud {result['id']} · Estado en USC: {result['state']}.\n{result['absenceTypeName']}")
    except AbsenceRequestCancelled as exc:
        if status:
            await status.edit_text(pending.reason if pending.decision.done() else str(exc))
    except ReadOnlyStop:
        if status:
            await status.edit_text(
                "🧪 Modo de solo lectura: la solicitud llegó al paso final, pero no se envió a USC."
            )
    except (AbsenceRequestError, PermissionError) as exc:
        if status:
            await status.edit_text(f'{exc} Abre /ausencias para volver a intentarlo.')
    except AbsenceRequestUncertain as exc:
        if status:
            await status.edit_text(f'{exc} https://fichaxe.usc.gal/pas/solicitudesPropias')
    except Exception:
        logger.exception('Could not complete absence request')
        if status:
            await status.edit_text('No se pudo confirmar el envío. Comprueba tus solicitudes antes de repetirlo: '
                                   'https://fichaxe.usc.gal/pas/solicitudesPropias')
    finally:
        pending.abort('Solicitud cancelada. No se envió a USC.')
        await pending.finish()


def register_absences(application):
    application.add_handler(CallbackQueryHandler(handle_attachment_action, pattern=ATTACHMENT_PATTERN))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
