# Core Task Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the marks-only `SchedulerManager` with a generic `TaskScheduler` (typed persisted one-shot tasks + declared recurring jobs) that core code and plugins use the same way.

**Architecture:** `fichaxebot/scheduler.py` holds `TaskScheduler`: a registry of task kinds and recurring jobs, a persisted task store (`.schedule.data`, version 2) with a `pending → running → removed` lifecycle, and restore policies for missed and interrupted tasks. Marks become an ordinary kind registered by `fichaxebot/tasks/marks.py`. PTB's `JobQueue` remains the timer underneath.

**Tech Stack:** Python 3.12, python-telegram-bot 20.7 (`JobQueue`), `unittest` (run with `.venv/bin/python -m unittest`).

**Spec:** `docs/superpowers/specs/2026-09-25-task-scheduler-design.md`

## Global Constraints

- No new dependencies.
- User-facing text is Spanish, matching existing messages.
- All times are Europe/Madrid (`fichaxebot.utils.MADRID_TZ`, a `zoneinfo.ZoneInfo`; never call `.localize`).
- Persistence always goes through `fichaxebot.storage.write_json_atomic`; the file is `fichaxebot.scheduler.SCHEDULE_FILE` (next to `config.json`).
- No migration from the old `.schedule.data` list format: it is moved to `.schedule.data.corrupt`.
- Kind and job names match `[a-z0-9_]+(\.[a-z0-9_]+)?`; plugins use `<plugin>.<name>`.
- Run tests with `.venv/bin/python -m unittest <module> -v` from the repository root. `pytest` is not installed.
- `tests/test_fichador_login.py` already fails on import (`fichaxebot.fichador` no longer exists); it is not part of this work.
- Commits: the user has not authorised commits yet. Run the commit steps only after the user approves; otherwise leave changes uncommitted.

## File Structure

| File | Responsibility |
| --- | --- |
| `fichaxebot/scheduler.py` | Modify: add `TaskScheduler`, `ScheduledTask`, `Misfire`, `Interrupted`, `StartupReport`; remove `SchedulerManager`/`ScheduledMark` in Task 6 |
| `fichaxebot/tasks/__init__.py` | Create: empty package marker |
| `fichaxebot/tasks/marks.py` | Create: kind `mark` — handler, `schedule`, `pending`, `cancel`, auto-checkout |
| `fichaxebot/plugins.py` | Modify: optional plugin `setup(application)` hook |
| `fichaxebot/bot.py` | Modify: build/start the scheduler, daily question as a recurring job, single startup message |
| `fichaxebot/commands/{mark,cancel,pending,messages}.py` | Modify: use `marks.*` |
| `tests/scheduler_fakes.py` | Create: fake `JobQueue`, clock, app, `fire()` helper |
| `tests/test_task_scheduler.py` | Create: scheduler tests (Tasks 1–3) |
| `tests/test_marks.py` | Create: marks module tests |
| `tests/test_mark_commands.py` | Create: `/pendientes` and `/cancelar` tests |
| `tests/test_scheduler.py` | Modify in Task 6: keep only file-location and atomic-write tests |
| `tests/test_plugins.py` | Modify: setup hook tests; `scheduler_manager` → `scheduler` |
| `README.md` | Modify: restart behaviour and upgrade note |

---

### Task 1: Typed tasks, registry and lifecycle

**Files:**
- Create: `tests/scheduler_fakes.py`
- Create: `tests/test_task_scheduler.py`
- Modify: `fichaxebot/scheduler.py` (imports at the top; new code appended after `SchedulerManager`)

**Interfaces:**
- Produces:
  - `Misfire` enum: `DISCARD`, `DISCARD_AND_NOTIFY`, `RUN_LATE`
  - `Interrupted` enum: `NOTIFY`, `RETRY`
  - `ScheduledTask(id: str, kind: str, when: datetime, payload: dict, state: str = "pending")` with `to_dict()` / `from_dict(dict)`
  - `StartupReport(restored: list[ScheduledTask], missed: list[ScheduledTask], interrupted: list[ScheduledTask])`
  - `TaskScheduler(chat_id: str, *, path: Path | None = None, clock: Callable[[], datetime] = get_madrid_now)`
    - `register_kind(name, handler, *, misfire: Misfire, interrupted: Interrupted = Interrupted.NOTIFY, notify_errors: bool = False, describe: Callable[[ScheduledTask], str] | None = None) -> None`
    - `start(app) -> StartupReport` (Task 1: minimal; replaced in Task 2)
    - `schedule(kind: str, when: datetime, payload: dict | None = None) -> ScheduledTask` (the app is captured by `start`)
    - `pending(kind: str | None = None) -> list[ScheduledTask]` (state `pending`, sorted by `when`)
    - `cancel(kind: str, predicate: Callable[[ScheduledTask], bool] | None = None) -> int`
    - `describe(task: ScheduledTask) -> str`
  - Task handler signature: `async def handler(context, task: ScheduledTask) -> None`
  - Fakes: `FakeJobQueue.of(kind: "once" | "daily" | "repeating") -> list[FakeJob]`, `FakeJob.data`, `FakeJob.name`, `FakeJob.timing` (dict with `kind` plus `when` / `time`+`days` / `interval`+`first`), `Clock(now)` with mutable `.now`, `fake_app()`, `async fire(app, job)`

- [ ] **Step 1: Create the test fakes**

