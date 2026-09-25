import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fichaxebot.commands.cancel import cancel
from fichaxebot.commands.pending import show_pending
from fichaxebot.tasks import marks
from tests.test_marks import NOW, started_app


class MarkCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app, self.scheduler = started_app(self)
        self.reply = AsyncMock()
        self.update = SimpleNamespace(message=SimpleNamespace(reply_text=self.reply))
        self.context = SimpleNamespace(application=self.app, args=[])

    async def test_pending_lists_only_marks(self):
        marks.schedule(self.app, "entrada", NOW + timedelta(hours=1))
        self.scheduler.schedule("demo.step", NOW + timedelta(hours=2))
        await show_pending(self.update, self.context)
        self.assertEqual(self.reply.await_args.args[0], "Marcajes pendientes:\n• Entrada el 01/10 a las 09:00")

    async def test_pending_ignores_other_kinds(self):
        self.scheduler.schedule("demo.step", NOW + timedelta(hours=2))
        await show_pending(self.update, self.context)
        self.assertEqual(self.reply.await_args.args[0], "No hay marcajes programados en el scheduler.")

    async def test_cancel_removes_marks_only(self):
        marks.schedule(self.app, "entrada", NOW + timedelta(hours=1))
        other = self.scheduler.schedule("demo.step", NOW + timedelta(hours=2))
        await cancel(self.update, self.context)
        self.assertEqual(self.reply.await_args.args[0], "🗓️ Todos los marcajes programados han sido cancelados.")
        self.assertEqual(self.scheduler.pending(), [other])

    async def test_cancel_without_marks_keeps_other_tasks(self):
        other = self.scheduler.schedule("demo.step", NOW + timedelta(hours=2))
        await cancel(self.update, self.context)
        self.assertEqual(self.reply.await_args.args[0], "No hay marcajes programados actualmente.")
        self.assertEqual(self.scheduler.pending(), [other])
