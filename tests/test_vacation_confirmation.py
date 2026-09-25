import asyncio
import unittest
from types import SimpleNamespace
from threading import Event
from unittest.mock import AsyncMock

from telegram.ext import ApplicationHandlerStop

from fichaxebot.webapp_controller.vacation_confirmation import (
    ACTIVE_KEY, PendingVacation, handle_confirmation, reject_while_busy, stop_vacation_confirmation,
)


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.document = SimpleNamespace(edit_reply_markup=AsyncMock())
        self.app = SimpleNamespace(bot_data={}, bot=SimpleNamespace(
            send_document=AsyncMock(return_value=self.document)))
        self.pending = PendingVacation(self.app, chat_id=123, user_id=456, timeout=60)
        self.app.bot_data[ACTIVE_KEY] = self.pending
        self.context = SimpleNamespace(application=self.app)

    def update(self, action='confirm', token=None, chat=123, user=456):
        return SimpleNamespace(
            effective_chat=SimpleNamespace(id=chat), effective_user=SimpleNamespace(id=user),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            callback_query=SimpleNamespace(
                data=f'vacation_{action}:{token or self.pending.token}', answer=AsyncMock()),
        )

    async def start_wait(self):
        task = asyncio.create_task(self.pending.request_confirmation(b'png'))
        await asyncio.sleep(0)
        return task

    async def test_first_owner_decision_wins_and_document_is_original(self):
        task = await self.start_wait()
        sent = self.app.bot.send_document.call_args.kwargs
        self.assertEqual(sent['document'].getvalue(), b'png')
        self.assertEqual(sent['filename'], 'solicitud-vacaciones.png')
        await handle_confirmation(self.update(user=999), self.context)
        await handle_confirmation(self.update(chat=999), self.context)
        await handle_confirmation(self.update(token='old'), self.context)
        self.assertFalse(task.done())
        await handle_confirmation(self.update(), self.context)
        await handle_confirmation(self.update(action='cancel'), self.context)
        self.assertTrue(await task)

    async def test_cancel_and_timeout(self):
        task = await self.start_wait()
        await handle_confirmation(self.update(action='cancel'), self.context)
        self.assertFalse(await task)
        self.assertIn('cancelada', self.pending.reason)
        self.pending = PendingVacation(self.app, chat_id=123, user_id=456, timeout=0.01)
        self.assertFalse(await self.pending.request_confirmation(b'png'))
        self.assertIn('Tiempo', self.pending.reason)

    async def test_deadline_checked_even_before_timeout_task_runs(self):
        task = await self.start_wait()
        self.pending.deadline = asyncio.get_running_loop().time() - 1
        await handle_confirmation(self.update(), self.context)
        self.assertFalse(await task)

    async def test_timer_starts_only_after_delivery(self):
        delivered = asyncio.Event()
        async def slow_send(**kwargs):
            await delivered.wait()
            return self.document
        self.app.bot.send_document.side_effect = slow_send
        self.pending.timeout = 0.01
        task = await self.start_wait()
        await asyncio.sleep(0.02)
        self.assertIsNone(self.pending.deadline)
        self.assertFalse(task.done())
        delivered.set()
        await asyncio.sleep(0)
        await handle_confirmation(self.update(), self.context)
        self.assertTrue(await task)

    async def test_send_failure_and_shutdown_abort_without_confirmation(self):
        self.app.bot.send_document.side_effect = RuntimeError('delivery failed')
        self.assertFalse(await self.pending.request_confirmation(b'png'))
        self.assertIn('captura', self.pending.reason)
        self.app.bot.send_document.side_effect = None
        self.pending = PendingVacation(self.app, chat_id=123, user_id=456, timeout=60)
        self.app.bot_data[ACTIVE_KEY] = self.pending
        task = await self.start_wait()
        await stop_vacation_confirmation(self.app)
        self.assertFalse(await task)
        self.assertIn('apagando', self.pending.reason)

    async def test_shutdown_during_preparation_skips_delivery(self):
        await stop_vacation_confirmation(self.app)
        self.assertFalse(await self.pending.request_confirmation(b'png'))
        self.app.bot.send_document.assert_not_called()

    async def test_gate_allows_confirmation_and_rejects_other_updates(self):
        await reject_while_busy(self.update(), self.context)
        update = self.update()
        update.callback_query = None
        with self.assertRaises(ApplicationHandlerStop):
            await reject_while_busy(update, self.context)
        update.effective_message.reply_text.assert_awaited_once()
        self.app.bot_data.clear()
        await reject_while_busy(update, self.context)


class TransactionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def start_transaction(self, *, timeout=60, delivery_error=False, hold_after_confirm=False):
        from fichaxebot.scrap_functions.vacation_request import VacationRequestCancelled
        from fichaxebot.webapp_controller.calendar_vacations import _run_confirmed_request
        loop = asyncio.get_running_loop()
        self.delivered = asyncio.Event()
        self.submitted = asyncio.Event()
        self.worker_finished = Event()
        self.release = Event()
        self.addCleanup(self.release.set)
        self.document = SimpleNamespace(edit_reply_markup=AsyncMock())
        self.status = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(reply_text=AsyncMock(return_value=self.status))
        async def send_document(**kwargs):
            self.delivered.set()
            if delivery_error:
                raise RuntimeError('delivery failed')
            return self.document
        self.app = SimpleNamespace(bot_data={}, bot=SimpleNamespace(send_document=send_document))
        self.pending = PendingVacation(self.app, chat_id=123, user_id=456, timeout=timeout)
        self.app.bot_data[ACTIVE_KEY] = self.pending
        def submit(selection, confirm):
            try:
                if not confirm(b'png'):
                    raise VacationRequestCancelled()
                loop.call_soon_threadsafe(self.submitted.set)
                if hold_after_confirm and not self.release.wait(2):
                    raise AssertionError('worker was not released')
                return dict(id='123', state='Solicitada', vacationTypeName='Vacacións', year=2026,
                            days=['2026-10-01'])
            finally:
                self.worker_finished.set()
        self.pending.task = asyncio.create_task(_run_confirmed_request(
            message, SimpleNamespace(submit_vacation_request=submit), {}, self.pending))
        self.addAsyncCleanup(self.cleanup_transaction)
        await asyncio.wait_for(self.delivered.wait(), 1)

    async def cleanup_transaction(self):
        self.release.set()
        await stop_vacation_confirmation(self.app)

    async def test_shutdown_unblocks_and_awaits_live_worker(self):
        await self.start_transaction()
        self.assertFalse(self.worker_finished.is_set())
        await asyncio.wait_for(stop_vacation_confirmation(self.app), 1)
        self.assertTrue(self.worker_finished.is_set())
        self.assertFalse(self.submitted.is_set())
        self.assertNotIn(ACTIVE_KEY, self.app.bot_data)
        self.assertIn('apagando', self.status.edit_text.call_args.args[0])
        self.document.edit_reply_markup.assert_awaited_once_with(reply_markup=None)

    async def test_shutdown_reporting_failure_does_not_interrupt_shutdown(self):
        await self.start_transaction()
        self.status.edit_text.side_effect = RuntimeError('Telegram offline')
        await asyncio.wait_for(stop_vacation_confirmation(self.app), 1)
        self.assertTrue(self.worker_finished.is_set())
        self.assertNotIn(ACTIVE_KEY, self.app.bot_data)

    async def test_shutdown_allows_confirmed_worker_to_finish(self):
        await self.start_transaction(hold_after_confirm=True)
        self.pending.decision.set_result(True)
        await asyncio.wait_for(self.submitted.wait(), 1)
        shutdown = asyncio.create_task(stop_vacation_confirmation(self.app))
        await asyncio.sleep(0)
        self.assertFalse(shutdown.done())
        self.assertFalse(self.worker_finished.is_set())
        self.release.set()
        await asyncio.wait_for(shutdown, 1)
        self.assertTrue(self.worker_finished.is_set())
        self.assertIn('Solicitada', self.status.edit_text.call_args.args[0])

    async def test_timeout_and_delivery_failure_release_worker_without_submitting(self):
        for delivery_error in (False, True):
            with self.subTest(delivery_error=delivery_error):
                await self.start_transaction(timeout=0.01, delivery_error=delivery_error)
                await asyncio.wait_for(self.pending.task, 1)
                self.assertTrue(self.worker_finished.is_set())
                self.assertFalse(self.submitted.is_set())
                self.assertNotIn(ACTIVE_KEY, self.app.bot_data)
                self.assertIn('No se envió', self.status.edit_text.call_args.args[0])