`tests/scheduler_fakes.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock


class FakeJob:
    def __init__(self, callback, data, name, **timing):
        self.callback = callback
        self.data = data
        self.name = name
        self.timing = timing
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    """Records jobs the way PTB's JobQueue is called; nothing runs until fire()."""

    def __init__(self):
        self.jobs = []

    def _add(self, kind, callback, data, name, **timing):
        job = FakeJob(callback, data, name, kind=kind, **timing)
        self.jobs.append(job)
        return job

    def run_once(self, callback, when, data=None, name=None, job_kwargs=None):
        return self._add("once", callback, data, name, when=when)

    def run_daily(self, callback, time, days=tuple(range(7)), data=None, name=None, job_kwargs=None):
        return self._add("daily", callback, data, name, time=time, days=days)

    def run_repeating(self, callback, interval, first=None, data=None, name=None, job_kwargs=None):
        return self._add("repeating", callback, data, name, interval=interval, first=first)

    def of(self, kind):
        return [job for job in self.jobs if job.timing["kind"] == kind and not job.removed]


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def fake_app():
    return SimpleNamespace(job_queue=FakeJobQueue(), bot=SimpleNamespace(send_message=AsyncMock()), bot_data={})


async def fire(app, job):
    """Run a recorded job the way PTB would: callback(context)."""
    await job.callback(SimpleNamespace(job=job, application=app, bot=app.bot, job_queue=app.job_queue))
```

- [ ] **Step 2: Write the failing tests**

`tests/test_task_scheduler.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_task_scheduler -v`
Expected: ERROR — `ImportError: cannot import name 'Interrupted' from 'fichaxebot.scheduler'`

- [ ] **Step 4: Replace the import block of `fichaxebot/scheduler.py`**

Replace everything above `logger = get_logger(__name__)` with:

```python
from __future__ import annotations

import copy
import inspect
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from enum import Enum
from pathlib import Path
from random import randint
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from telegram.ext import Application, ContextTypes, Job

from fichaxebot.config import CONFIG_FILE
from fichaxebot.logging_config import get_logger
from fichaxebot.storage import write_json_atomic
from fichaxebot.utils import MADRID_TZ, execute_check_in_async, get_madrid_now
```

(`randint`, `Dict`, `List`, `Application`, `ContextTypes`, `Job`, `execute_check_in_async` are still used by the legacy `SchedulerManager`, removed in Task 6.)

- [ ] **Step 5: Append the new scheduler code at the end of `fichaxebot/scheduler.py`**

