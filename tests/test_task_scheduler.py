import asyncio
import json
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

from fichaxebot.scheduler import Interrupted, Misfire, ScheduledTask, StartupReport, TaskScheduler
from fichaxebot.utils import MADRID_TZ
from tests.scheduler_fakes import Clock, fake_app, fire

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=MADRID_TZ)  # a Thursday


async def noop(context, task):
    pass


class SchedulerTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / ".schedule.data"
        self.clock = Clock(NOW)
        self.scheduler = TaskScheduler("123", path=self.path, clock=self.clock)
        self.app = fake_app()

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


class TaskLifecycleTests(SchedulerTestCase):
    async def test_task_is_saved_as_running_while_its_handler_runs(self):
        seen = []

        async def handler(context, task):
            seen.append([entry["state"] for entry in self.saved()["tasks"]])

        self.scheduler.register_kind("demo.step", handler, misfire=Misfire.RUN_LATE)
        self.scheduler.start(self.app)
        self.scheduler.schedule("demo.step", NOW + timedelta(hours=1), {"n": 1})
        self.assertEqual(self.saved()["tasks"][0]["state"], "pending")

        await fire(self.app, self.app.job_queue.of("once")[0])

        self.assertEqual(seen, [["running"]])
        self.assertEqual(self.saved()["tasks"], [])
        self.assertEqual(self.scheduler.pending(), [])

    async def test_failing_handler_is_removed_and_reported_only_when_requested(self):
        async def boom(context, task):
            raise RuntimeError("kaput")

        self.scheduler.register_kind("demo.quiet", boom, misfire=Misfire.DISCARD)
        self.scheduler.register_kind("demo.loud", boom, misfire=Misfire.DISCARD, notify_errors=True)
        self.scheduler.start(self.app)
        self.scheduler.schedule("demo.quiet", NOW + timedelta(hours=1))
        self.scheduler.schedule("demo.loud", NOW + timedelta(hours=2))

        with self.assertLogs("fichaxebot.scheduler", "ERROR"):
            for job in self.app.job_queue.of("once"):
                await fire(self.app, job)

        self.assertEqual(self.saved()["tasks"], [])
        self.app.bot.send_message.assert_awaited_once()
        self.assertIn("kaput", self.app.bot.send_message.await_args.kwargs["text"])

    async def test_handler_cut_off_by_shutdown_stays_running_in_the_file(self):
        async def cut_off(context, task):
            raise asyncio.CancelledError()

        self.scheduler.register_kind("demo.step", cut_off, misfire=Misfire.RUN_LATE)
        self.scheduler.start(self.app)
        self.scheduler.schedule("demo.step", NOW + timedelta(hours=1))

        with self.assertRaises(asyncio.CancelledError):
            await fire(self.app, self.app.job_queue.of("once")[0])

        self.assertEqual([entry["state"] for entry in self.saved()["tasks"]], ["running"])

    def test_schedule_validates_its_arguments(self):
        self.scheduler.register_kind("demo.step", noop, misfire=Misfire.DISCARD)
        with self.assertRaises(RuntimeError):
            self.scheduler.schedule("demo.step", NOW + timedelta(hours=1))
        self.scheduler.start(self.app)
        with self.assertRaises(ValueError):
            self.scheduler.schedule("demo.unknown", NOW + timedelta(hours=1))
        with self.assertRaises(TypeError):
            self.scheduler.schedule("demo.step", NOW + timedelta(hours=1), {"when": NOW})
        with self.assertRaises(ValueError):
            self.scheduler.schedule("demo.step", datetime(2026, 10, 1, 9, 0))
        self.assertEqual(self.scheduler.pending(), [])

    def test_registration_rules(self):
        self.scheduler.register_kind("demo.step", noop, misfire=Misfire.DISCARD)

        def sync_handler(context, task):
            pass

        for name, handler in (("demo.step", noop), ("Bad Name", noop), ("a.b.c", noop), ("demo.sync", sync_handler)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.scheduler.register_kind(name, handler, misfire=Misfire.DISCARD)
        self.scheduler.start(self.app)
        with self.assertRaises(RuntimeError):
            self.scheduler.register_kind("demo.late", noop, misfire=Misfire.DISCARD)
        with self.assertRaises(RuntimeError):
            self.scheduler.start(self.app)


class PendingAndCancelTests(SchedulerTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []

        async def record(context, task):
            self.calls.append(task.id)

        self.scheduler.register_kind("mark", record, misfire=Misfire.DISCARD)
        self.scheduler.register_kind("demo.step", record, misfire=Misfire.RUN_LATE)
        self.scheduler.start(self.app)
        self.first = self.scheduler.schedule("mark", NOW + timedelta(hours=2), {"action": "salida"})
        self.second = self.scheduler.schedule("mark", NOW + timedelta(hours=1), {"action": "entrada"})
        self.other = self.scheduler.schedule("demo.step", NOW + timedelta(hours=3))

    def test_pending_filters_by_kind_and_sorts_by_time(self):
        self.assertEqual([task.id for task in self.scheduler.pending("mark")], [self.second.id, self.first.id])
        self.assertEqual(len(self.scheduler.pending()), 3)

    def test_cancel_removes_matching_tasks_and_their_jobs(self):
        removed = self.scheduler.cancel("mark", lambda task: task.payload["action"] == "salida")
        self.assertEqual(removed, 1)
        self.assertEqual([task.id for task in self.scheduler.pending("mark")], [self.second.id])
        self.assertEqual(self.scheduler.cancel("mark"), 1)
        self.assertEqual([task.id for task in self.scheduler.pending()], [self.other.id])
        self.assertEqual([job.data["id"] for job in self.app.job_queue.of("once")], [self.other.id])
        self.assertEqual([entry["id"] for entry in self.saved()["tasks"]], [self.other.id])

    async def test_a_cancelled_job_that_still_fires_does_nothing(self):
        job = next(job for job in self.app.job_queue.jobs if job.data["id"] == self.first.id)
        self.scheduler.cancel("mark")
        await fire(self.app, job)
        self.assertEqual(self.calls, [])


class ScheduledTaskTests(unittest.TestCase):
    def test_naive_timestamp_is_read_as_madrid_time(self):
        task = ScheduledTask.from_dict({"id": "x", "kind": "mark", "when": "2026-10-01T09:00:00", "payload": {}})
        self.assertEqual(task.when.utcoffset(), timedelta(hours=2))
        self.assertEqual(task.state, "pending")

    def test_round_trip(self):
        task = ScheduledTask("x", "mark", datetime(2026, 10, 1, 9, 0, tzinfo=MADRID_TZ), {"action": "entrada"}, "running")
        self.assertEqual(ScheduledTask.from_dict(task.to_dict()), task)


def entry(kind, when, state="pending", payload=None, id="t1"):
    return {"id": id, "kind": kind, "when": when.isoformat(), "payload": payload or {}, "state": state}


class RestoreTests(SchedulerTestCase):
    def write(self, tasks, recurring=None):
        self.path.write_text(json.dumps({"version": 2, "tasks": tasks, "recurring": recurring or {}}),
                             encoding="utf-8")

    def register(self):
        self.calls = []

        async def record(context, task):
            self.calls.append(task.id)

        self.scheduler.register_kind("demo.notify", record, misfire=Misfire.DISCARD_AND_NOTIFY)
        self.scheduler.register_kind("demo.late", record, misfire=Misfire.RUN_LATE, interrupted=Interrupted.RETRY)
        self.scheduler.register_kind("demo.drop", record, misfire=Misfire.DISCARD)

    def test_future_tasks_are_rescheduled_at_their_time(self):
        self.register()
        self.write([entry("demo.notify", NOW + timedelta(hours=1))])
        report = self.scheduler.start(self.app)
        self.assertEqual([task.id for task in report.restored], ["t1"])
        self.assertEqual([job.timing["when"] for job in self.app.job_queue.of("once")], [NOW + timedelta(hours=1)])

    def test_overdue_tasks_follow_their_misfire_policy(self):
        self.register()
        past = NOW - timedelta(hours=1)
        self.write([entry("demo.notify", past, id="n"), entry("demo.late", past, id="l"),
                    entry("demo.drop", past, id="d")])
        report = self.scheduler.start(self.app)
        self.assertEqual([task.id for task in report.missed], ["n"])
        self.assertEqual([(job.data["id"], job.timing["when"]) for job in self.app.job_queue.of("once")], [("l", 0)])
        self.assertEqual([task["id"] for task in self.saved()["tasks"]], ["l"])

    def test_interrupted_tasks_follow_their_interrupted_policy(self):
        self.register()
        earlier = NOW - timedelta(minutes=5)
        self.write([entry("demo.notify", earlier, "running", id="n"), entry("demo.late", earlier, "running", id="l")])
        report = self.scheduler.start(self.app)
        self.assertEqual([task.id for task in report.interrupted], ["n"])
        self.assertEqual([(job.data["id"], job.timing["when"]) for job in self.app.job_queue.of("once")], [("l", 0)])
        self.assertEqual([(task["id"], task["state"]) for task in self.saved()["tasks"]], [("l", "pending")])

    def test_tasks_of_unregistered_kinds_are_kept_untouched(self):
        self.register()
        dormant = entry("gone.plugin", NOW - timedelta(days=1), "running", {"case": "c1"}, id="g")
        self.write([dormant])
        self.scheduler.start(self.app)
        self.scheduler.schedule("demo.notify", NOW + timedelta(hours=1))
        self.assertIn(dormant, self.saved()["tasks"])
        self.assertNotIn("g", [job.data["id"] for job in self.app.job_queue.of("once")])

    def test_unreadable_or_old_format_files_are_moved_aside(self):
        old_format = json.dumps([{"id": "x", "action": "entrada", "when": NOW.isoformat()}])
        for content in ("{not json", old_format, json.dumps({"version": 1, "tasks": []})):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertLogs("fichaxebot.scheduler", "WARNING"):
                    report = TaskScheduler("123", path=self.path, clock=self.clock).start(fake_app())
                self.assertEqual(report, StartupReport())
                backup = self.path.with_name(".schedule.data.corrupt")
                self.assertEqual(backup.read_text(encoding="utf-8"), content)
                self.assertEqual(self.saved()["tasks"], [])

    def test_invalid_entries_are_dropped_and_the_rest_restored(self):
        self.register()
        self.write([{"id": "bad"}, entry("demo.notify", NOW + timedelta(hours=1))])
        with self.assertLogs("fichaxebot.scheduler", "WARNING"):
            report = self.scheduler.start(self.app)
        self.assertEqual([task.id for task in report.restored], ["t1"])

    def test_startup_message_lists_each_group(self):
        self.scheduler.register_kind("demo.notify", noop, misfire=Misfire.DISCARD_AND_NOTIFY,
                                     describe=lambda task: f"Tarea {task.id}")
        self.write([entry("demo.notify", NOW + timedelta(hours=1), id="a"),
                    entry("demo.notify", NOW - timedelta(hours=1), id="b"),
                    entry("demo.notify", NOW - timedelta(hours=1), "running", id="c")])
        text = self.scheduler.startup_message(self.scheduler.start(self.app))
        self.assertTrue(text.startswith("♻️ Bot reiniciado."))
        self.assertLess(text.index("Tarea a"), text.index("Tarea b"))
        self.assertLess(text.index("Tarea b"), text.index("Tarea c"))
        self.assertIn("comprueba USC", text)
        self.assertIsNone(self.scheduler.startup_message(StartupReport()))


class RecurringTests(SchedulerTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []

        async def handler(context):
            self.calls.append(context.job.data["name"])

        self.handler = handler

    def daily(self, scheduler, **overrides):
        options = {"at": time(9, 0), "weekdays": range(5), "catch_up": True, **overrides}
        scheduler.register_daily("daily_question", self.handler, **options)

    def test_daily_job_uses_madrid_time_and_ptb_day_numbers(self):
        self.scheduler.register_daily("core.weekdays", self.handler, at=time(9, 0), weekdays=range(5))
        self.scheduler.register_daily("core.sunday", self.handler, at=time(9, 0), weekdays=[6])
        self.scheduler.start(self.app)
        jobs = {job.name: job.timing for job in self.app.job_queue.of("daily")}
        self.assertEqual(jobs["core.weekdays"]["days"], (1, 2, 3, 4, 5))
        self.assertEqual(jobs["core.sunday"]["days"], (0,))
        self.assertEqual(jobs["core.weekdays"]["time"], time(9, 0, tzinfo=MADRID_TZ))

    async def test_catch_up_runs_once_per_day_across_restarts(self):
        self.clock.now = NOW.replace(hour=10)
        self.daily(self.scheduler)
        self.scheduler.start(self.app)
        [catch_up] = self.app.job_queue.of("once")
        self.assertEqual(catch_up.timing["when"], 0)

        await fire(self.app, catch_up)

        self.assertEqual(self.calls, ["daily_question"])
        self.assertEqual(self.saved()["recurring"], {"daily_question": "2026-10-01"})
        restarted = TaskScheduler("123", path=self.path, clock=self.clock)
        self.daily(restarted)
        app = fake_app()
        restarted.start(app)
        self.assertEqual(app.job_queue.of("once"), [])

    def test_no_catch_up_before_time_on_other_days_or_when_disabled(self):
        cases = (
            (NOW, {}),                                  # 08:00, before 09:00
            (NOW.replace(day=3, hour=10), {}),          # Saturday
            (NOW.replace(hour=10), {"catch_up": False}),
        )
        for now, overrides in cases:
            with self.subTest(now=now, overrides=overrides):
                self.clock.now = now
                scheduler = TaskScheduler("123", path=self.path, clock=self.clock)
                self.daily(scheduler, **overrides)
                app = fake_app()
                scheduler.start(app)
                self.assertEqual(app.job_queue.of("once"), [])

    async def test_daily_run_records_the_date_and_survives_handler_errors(self):
        async def boom(context):
            raise RuntimeError("kaput")

        self.scheduler.register_daily("core.quiet", boom, at=time(9, 0))
        self.scheduler.register_daily("core.loud", boom, at=time(9, 0), notify_errors=True)
        self.scheduler.start(self.app)

        with self.assertLogs("fichaxebot.scheduler", "ERROR"):
            for job in self.app.job_queue.of("daily"):
                await fire(self.app, job)

        self.assertEqual(self.saved()["recurring"], {"core.loud": "2026-10-01", "core.quiet": "2026-10-01"})
        self.app.bot.send_message.assert_awaited_once()

    def test_interval_job(self):
        self.scheduler.register_interval("core.poll", self.handler, every=timedelta(minutes=30))
        self.scheduler.start(self.app)
        [job] = self.app.job_queue.of("repeating")
        self.assertEqual((job.timing["interval"], job.timing["first"]), (1800.0, 1800.0))

    def test_invalid_recurring_registrations(self):
        for kwargs in ({"at": time(9, 0, tzinfo=MADRID_TZ)}, {"at": time(9, 0), "weekdays": []},
                       {"at": time(9, 0), "weekdays": [7]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.scheduler.register_daily("core.bad", self.handler, **kwargs)
        with self.assertRaises(ValueError):
            self.scheduler.register_interval("core.bad", self.handler, every=timedelta(0))
        self.scheduler.register_kind("core.shared", noop, misfire=Misfire.DISCARD)
        with self.assertRaises(ValueError):
            self.scheduler.register_daily("core.shared", self.handler, at=time(9, 0))
