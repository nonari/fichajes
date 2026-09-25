import asyncio
from threading import Event, get_ident
import unittest
from unittest.mock import AsyncMock

from fichaxebot.confirmation import Confirmation


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_documents_are_opaque_and_first_decision_wins(self):
        for document in (b'\x89PNG\r\n', b'%PDF-1.7\n'):
            deliver = AsyncMock()
            confirmation = Confirmation(deliver)
            task = asyncio.create_task(confirmation.request_confirmation(document))
            while confirmation.deadline is None and not task.done():
                await asyncio.sleep(0)
            deliver.assert_awaited_once_with(document)
            self.assertTrue(confirmation.resolve(True))
            self.assertFalse(confirmation.resolve(False))
            self.assertTrue(await task)

    async def test_delivery_precedes_deadline_and_errors_abort(self):
        delivered = asyncio.Event()
        async def deliver(document):
            await delivered.wait()
        confirmation = Confirmation(deliver, timeout=0.01)
        task = asyncio.create_task(confirmation.request_confirmation(b'pdf'))
        await asyncio.sleep(0.02)
        self.assertIsNone(confirmation.deadline)
        self.assertFalse(confirmation.resolve(True))
        delivered.set()
        self.assertFalse(await task)
        self.assertIn('Tiempo', confirmation.reason)
        confirmation = Confirmation(AsyncMock(side_effect=RuntimeError('offline')))
        self.assertFalse(await confirmation.request_confirmation(b'pdf'))

    async def test_expired_decision_is_rejected(self):
        confirmation = Confirmation(AsyncMock())
        task = asyncio.create_task(confirmation.request_confirmation(b'pdf'))
        while confirmation.deadline is None and not task.done():
            await asyncio.sleep(0)
        confirmation.deadline = confirmation.loop.time() - 1
        self.assertFalse(confirmation.resolve(True))
        self.assertFalse(await task)

    async def test_shutdown_unblocks_worker_and_awaits_its_exit(self):
        delivered = asyncio.Event()
        finished = Event()
        async def deliver(document):
            delivered.set()
        confirmation = Confirmation(deliver)
        event_thread = get_ident()
        def transaction(data, confirm):
            self.assertNotEqual(get_ident(), event_thread)
            decision = confirm(b'%PDF-1.7')
            finished.set()
            return decision
        task = asyncio.create_task(confirmation.run(transaction, {}))
        await asyncio.wait_for(delivered.wait(), 1)
        await confirmation.stop(task, 'shutdown')
        self.assertTrue(finished.is_set())
        self.assertFalse(await task)

    async def test_task_cancellation_does_not_abandon_the_worker(self):
        delivered = asyncio.Event()
        finished = Event()
        async def deliver(document):
            delivered.set()
        confirmation = Confirmation(deliver)
        def transaction(confirm):
            try:
                return confirm(b'pdf')
            finally:
                finished.set()
        task = asyncio.create_task(confirmation.run(transaction))
        await asyncio.wait_for(delivered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())

    async def test_shutdown_during_stalled_delivery_releases_worker(self):
        started, stalled = asyncio.Event(), asyncio.Event()
        finished = Event()
        async def deliver(document):
            started.set()
            await stalled.wait()
        confirmation = Confirmation(deliver)
        def transaction(confirm):
            try:
                return confirm(b'pdf')
            finally:
                finished.set()
        task = asyncio.create_task(confirmation.run(transaction))
        await asyncio.wait_for(started.wait(), 1)
        stop = asyncio.create_task(confirmation.stop(task))
        try:
            done, _ = await asyncio.wait([stop], timeout=0.1)
            self.assertIn(stop, done, 'Shutdown must not wait for document delivery to finish')
            self.assertTrue(finished.is_set())
            self.assertFalse(task.result())
        finally:
            stalled.set()
            await stop