```python
# --- Generic task scheduler -------------------------------------------------

FILE_VERSION = 2
PENDING = "pending"
RUNNING = "running"
_NAME = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)?")

TaskHandler = Callable[[Any, "ScheduledTask"], Awaitable[None]]
JobHandler = Callable[[Any], Awaitable[None]]


class Misfire(Enum):
    """What to do with a task whose time passed while the bot was down."""

    DISCARD = "discard"
    DISCARD_AND_NOTIFY = "discard_and_notify"
    RUN_LATE = "run_late"


class Interrupted(Enum):
    """What to do with a task that was running when the bot stopped."""

    NOTIFY = "notify"
    RETRY = "retry"


@dataclass
class ScheduledTask:
    id: str
    kind: str
    when: datetime
    payload: dict
    state: str = PENDING

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "when": self.when.astimezone(MADRID_TZ).isoformat(),
            "payload": self.payload,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledTask":
        when = datetime.fromisoformat(data["when"])
        when = when.replace(tzinfo=MADRID_TZ) if when.tzinfo is None else when.astimezone(MADRID_TZ)
        state = data.get("state", PENDING)
        if (state not in (PENDING, RUNNING) or not isinstance(data["id"], str)
                or not isinstance(data["kind"], str) or not isinstance(data["payload"], dict)):
            raise ValueError(f"invalid task entry: {data!r}")
        return cls(id=data["id"], kind=data["kind"], when=when, payload=data["payload"], state=state)


@dataclass(frozen=True)
class TaskKind:
    name: str
    handler: TaskHandler
    misfire: Misfire
    interrupted: Interrupted
    notify_errors: bool
    describe: Callable[[ScheduledTask], str]


@dataclass
class StartupReport:
    restored: list = field(default_factory=list)
    missed: list = field(default_factory=list)
    interrupted: list = field(default_factory=list)


def _default_describe(task: ScheduledTask) -> str:
    return f"{task.kind} ({task.when.astimezone(MADRID_TZ):%d/%m %H:%M})"


class TaskScheduler:
    """Typed, persisted one-shot tasks and declared recurring jobs on top of PTB's JobQueue."""

    def __init__(self, chat_id: str, *, path: Optional[Path] = None,
                 clock: Callable[[], datetime] = get_madrid_now) -> None:
        self._chat_id = chat_id
        self._path = Path(path) if path is not None else SCHEDULE_FILE
        self._clock = clock
        self._kinds: dict[str, TaskKind] = {}
        self._recurring: dict = {}
        self._tasks: dict[str, ScheduledTask] = {}
        self._jobs: dict = {}
        self._dormant: list[dict] = []
        self._last_run: dict[str, str] = {}
        self._app = None

    # Registration -----------------------------------------------------------

    def _check_registration(self, name: str, handler) -> None:
        if self._app is not None:
            raise RuntimeError("Register task kinds and jobs before start()")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"Invalid task or job name: {name!r}")
        if name in self._kinds or name in self._recurring:
            raise ValueError(f"'{name}' is already registered")
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"The handler for '{name}' must be an async function")

    def register_kind(self, name: str, handler: TaskHandler, *, misfire: Misfire,
                      interrupted: Interrupted = Interrupted.NOTIFY, notify_errors: bool = False,
                      describe: Optional[Callable[[ScheduledTask], str]] = None) -> None:
        self._check_registration(name, handler)
        self._kinds[name] = TaskKind(name, handler, misfire, interrupted, notify_errors,
                                     describe or _default_describe)

    # Lifecycle ---------------------------------------------------------------

    def start(self, app) -> StartupReport:
        if self._app is not None:
            raise RuntimeError("The scheduler is already started")
        self._app = app
        return StartupReport()

    def schedule(self, kind: str, when: datetime, payload: Optional[dict] = None) -> ScheduledTask:
        if self._app is None:
            raise RuntimeError("start() the scheduler before scheduling tasks")
        if kind not in self._kinds:
            raise ValueError(f"Unknown task kind: {kind!r}")
        payload = {} if payload is None else payload
        if not isinstance(payload, dict):
            raise TypeError("The payload must be a dict")
        json.dumps(payload)  # TypeError now rather than when persisting
        if when.tzinfo is None:
            raise ValueError("The task time must be timezone-aware")
        task = ScheduledTask(str(uuid4()), kind, when.astimezone(MADRID_TZ), copy.deepcopy(payload))
        self._tasks[task.id] = task
        self._enqueue(task, task.when)
        self._persist()
        logger.info("Scheduled %s", self.describe(task))
        return task

    def pending(self, kind: Optional[str] = None) -> list[ScheduledTask]:
        tasks = [task for task in self._tasks.values()
                 if task.state == PENDING and (kind is None or task.kind == kind)]
        return sorted(tasks, key=lambda task: task.when)

    def cancel(self, kind: str, predicate: Optional[Callable[[ScheduledTask], bool]] = None) -> int:
        doomed = [task for task in self.pending(kind) if predicate is None or predicate(task)]
        for task in doomed:
            job = self._jobs.pop(task.id, None)
            if job:
                job.schedule_removal()
            del self._tasks[task.id]
        if doomed:
            self._persist()
            logger.info("Cancelled %s task(s) of kind %s", len(doomed), kind)
        return len(doomed)

    def describe(self, task: ScheduledTask) -> str:
        kind = self._kinds.get(task.kind)
        return (kind.describe if kind else _default_describe)(task)

    # Internals ---------------------------------------------------------------

    def _enqueue(self, task: ScheduledTask, when) -> None:
        self._jobs[task.id] = self._app.job_queue.run_once(
            self._run_task, when=when, name=f"task_{task.id}", data={"id": task.id},
            job_kwargs={"misfire_grace_time": None},
        )

    async def _run_task(self, context) -> None:
        task = self._tasks.get(context.job.data["id"])
        if task is None or task.state != PENDING:
            return  # cancelled after the job was queued
        self._jobs.pop(task.id, None)
        task.state = RUNNING
        self._persist()
        kind = self._kinds[task.kind]
        try:
            await kind.handler(context, task)
        except Exception as exc:  # noqa: BLE001 - a failing task must not stop the scheduler
            logger.exception("Scheduled task %s failed", self.describe(task))
            if kind.notify_errors:
                await self._notify(context.bot, f"❌ Falló la tarea programada {self.describe(task)}: {exc}")
        # CancelledError (shutdown) propagates above and leaves the task saved as running.
        self._tasks.pop(task.id, None)
        self._persist()

    async def _notify(self, bot, text: str) -> None:
        try:
            await bot.send_message(chat_id=self._chat_id, text=text)
        except Exception:  # noqa: BLE001
            logger.exception("Could not send scheduler notification")

    def _persist(self) -> None:
        tasks = [task.to_dict() for task in sorted(self._tasks.values(), key=lambda task: task.when)]
        write_json_atomic(self._path, {
            "version": FILE_VERSION,
            "tasks": tasks + self._dormant,
            "recurring": dict(sorted(self._last_run.items())),
        })
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_task_scheduler -v`
Expected: all tests in `TaskLifecycleTests`, `PendingAndCancelTests`, `ScheduledTaskTests` PASS.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m unittest discover -s tests -p "test_*.py"`
Expected: only the pre-existing `test_fichador_login` import error.

- [ ] **Step 8: Commit** (only with user approval)

```bash
git add fichaxebot/scheduler.py tests/scheduler_fakes.py tests/test_task_scheduler.py
git commit -m "feat: add typed task scheduler with running-state lifecycle"
```

---

### Task 2: Restore on startup and the startup message

**Files:**
- Modify: `fichaxebot/scheduler.py` (replace `TaskScheduler.start`; add `_read_file`, `startup_message`)
- Test: `tests/test_task_scheduler.py` (append `RestoreTests`)

**Interfaces:**
- Consumes: Task 1 (`TaskScheduler`, `ScheduledTask`, `StartupReport`, `Misfire`, `Interrupted`, `_enqueue`, `_persist`)
- Produces:
  - `TaskScheduler.start(app) -> StartupReport` — restores tasks per the spec's restore table and persists
  - `TaskScheduler.startup_message(report: StartupReport) -> Optional[str]` — `None` when the report is empty

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_task_scheduler.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_task_scheduler.RestoreTests -v`
Expected: FAIL/ERROR — restored lists are empty and `startup_message` does not exist.

- [ ] **Step 3: Replace `TaskScheduler.start` and add `_read_file` and `startup_message`**

