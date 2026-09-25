import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fichaxebot.scheduler import Misfire, TaskScheduler
from fichaxebot.scrap_functions.mark import CheckInResult
from fichaxebot.tasks import marks
from fichaxebot.utils import MADRID_TZ
from tests.scheduler_fakes import Clock, fake_app, fire

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=MADRID_TZ)


async def noop(context, task):
    pass


def started_app(test):
    """An app whose scheduler is started with the mark kind and one unrelated kind."""
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    scheduler = TaskScheduler("123", path=Path(directory.name) / ".schedule.data", clock=Clock(NOW))
    marks.register(scheduler)
    scheduler.register_kind("demo.step", noop, misfire=Misfire.RUN_LATE)
    app = fake_app()
    app.scheduler = scheduler
    app.web_session = object()
    scheduler.start(app)
    test.config = SimpleNamespace(telegram_chat_id="123", auto_checkout_delay=timedelta(hours=7),
                                  auto_checkout_random_offset_minutes=0)
    for target, value in (("fichaxebot.tasks.marks.get_config", test.config),
                          ("fichaxebot.tasks.marks.get_madrid_now", NOW)):
        patcher = patch(target, return_value=value)
        patcher.start()
        test.addCleanup(patcher.stop)
    return app, scheduler


class MarksTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app, self.scheduler = started_app(self)

    def test_schedule_validates_action_and_time(self):
        task = marks.schedule(self.app, "entrada", NOW + timedelta(hours=1))
        self.assertEqual((task.kind, task.payload), ("mark", {"action": "entrada"}))
        with self.assertRaises(ValueError):
            marks.schedule(self.app, "descanso", NOW + timedelta(hours=1))
        with self.assertRaisesRegex(ValueError, "ya ha pasado"):
            marks.schedule(self.app, "salida", NOW)

    def test_pending_and_cancel_only_see_marks(self):
        marks.schedule(self.app, "entrada", NOW + timedelta(hours=1))
        marks.schedule(self.app, "salida", NOW + timedelta(hours=8))
        other = self.scheduler.schedule("demo.step", NOW + timedelta(hours=2))
        self.assertEqual([task.payload["action"] for task in marks.pending(self.app)], ["entrada", "salida"])
        self.assertEqual(marks.cancel(self.app, "salida"), 1)
        self.assertEqual(marks.cancel(self.app), 1)
        self.assertEqual(marks.pending(self.app), [])
        self.assertEqual(self.scheduler.pending(), [other])

    def test_auto_checkout_time(self):
        compute = marks.compute_auto_checkout_time
        self.assertEqual(compute(NOW, timedelta(hours=7), 0), NOW + timedelta(hours=7))
        self.assertEqual(compute(NOW, timedelta(hours=7), 3, rand=lambda low, high: -2),
                         NOW + timedelta(hours=7, minutes=-2))
        self.assertEqual(compute(NOW, timedelta(minutes=1), 3, rand=lambda low, high: -3),
                         NOW + timedelta(minutes=1))

    def test_schedule_auto_checkout_requires_configuration(self):
        task = marks.schedule_auto_checkout(self.app)
        self.assertEqual((task.payload["action"], task.when), ("salida", NOW + timedelta(hours=7)))
        self.config.auto_checkout_delay = None
        with self.assertRaises(ValueError):
            marks.schedule_auto_checkout(self.app)

    def test_describe(self):
        task = marks.schedule(self.app, "entrada", NOW + timedelta(hours=1))
        self.assertEqual(marks.describe(task), "Entrada el 01/10 09:00")

    async def test_successful_scheduled_entry_reports_and_schedules_checkout(self):
        marks.schedule(self.app, "entrada", NOW + timedelta(hours=1))
        result = CheckInResult(True, "entrada", "✅ ok")
        with patch("fichaxebot.tasks.marks.execute_check_in_async", AsyncMock(return_value=result)) as check_in:
            await fire(self.app, self.app.job_queue.of("once")[0])
        check_in.assert_awaited_once()
        texts = [call.kwargs["text"] for call in self.app.bot.send_message.await_args_list]
        self.assertEqual(texts[:2], ["🚪 Marcaje programado de entrada ejecutado.", "✅ ok"])
        self.assertIn("Salida automática programada", texts[2])
        self.assertEqual([task.payload["action"] for task in marks.pending(self.app)], ["salida"])

    async def test_failed_scheduled_entry_does_not_schedule_checkout(self):
        marks.schedule(self.app, "entrada", NOW + timedelta(hours=1))
        result = CheckInResult(False, "entrada", "⚠️ no")
        with patch("fichaxebot.tasks.marks.execute_check_in_async", AsyncMock(return_value=result)):
            await fire(self.app, self.app.job_queue.of("once")[0])
        self.assertEqual(marks.pending(self.app), [])
