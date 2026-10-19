import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fichaxebot.scrap_functions.mark import CheckInResult
from plugins.marcaje import marks
from plugins.marcaje.commands import cancel, mark, show_pending
from tests.test_marcaje_marks import NOW, started_app


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

    async def test_immediate_mark_says_it_is_trying_before_checking_in(self):
        events = []
        self.reply.side_effect = events.append

        async def check_in(action, session, context):
            events.append(f"check-in {action}")
            return CheckInResult(True, action, "✅ ok")

        for action in ("entrada", "salida"):
            with self.subTest(action=action), \
                    patch("plugins.marcaje.commands.execute_check_in_async", check_in):
                events.clear()
                self.context.args = [action]
                await mark(self.update, self.context)
                self.assertEqual(events[:3], [f"🔄 Intentando fichaje de {action}...", f"check-in {action}", "✅ ok"])