In `fichaxebot/scheduler.py`, replace the Task 1 `start` method with:

```python
    def start(self, app) -> StartupReport:
        """Restore saved tasks, then persist. Call after app.start(), once all kinds are registered."""
        if self._app is not None:
            raise RuntimeError("The scheduler is already started")
        self._app = app
        data = self._read_file()
        self._last_run = {name: day for name, day in data["recurring"].items()
                          if isinstance(name, str) and isinstance(day, str)}
        now = self._clock()
        report = StartupReport()
        for raw in data["tasks"]:
            try:
                task = ScheduledTask.from_dict(raw)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Discarding invalid scheduled task %r: %s", raw, exc)
                continue
            kind = self._kinds.get(task.kind)
            if kind is None:
                logger.warning("Keeping task %s of unregistered kind %r without scheduling it", task.id, task.kind)
                self._dormant.append(raw)
                continue
            if task.state == RUNNING:
                if kind.interrupted is Interrupted.RETRY:
                    task.state = PENDING
                    self._tasks[task.id] = task
                    self._enqueue(task, 0)
                else:
                    report.interrupted.append(task)
                continue
            if task.when <= now:
                if kind.misfire is Misfire.RUN_LATE:
                    self._tasks[task.id] = task
                    self._enqueue(task, 0)
                elif kind.misfire is Misfire.DISCARD_AND_NOTIFY:
                    report.missed.append(task)
                else:
                    logger.info("Discarding overdue task %s", self.describe(task))
                continue
            self._tasks[task.id] = task
            self._enqueue(task, task.when)
            report.restored.append(task)
        self._persist()
        return report

    def startup_message(self, report: StartupReport) -> Optional[str]:
        def lines(tasks):
            return "\n".join(f"• {self.describe(task)}" for task in tasks)

        sections = []
        if report.restored:
            sections.append("Tareas programadas restauradas:\n" + lines(report.restored))
        if report.missed:
            sections.append("No realizadas mientras el bot estaba apagado:\n" + lines(report.missed))
        if report.interrupted:
            sections.append("⚠️ Interrumpidas; comprueba USC antes de repetirlas:\n" + lines(report.interrupted))
        if not sections:
            return None
        return "♻️ Bot reiniciado.\n\n" + "\n\n".join(sections)

    def _read_file(self) -> dict:
        empty = {"tasks": [], "recurring": {}}
        if not self._path.exists():
            return empty
        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw:
            return empty
        try:
            data = json.loads(raw)
            if (not isinstance(data, dict) or data.get("version") != FILE_VERSION
                    or not isinstance(data.get("tasks"), list)
                    or not isinstance(data.get("recurring", {}), dict)):
                raise ValueError("unsupported format")
        except ValueError:  # includes json.JSONDecodeError
            backup = self._path.with_name(self._path.name + ".corrupt")
            logger.warning("Invalid format in %s. Moved to %s and ignored.", self._path, backup)
            self._path.replace(backup)
            return empty
        return {"tasks": data["tasks"], "recurring": data.get("recurring", {})}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_task_scheduler -v`
Expected: all PASS (Task 1 tests included).

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add fichaxebot/scheduler.py tests/test_task_scheduler.py
git commit -m "feat: restore scheduled tasks with misfire and interrupted policies"
```

---

### Task 3: Declared recurring jobs with catch-up

**Files:**
- Modify: `fichaxebot/scheduler.py` (add `RecurringJob`, `register_daily`, `register_interval`, `_start_recurring`, `_run_recurring`; call `_start_recurring` from `start`)
- Test: `tests/test_task_scheduler.py` (append `RecurringTests`)

**Interfaces:**
- Consumes: Task 2 (`start` loads `self._last_run`), Task 1 (`_check_registration`, `_persist`, `_notify`)
- Produces:
  - `register_daily(name, handler, *, at: datetime.time, weekdays=range(7), catch_up: bool = False, notify_errors: bool = False)` — `at` is a naive Madrid time; `weekdays` uses 0 = Monday
  - `register_interval(name, handler, *, every: timedelta, notify_errors: bool = False)`
  - Recurring handler signature: `async def handler(context) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_task_scheduler.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_task_scheduler.RecurringTests -v`
Expected: ERROR — `AttributeError: 'TaskScheduler' object has no attribute 'register_daily'`

- [ ] **Step 3: Add `RecurringJob` above `class TaskScheduler`**

```python
@dataclass(frozen=True)
class RecurringJob:
    name: str
    handler: JobHandler
    at: Optional[dtime] = None
    weekdays: tuple = tuple(range(7))
    every: Optional[timedelta] = None
    catch_up: bool = False
    notify_errors: bool = False
```

- [ ] **Step 4: Add the registration methods after `register_kind`**

```python
    def register_daily(self, name: str, handler: JobHandler, *, at: dtime, weekdays=range(7),
                       catch_up: bool = False, notify_errors: bool = False) -> None:
        self._check_registration(name, handler)
        if at.tzinfo is not None:
            raise ValueError("'at' must be a naive time; it is interpreted in Europe/Madrid")
        days = tuple(sorted(set(weekdays)))
        if not days or any(day not in range(7) for day in days):
            raise ValueError("'weekdays' must contain values from 0 (Monday) to 6 (Sunday)")
        self._recurring[name] = RecurringJob(name, handler, at=at, weekdays=days,
                                             catch_up=catch_up, notify_errors=notify_errors)

    def register_interval(self, name: str, handler: JobHandler, *, every: timedelta,
                          notify_errors: bool = False) -> None:
        self._check_registration(name, handler)
        if every <= timedelta(0):
            raise ValueError("'every' must be a positive interval")
        self._recurring[name] = RecurringJob(name, handler, every=every, notify_errors=notify_errors)
