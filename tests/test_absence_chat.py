import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fichaxebot.webapp_controller import absences
from fichaxebot.webapp_controller.vacation_confirmation import ACTIVE_KEY, _reject_while_busy, _handle_confirmation
from telegram.ext import ApplicationHandlerStop

CATALOG = {"requestId": "launch", "years": [2026], "types": [
    {"id": "6", "name": "Traslado de domicilio", "requiresHours": False}]}
PAYLOAD = {"requestId": "launch", "year": 2026, "absenceTypeId": "6", "periods": [{"date": "2026-01-01"}]}


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        def submit(data, confirm=None):
            self.calls.append(data)
            return {"id": "123", "state": "Solicitada", "absenceTypeName": "Traslado de domicilio"}
        self.app = SimpleNamespace(bot_data={}, bot=SimpleNamespace(send_document=AsyncMock()),
                                   web_session=SimpleNamespace(submit_absence_request=submit))
        self.tasks = []
        def create_task(coro, **kwargs):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task
        self.app.create_task = create_task
        self.message = SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock(), edit_reply_markup=AsyncMock())), document=None)
        self.update = SimpleNamespace(effective_message=self.message, effective_chat=SimpleNamespace(id=123),
                                      effective_user=SimpleNamespace(id=456), callback_query=None)
        self.context = SimpleNamespace(application=self.app, user_data={absences.SELECTION_KEY: CATALOG}, bot=self.app.bot)
        self.config = SimpleNamespace(absence_confirmation_enabled=False, absence_confirmation_timeout_seconds=60)
        self.patch = patch.object(absences, 'get_config', return_value=self.config)
        self.patch.start()

    async def asyncTearDown(self):
        pending = self.app.bot_data.get(ACTIVE_KEY)
        if pending:
            await pending.stop(pending.task)
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.patch.stop()

    async def launch(self, attach):
        await absences.handle_absence_request(self.update, self.context, {**PAYLOAD, "attachDocuments": attach})
        return self.app.bot_data.get(ACTIVE_KEY)

    async def action(self, pending, action):
        self.update.callback_query = SimpleNamespace(data=f'absence_{action}:{pending.token}', answer=AsyncMock())
        await absences.handle_attachment_action(self.update, self.context)

    async def test_unchecked_submits_without_asking_for_files_and_consumes_token(self):
        await self.launch(False)
        await asyncio.gather(*self.tasks)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['attachments'], [])
        self.assertNotIn(absences.SELECTION_KEY, self.context.user_data)
        self.assertNotIn(ACTIVE_KEY, self.app.bot_data)
        await self.launch(False)
        self.assertEqual(len(self.calls), 1)

    async def test_checked_waits_and_requires_file_or_explicit_skip(self):
        pending = await self.launch(True)
        self.assertEqual(self.calls, [])
        await self.action(pending, 'ready')
        self.assertEqual(self.calls, [])
        await self.action(pending, 'skip')
        await asyncio.gather(*self.tasks)
        self.assertEqual(len(self.calls), 1)

    async def test_chat_pdf_downloaded_used_and_removed(self):
        pending = await self.launch(True)
        async def download(custom_path):
            Path(custom_path).write_bytes(b'%PDF-1.4\nproof')
        document = SimpleNamespace(file_name='proof.pdf', file_size=15,
            get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=download)))
        self.message.document = document
        await absences.handle_document(self.update, self.context)
        path = Path(pending.selection['attachments'][0])
        self.assertTrue(path.exists())
        await self.action(pending, 'ready')
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.calls[0]['attachments'], [str(path)])
        self.assertFalse(path.exists())

    async def test_webapp_cannot_inject_local_file_paths(self):
        await absences.handle_absence_request(self.update, self.context,
            {**PAYLOAD, 'attachDocuments': False, 'attachments': ['/etc/secret.pdf']})
        self.assertEqual(self.calls, [])
        self.assertNotIn(ACTIVE_KEY, self.app.bot_data)

    async def test_invalid_checkbox_is_rejected(self):
        await self.launch('false')
        self.assertEqual(self.calls, [])
        self.assertNotIn(ACTIVE_KEY, self.app.bot_data)

    async def test_other_user_cannot_attach_or_continue(self):
        pending = await self.launch(True)
        self.update.effective_user.id = 999
        self.message.document = SimpleNamespace(file_name='proof.pdf', file_size=10, get_file=AsyncMock())
        await absences.handle_document(self.update, self.context)
        await self.action(pending, 'skip')
        self.assertEqual(pending.selection['attachments'], [])
        self.assertEqual(self.calls, [])

    async def test_expiry_cancel_and_shutdown_remove_draft(self):
        for action in ('cancel', 'expire', 'stop'):
            self.context.user_data[absences.SELECTION_KEY] = CATALOG
            pending = await self.launch(True)
            directory = Path(pending.directory.name)
            if action == 'cancel':
                await self.action(pending, 'discard')
            elif action == 'expire':
                await pending.expire()
            else:
                await pending.stop(None)
            self.assertNotIn(ACTIVE_KEY, self.app.bot_data)
            self.assertFalse(directory.exists())
            self.assertEqual(self.calls, [])

    async def test_gate_allows_draft_actions_but_blocks_other_browser_commands(self):
        pending = await self.launch(True)
        self.message.document = SimpleNamespace()
        await _reject_while_busy(self.update, self.context)
        self.message.document = None
        with self.assertRaises(ApplicationHandlerStop):
            await _reject_while_busy(self.update, self.context)

    async def test_absence_screenshot_confirmation_routes_by_prefix(self):
        self.config.absence_confirmation_enabled = True
        seen = []
        def submit(data, confirm=None):
            seen.append(confirm(b'png'))
            return {"id": "123", "state": "Solicitada", "absenceTypeName": "Traslado de domicilio"}
        self.app.web_session.submit_absence_request = submit
        pending = await self.launch(False)
        for _ in range(1000):
            if pending.deadline is not None:
                break
            await asyncio.sleep(.001)
        self.assertIsNotNone(pending.deadline)
        self.update.callback_query = SimpleNamespace(data=f'absence_confirm:{pending.token}', answer=AsyncMock())
        await _handle_confirmation(self.update, self.context)
        await asyncio.wait_for(asyncio.gather(*self.tasks), 2)
        self.assertEqual(seen, [True])

    async def test_invalid_download_is_removed_and_draft_can_retry(self):
        pending = await self.launch(True)
        async def download(custom_path):
            Path(custom_path).write_bytes(b'not a PDF')
        self.message.document = SimpleNamespace(file_name='proof.pdf', file_size=9,
            get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=download)))
        await absences.handle_document(self.update, self.context)
        self.assertEqual(pending.selection['attachments'], [])
        self.assertEqual(list(Path(pending.directory.name).rglob('*.pdf')), [])
        self.assertEqual(pending.phase, 'collecting')

    async def test_oversized_file_is_rejected_before_download(self):
        pending = await self.launch(True)
        document = SimpleNamespace(file_name='proof.pdf', file_size=1048577, get_file=AsyncMock())
        self.message.document = document
        await absences.handle_document(self.update, self.context)
        document.get_file.assert_not_awaited()
        self.assertEqual(pending.selection['attachments'], [])

    async def test_duplicate_continue_cannot_start_second_submission(self):
        pending = await self.launch(True)
        await self.action(pending, 'skip')
        await self.action(pending, 'skip')
        await asyncio.gather(*self.tasks)
        self.assertEqual(len(self.calls), 1)

    async def test_shutdown_preserves_files_until_unconfirmed_worker_exits(self):
        from threading import Event
        entered, release = Event(), Event()
        exists = []
        def submit(data):
            entered.set()
            release.wait(3)
            exists.append(Path(data['attachments'][0]).exists())
            return {'id':'123','state':'Solicitada','absenceTypeName':'Traslado'}
        self.app.web_session.submit_absence_request = submit
        pending = await self.launch(True)
        path = Path(pending.directory.name) / 'proof.pdf'
        path.write_bytes(b'%PDF-1.4\nproof')
        pending.selection['attachments'] = [str(path)]
        await self.action(pending, 'ready')
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            stop = asyncio.create_task(pending.stop(pending.task))
            await asyncio.sleep(.02)
            self.assertFalse(stop.done())
            self.assertTrue(path.exists())
        finally:
            release.set()
        await asyncio.wait_for(stop, 2)
        self.assertEqual(exists, [True])
        self.assertFalse(path.exists())

    async def test_shutdown_declines_screenshot_confirmation_and_cleans_files(self):
        from fichaxebot.scrap_functions.absence_request import AbsenceRequestCancelled
        self.config.absence_confirmation_enabled = True
        submitted = []
        def submit(data, confirm):
            if not confirm(b'png'):
                raise AbsenceRequestCancelled('cancelled')
            submitted.append(True)
        self.app.web_session.submit_absence_request = submit
        pending = await self.launch(False)
        for _ in range(1000):
            if pending.deadline is not None:
                break
            await asyncio.sleep(.001)
        await asyncio.wait_for(pending.stop(pending.task), 2)
        self.assertEqual(submitted, [])
        self.assertFalse(Path(pending.directory.name).exists())
        self.assertNotIn(ACTIVE_KEY, self.app.bot_data)
