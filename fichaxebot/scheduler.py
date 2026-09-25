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

logger = get_logger(__name__)

# Anchored next to config.json so the working directory does not decide which file is used.
SCHEDULE_FILE = CONFIG_FILE.parent / ".schedule.data"


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


@dataclass(frozen=True)
class RecurringJob:
    name: str
    handler: JobHandler
    at: Optional[dtime] = None
    weekdays: tuple = tuple(range(7))
    every: Optional[timedelta] = None
    catch_up: bool = False
    notify_errors: bool = False


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

    # Lifecycle ---------------------------------------------------------------

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
        self._start_recurring(now)
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