```

- [ ] **Step 5: Add the recurring internals after `_run_task`**

```python
    def _start_recurring(self, now: datetime) -> None:
        queue = self._app.job_queue
        for job in self._recurring.values():
            data = {"name": job.name}
            if job.every is not None:
                seconds = job.every.total_seconds()
                queue.run_repeating(self._run_recurring, interval=seconds, first=seconds, name=job.name, data=data)
                continue
            # PTB numbers days from 0 = Sunday; ours use Python's 0 = Monday.
            queue.run_daily(self._run_recurring, time=job.at.replace(tzinfo=MADRID_TZ),
                            days=tuple((day + 1) % 7 for day in job.weekdays), name=job.name, data=data)
            if (job.catch_up and now.weekday() in job.weekdays and now.time() >= job.at
                    and self._last_run.get(job.name) != now.date().isoformat()):
                logger.info("Catching up recurring job %s", job.name)
                queue.run_once(self._run_recurring, when=0, name=f"{job.name}_catch_up", data=data)

    async def _run_recurring(self, context) -> None:
        job = self._recurring[context.job.data["name"]]
        if job.at is not None:
            # Recorded before running so a restart mid-run does not repeat today's run.
            self._last_run[job.name] = self._clock().date().isoformat()
            self._persist()
        try:
            await job.handler(context)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Recurring job %s failed", job.name)
            if job.notify_errors:
                await self._notify(context.bot, f"❌ Falló la tarea periódica {job.name}: {exc}")
```

- [ ] **Step 6: Call `_start_recurring` from `start`**

In `TaskScheduler.start`, replace the final two lines:

```python
        self._persist()
        return report
```

with:

```python
        self._start_recurring(now)
        self._persist()
        return report
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_task_scheduler -v`
Expected: all PASS.

- [ ] **Step 8: Commit** (only with user approval)

```bash
git add fichaxebot/scheduler.py tests/test_task_scheduler.py
git commit -m "feat: add declared recurring jobs with once-a-day catch-up"
```

---

### Task 4: Marks as an ordinary task kind

**Files:**
- Create: `fichaxebot/tasks/__init__.py` (empty)
- Create: `fichaxebot/tasks/marks.py`
- Test: `tests/test_marks.py`

**Interfaces:**
- Consumes: `TaskScheduler.register_kind/schedule/pending/cancel`, `ScheduledTask`, `Misfire`, `Interrupted`; `app.scheduler` is the application's `TaskScheduler` (set in Task 6)
- Produces (module `fichaxebot.tasks.marks`):
  - `KIND = "mark"`
  - `register(scheduler: TaskScheduler) -> None`
  - `describe(task: ScheduledTask) -> str` → e.g. `"Entrada el 01/10 09:00"`
  - `schedule(app, action: str, when: datetime) -> ScheduledTask` — `ValueError` for unknown action or past time (`"La hora indicada ya ha pasado"`)
  - `pending(app) -> list[ScheduledTask]`
  - `cancel(app, action: str | None = None) -> int`
  - `compute_auto_checkout_time(now, delay: timedelta, random_offset_minutes: int, rand=randint) -> datetime`
  - `schedule_auto_checkout(app) -> ScheduledTask` — `ValueError` if `auto_checkout_delay` is not configured
  - `async run_mark(context, task) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_marks.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_marks -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'fichaxebot.tasks'`

- [ ] **Step 3: Create the package and module**

`fichaxebot/tasks/__init__.py`: empty file.

`fichaxebot/tasks/marks.py`:

```python
"""Clock-in/out marks as an ordinary task kind of the core scheduler."""

from __future__ import annotations

from datetime import datetime, timedelta
from random import randint
from typing import Callable, Optional

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scheduler import Interrupted, Misfire, ScheduledTask, TaskScheduler
from fichaxebot.utils import MADRID_TZ, execute_check_in_async, get_madrid_now

logger = get_logger(__name__)

KIND = "mark"
ACTIONS = ("entrada", "salida")


def register(scheduler: TaskScheduler) -> None:
    # A mark missed while the bot was down must not run late, and one cut off mid-run
    # may already be recorded in USC: report both, never repeat them.
    scheduler.register_kind(KIND, run_mark, misfire=Misfire.DISCARD_AND_NOTIFY,
                            interrupted=Interrupted.NOTIFY, describe=describe)


def describe(task: ScheduledTask) -> str:
    return f"{task.payload['action'].capitalize()} el {task.when.astimezone(MADRID_TZ):%d/%m %H:%M}"


def schedule(app, action: str, when: datetime) -> ScheduledTask:
    if action not in ACTIONS:
        raise ValueError("La acción de fichaje debe ser 'entrada' o 'salida'.")
    if when <= get_madrid_now():
        raise ValueError("La hora indicada ya ha pasado")
    return app.scheduler.schedule(KIND, when, {"action": action})


def pending(app) -> list[ScheduledTask]:
    return app.scheduler.pending(KIND)


def cancel(app, action: Optional[str] = None) -> int:
    if action is None:
        return app.scheduler.cancel(KIND)
    return app.scheduler.cancel(KIND, lambda task: task.payload["action"] == action)


def compute_auto_checkout_time(now: datetime, delay: timedelta, random_offset_minutes: int,
                               rand: Callable[[int, int], int] = randint) -> datetime:
    exit_time = now + delay
    limit = max(0, random_offset_minutes)
    if limit:
        offset = rand(-limit, limit)
        if offset:
            logger.info("Applying random offset of %s minutes to auto-checkout", offset)
        exit_time += timedelta(minutes=offset)
    if exit_time <= now:
        logger.info("Computed auto-checkout time %s is not in the future. Adjusting by one minute.",
                    exit_time.isoformat())
        exit_time = now + timedelta(minutes=1)
    return exit_time


def schedule_auto_checkout(app) -> ScheduledTask:
    config = get_config()
    if not config.auto_checkout_delay:
        raise ValueError("La salida automática no está configurada")
    when = compute_auto_checkout_time(get_madrid_now(), config.auto_checkout_delay,
                                      config.auto_checkout_random_offset_minutes)
    return schedule(app, "salida", when)


async def run_mark(context, task: ScheduledTask) -> None:
    action = task.payload["action"]
    chat_id = get_config().telegram_chat_id
    logger.info("Executing scheduled mark %s (%s)", task.id, action)
    result = await execute_check_in_async(action, context.application.web_session, context)

    prefix = "🚪" if action == "entrada" else "🏁"
    await context.bot.send_message(chat_id=chat_id, text=f"{prefix} Marcaje programado de {action} ejecutado.")
    await context.bot.send_message(chat_id=chat_id, text=result.message)

    if action == "entrada" and result.success and get_config().auto_checkout_delay:
        try:
            auto_task = schedule_auto_checkout(context.application)
        except ValueError:
            await context.bot.send_message(
                chat_id=chat_id, text="⚠️ No se programó la salida porque la hora calculada ya no es válida.",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🕐 Salida automática programada para las {}.".format(auto_task.when.strftime("%H:%M")),
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_marks -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add fichaxebot/tasks/__init__.py fichaxebot/tasks/marks.py tests/test_marks.py
git commit -m "feat: implement marks as a scheduler task kind"
```

---

### Task 5: Plugin `setup(application)` hook

**Files:**
- Modify: `fichaxebot/plugins.py`
- Test: `tests/test_plugins.py` (append to `PluginLoaderTests`)

**Interfaces:**
- Produces: plugins may export `setup(application) -> None`, called once after all plugin command handlers are added. Plugins use it to call `application.scheduler.register_kind/register_daily/register_interval`. A non-callable `setup` or an exception inside it raises `ValueError("Plugin '<name>': ...")`.

- [ ] **Step 1: Write the failing tests**

Append inside `class PluginLoaderTests` in `tests/test_plugins.py`:

```python
    async def test_setup_hook_runs_after_commands_are_registered(self):
        self.plugin("feature", """
            async def run(update, context):
                pass
            COMMANDS = {"hello": run}
            def setup(application):
                application.bot_data["setup"] = [
                    handler.commands for handlers in application.handlers.values()
                    for handler in handlers if hasattr(handler, "commands")
                ]
        """)
        register_plugins(self.app, ["feature"])
        self.assertEqual(self.app.bot_data["setup"], [frozenset({"hello"})])

    async def test_setup_must_be_callable_and_its_errors_name_the_plugin(self):
        self.plugin("bad_setup", "COMMANDS = {}\nsetup = 1\n")
        with self.assertRaisesRegex(ValueError, "bad_setup.*setup"):
            register_plugins(self.app, ["bad_setup"])
        self.plugin("failing_setup", 'COMMANDS = {}\ndef setup(application):\n    raise RuntimeError("no config")\n')
        with self.assertRaisesRegex(ValueError, "failing_setup.*no config"):
            register_plugins(self.app, ["failing_setup"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_plugins -v`
Expected: the two new tests FAIL (`KeyError: 'setup'` and "ValueError not raised").

- [ ] **Step 3: Implement the hook in `fichaxebot/plugins.py`**

Replace the docstring of `register_plugins` and add `setups` handling so the function reads:

```python
def register_plugins(application: Application, names: Sequence[str]) -> None:
    """Register COMMANDS from configured plugins, or fail before adding any handlers.

    Names are direct package names validated by load_config. Plugins export a
    mapping of command names (without '/') to async (update, context) callbacks,
    and may export setup(application), called after all commands are registered,
    to register scheduler task kinds and recurring jobs.
    """
    used_commands = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    pending = []
    setups = []
    for name in names:
        try:
            plugin = importlib.import_module(f"plugins.{name}")
            if not hasattr(plugin, "__path__"):
                raise ValueError("expected a plugin package containing __init__.py")
            commands = getattr(plugin, "COMMANDS", None)
            if not isinstance(commands, Mapping):
                raise ValueError("COMMANDS must map command names to async callbacks")

            for command, callback in commands.items():
                if not isinstance(command, str) or not re.fullmatch(
                    r"[a-zA-Z0-9_]{1,32}", command
                ):
                    raise ValueError(f"invalid command name: {command!r}")
                command = command.lower()
                if command in used_commands:
                    raise ValueError(f"command /{command} is already registered")
                if not inspect.iscoroutinefunction(callback):
                    raise ValueError(f"callback for /{command} must be an async function")
                pending.append(CommandHandler(command, callback))
                used_commands.add(command)

            setup = getattr(plugin, "setup", None)
            if setup is not None:
                if not callable(setup):
                    raise ValueError("setup must be a function taking the application")
                setups.append((name, setup))
        except Exception as exc:
            raise ValueError(f"Plugin '{name}': {exc}") from exc

    for handler in pending:
        application.add_handler(handler)
    for name, setup in setups:
        try:
            setup(application)
        except Exception as exc:
            raise ValueError(f"Plugin '{name}': setup failed: {exc}") from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_plugins -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add fichaxebot/plugins.py tests/test_plugins.py
git commit -m "feat: let plugins register scheduler work through a setup hook"
```

---

### Task 6: Switch the bot to the new scheduler and remove `SchedulerManager`

**Files:**
- Test: `tests/test_mark_commands.py` (create)
- Modify: `fichaxebot/commands/mark.py`, `fichaxebot/commands/cancel.py`, `fichaxebot/commands/pending.py`, `fichaxebot/commands/messages.py`, `fichaxebot/bot.py`, `fichaxebot/scheduler.py`, `tests/test_scheduler.py`, `tests/test_plugins.py`

**Interfaces:**
- Consumes: `fichaxebot.tasks.marks` (Task 4), `TaskScheduler.register_daily/start/startup_message` (Tasks 2–3), plugin `setup` (Task 5)
- Produces: `application.scheduler` (a started `TaskScheduler`); `application.scheduler_manager` no longer exists.

- [ ] **Step 1: Write the failing command tests**

`tests/test_mark_commands.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_mark_commands -v`
Expected: ERROR — `AttributeError: 'types.SimpleNamespace' object has no attribute 'scheduler_manager'`

- [ ] **Step 3: Rewrite `fichaxebot/commands/cancel.py`**

```python
from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.tasks import marks


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    if not marks.pending(context.application):
        await update.message.reply_text("No hay marcajes programados actualmente.")
        return

    marks.cancel(context.application)
    await update.message.reply_text("🗓️ Todos los marcajes programados han sido cancelados.")
```

- [ ] **Step 4: Rewrite `fichaxebot/commands/pending.py`**

```python
from telegram import Update
from telegram.ext import ContextTypes

from fichaxebot.tasks import marks
from fichaxebot.utils import MADRID_TZ


async def show_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    pending = marks.pending(context.application)
    if not pending:
        await update.message.reply_text("No hay marcajes programados en el scheduler.")
        return

    lines = []
    for task in pending:
        when = task.when.astimezone(MADRID_TZ)
        lines.append(f"• {task.payload['action'].capitalize()} el {when.strftime('%d/%m a las %H:%M')}")

    await update.message.reply_text("Marcajes pendientes:\n" + "\n".join(lines))
```

- [ ] **Step 5: Update `fichaxebot/commands/mark.py`**

- Replace `from fichaxebot.scheduler import SchedulerManager` with `from fichaxebot.tasks import marks`.
- Delete the line `scheduler_manager: SchedulerManager = context.application.scheduler_manager`.
- Replace `scheduler_manager.schedule(context.application, action, scheduled_time)` with `marks.schedule(context.application, action, scheduled_time)`.
- Replace `scheduler_manager.schedule_auto_checkout(\n                        context.application\n                    )` with `marks.schedule_auto_checkout(context.application)`.
- Replace `scheduler_manager.cancel_by_action("salida")` with `marks.cancel(context.application, "salida")`.

- [ ] **Step 6: Update `fichaxebot/commands/messages.py`**

- Replace `from fichaxebot.scheduler import SchedulerManager` with `from fichaxebot.tasks import marks`.
- Delete the line `scheduler_manager: SchedulerManager = context.application.scheduler_manager`.
- Replace `if scheduler_manager.has_pending():` with `if marks.pending(context.application):`.
- Replace `scheduler_manager.schedule_auto_checkout(\n                        context.application\n                    )` with `marks.schedule_auto_checkout(context.application)`.

- [ ] **Step 7: Run the command tests**

Run: `.venv/bin/python -m unittest tests.test_mark_commands -v`
Expected: all PASS.

- [ ] **Step 8: Update `fichaxebot/bot.py`**

1. Imports: replace `from fichaxebot.scheduler import SchedulerManager` with:

```python
from fichaxebot.scheduler import TaskScheduler
from fichaxebot.tasks import marks
```

2. In `ask_for_check_in`, replace:

```python
    scheduler_manager: SchedulerManager = context.application.scheduler_manager
    if scheduler_manager.has_pending():
```

with:

```python
    if marks.pending(context.application):
```

3. In `_run_bot`, replace:

```python
    scheduler_manager = SchedulerManager(
        appconfig.telegram_chat_id,
        appconfig.auto_checkout_delay,
        appconfig.auto_checkout_random_offset_minutes,
    )
```

with:

```python
    scheduler = TaskScheduler(appconfig.telegram_chat_id)
    marks.register(scheduler)
    scheduler.register_daily(
        "daily_question", ask_for_check_in,
        at=QUESTION_TIME, weekdays=range(5), catch_up=True,
    )
```

and replace `app.scheduler_manager = scheduler_manager` with `app.scheduler = scheduler`.

4. Delete the `app.job_queue.run_daily(ask_for_check_in, ...)` block, the line `restaurados = scheduler_manager.load_from_disk(app)`, and the whole block from `now = get_madrid_now()` / `question_time = now.replace(` through its `else: logger.info("🤖 Bot started. Waiting for question schedule.")`.

5. Delete the `if restaurados:` block after `await app.start()` and put in its place:

```python
    report = scheduler.start(app)
    startup_message = scheduler.startup_message(report)
    if startup_message:
        await app.bot.send_message(chat_id=CHAT_ID, text=startup_message)
    logger.info("🤖 Bot started.")
```

6. Run `grep -n "MADRID_TZ\|get_madrid_now\|is_galicia_holiday" fichaxebot/bot.py` and remove any of these names from the `fichaxebot.utils` import that no longer appear outside the import line.

- [ ] **Step 9: Remove the legacy classes from `fichaxebot/scheduler.py`**

Delete `class ScheduledMark` and `class SchedulerManager` entirely, and replace the import block with:

```python
from __future__ import annotations

import copy
import inspect
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from fichaxebot.config import CONFIG_FILE
from fichaxebot.logging_config import get_logger
from fichaxebot.storage import write_json_atomic
from fichaxebot.utils import MADRID_TZ, get_madrid_now
```

Keep `logger = get_logger(__name__)` and the `SCHEDULE_FILE = CONFIG_FILE.parent / ".schedule.data"` definition with its comment.

- [ ] **Step 10: Rewrite `tests/test_scheduler.py` to the tests that still apply**

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fichaxebot import config, scheduler, storage


class ScheduleFileLocationTests(unittest.TestCase):
    def test_schedule_file_lives_next_to_config_not_in_the_working_directory(self):
        self.assertTrue(scheduler.SCHEDULE_FILE.is_absolute())
        self.assertEqual(scheduler.SCHEDULE_FILE, config.CONFIG_FILE.parent / ".schedule.data")


class AtomicJsonTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "data.json"

    def test_writes_json(self):
        storage.write_json_atomic(self.path, [{"a": 1}])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"a": 1}])

    def test_failed_write_keeps_previous_content_and_leaves_no_temp_file(self):
        self.path.write_text('["previous"]', encoding="utf-8")
        with patch("fichaxebot.storage.os.replace", side_effect=OSError("disk full")), \
             self.assertRaises(OSError):
            storage.write_json_atomic(self.path, ["new"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), '["previous"]')
        self.assertEqual(sorted(p.name for p in Path(self.dir.name).iterdir()), ["data.json"])
```

- [ ] **Step 11: Update `tests/test_plugins.py`**

Replace every `scheduler_manager` with `scheduler` (three places: `self.app.scheduler_manager = object()`, the plugin source `context.application.scheduler_manager`, and the assertion `self.app.scheduler_manager`).

- [ ] **Step 12: Check nothing still references the old API**

Run: `grep -rn "scheduler_manager\|SchedulerManager\|ScheduledMark\|load_from_disk\|has_pending\|cancel_by_action\|list_pending" fichaxebot plugins tests --include=*.py`
Expected: no output.

- [ ] **Step 13: Run the whole suite and an import check**

Run: `.venv/bin/python -m unittest discover -s tests -p "test_*.py" && .venv/bin/python -c "import fichaxebot.bot"`
Expected: only the pre-existing `test_fichador_login` import error; the import succeeds.

- [ ] **Step 14: Commit** (only with user approval)

```bash
git add fichaxebot/bot.py fichaxebot/scheduler.py fichaxebot/commands/mark.py fichaxebot/commands/cancel.py \
        fichaxebot/commands/pending.py fichaxebot/commands/messages.py \
        tests/test_mark_commands.py tests/test_scheduler.py tests/test_plugins.py
git commit -m "refactor: run marks and the daily question on the task scheduler"
```

---

### Task 7: Documentation and end-to-end check

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-25-task-scheduler-design.md` (status line)

- [ ] **Step 1: Document restart behaviour in `README.md`**

After the paragraph starting "Read-only mode runs each procedure up to the final USC action", add:

```markdown
Scheduled marks survive restarts. Marks whose time passed while the bot was off are not executed; the startup message lists them, together with any mark that was interrupted mid-run (check USC before repeating it). The daily question is asked at most once per day, also after a restart. **Upgrading from a version before the task scheduler:** pending marks are not carried over — the old `.schedule.data` is moved to `.schedule.data.corrupt`; note and re-create them after deploying.
```

- [ ] **Step 2: Mark the spec as implemented**

In `docs/superpowers/specs/2026-09-25-task-scheduler-design.md`, replace the status sentence
`Status: **design approved 2026-09-25**, pending review of this written spec.` with
`Status: **implemented** (plan: docs/superpowers/plans/2026-09-25-task-scheduler.md).`

- [ ] **Step 3: Full verification**

Run: `.venv/bin/python -m unittest discover -s tests -p "test_*.py"`
Expected: only the pre-existing `test_fichador_login` import error.

- [ ] **Step 4: Manual check with the real bot** (the user runs it; it uses the real Telegram token)

1. Before deploying, note pending marks (`/pendientes`); they will not be restored.
2. Start the bot: `.venv/bin/python -m fichaxebot.bot`. Expect no "♻️" message on the first start (empty schedule) and, if started after 09:00 on a working day, exactly one daily question.
3. `/marcar salida HH:MM` for a time a few minutes ahead, then `/pendientes`: it is listed.
4. Stop and restart the bot before that time: the startup message lists it under "Tareas programadas restauradas", and no second daily question is sent.
5. Stop the bot, wait until after that time, start it: the message lists it under "No realizadas mientras el bot estaba apagado"; it is not executed.

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add README.md docs/superpowers/specs/2026-09-25-task-scheduler-design.md
git commit -m "docs: describe scheduler restart behaviour"
```
