from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from random import randint
from typing import Dict, List, Optional
from uuid import uuid4

from telegram.ext import Application, ContextTypes, Job

from fichaxebot.utils import MADRID_TZ, execute_check_in_async, get_madrid_now
from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)

SCHEDULE_FILE = Path(".schedule.data")


@dataclass
class ScheduledMark:
    identifier: str
    action: str
    when: datetime

    def to_dict(self) -> Dict[str, str]:
        return {
            "id": self.identifier,
            "action": self.action,
            "when": self.when.astimezone(MADRID_TZ).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ScheduledMark":
        when = datetime.fromisoformat(data["when"])
        if when.tzinfo is None:
            when = MADRID_TZ.localize(when)
        else:
            when = when.astimezone(MADRID_TZ)
        return cls(identifier=data["id"], action=data["action"], when=when)


class SchedulerManager:
    def __init__(
        self,
        chat_id: str,
        auto_checkout_delay: Optional[timedelta],
        auto_checkout_random_offset_minutes: int,
    ) -> None:
        self._scheduled: Dict[str, ScheduledMark] = {}
        self._jobs: Dict[str, Job] = {}
        self._chat_id = chat_id
        self._auto_checkout_delay = auto_checkout_delay
        self._auto_checkout_random_offset = max(0, auto_checkout_random_offset_minutes)

    @staticmethod
    def create_mark(action: str, when: datetime) -> ScheduledMark:
        normalized_when = when.astimezone(MADRID_TZ)
        return ScheduledMark(identifier=str(uuid4()), action=action, when=normalized_when)

    def add_mark(self, app: Application, mark: ScheduledMark) -> None:
        job = app.job_queue.run_once(
            self.execute_job,
            when=mark.when,
            name=f"marcaje_{mark.identifier}",
            data={"id": mark.identifier},
            job_kwargs={"misfire_grace_time": None},
        )
        self._scheduled[mark.identifier] = mark
        self._jobs[mark.identifier] = job
        self._persist()
        logger.info("Scheduled mark: %s at %s", mark.action, mark.when.isoformat())

    def schedule(self, app: Application, action: str, when: datetime) -> ScheduledMark:
        if when <= get_madrid_now():
            raise ValueError("La hora indicada ya ha pasado")
        mark = self.create_mark(action, when)
        self.add_mark(app, mark)
        return mark

    def _compute_auto_checkout_time(self) -> datetime:
        if not self._auto_checkout_delay:
            raise ValueError("La salida automática no está configurada")

        now = get_madrid_now()
        exit_time = now + self._auto_checkout_delay

        if self._auto_checkout_random_offset:
            offset = randint(
                -self._auto_checkout_random_offset, self._auto_checkout_random_offset
            )
            if offset:
                logger.info("Applying random offset of %s minutes to auto-checkout", offset)
            exit_time += timedelta(minutes=offset)

        if exit_time <= now:
            logger.info(
                "Computed auto-checkout time %s is not in the future. Adjusting by one minute.",
                exit_time.isoformat(),
            )
            exit_time = now + timedelta(minutes=1)

        return exit_time

    def schedule_auto_checkout(self, app: Application) -> ScheduledMark:
        exit_time = self._compute_auto_checkout_time()
        return self.schedule(app, "salida", exit_time)

    def has_pending(self) -> bool:
        return bool(self._scheduled)

    def list_pending(self) -> List[ScheduledMark]:
        return sorted(self._scheduled.values(), key=lambda mark: mark.when)

    def cancel_all(self) -> None:
        if self._scheduled:
            logger.info("Cancelling %s scheduled marks", len(self._scheduled))
        self._jobs.clear()
        self._scheduled.clear()
        self._persist()

    def cancel_by_action(self, action: str) -> int:
        identifiers = [mark_id for mark_id, mark in self._scheduled.items() if mark.action == action]
        for identifier in identifiers:
            job = self._jobs.pop(identifier, None)
            self._scheduled.pop(identifier, None)
        if identifiers:
            self._persist()
        return len(identifiers)

    def _persist(self) -> None:
        data = [mark.to_dict() for mark in self.list_pending()]
        SCHEDULE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_from_disk(self, app: Application) -> List[ScheduledMark]:
        if not SCHEDULE_FILE.exists():
            return []

        raw = SCHEDULE_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid format in %s. Content will be ignored.", SCHEDULE_FILE)
            SCHEDULE_FILE.write_text("[]", encoding="utf-8")
            return []

        restored: List[ScheduledMark] = []
        now = get_madrid_now()
        for item in data:
            try:
                mark = ScheduledMark.from_dict(item)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Invalid entry in scheduling data: %s", exc)
                continue
            if mark.when <= now:
                logger.info(
                    "Expired scheduled mark (%s at %s). Discarding.",
                    mark.action,
                    mark.when.isoformat(),
                )
                continue
            self.add_mark(app, mark)
            restored.append(mark)
        if restored:
            logger.info("Restored %s pending marks", len(restored))

        return restored

    async def execute_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        job_data = context.job.data if context.job else {}
        identifier = job_data.get("id") if job_data else None
        if not identifier:
            logger.error("Scheduled job without identifier")
            return

        mark = self._scheduled.pop(identifier, None)
        job = self._jobs.pop(identifier, None)
        self._persist()

        if not mark:
            logger.warning("Scheduled mark %s not found when executing the job", identifier)
            return

        logger.info("Executing scheduled mark %s (%s)", identifier, mark.action)
        session = context.application.web_session
        resultado = await execute_check_in_async(mark.action, session, context)

        prefix = "🚪" if mark.action == "entrada" else "🏁"
        await context.bot.send_message(
            chat_id=self._chat_id,
            text=f"{prefix} Marcaje programado de {mark.action} ejecutado.",
        )
        await context.bot.send_message(chat_id=self._chat_id, text=resultado.message)

        if mark.action == "entrada" and resultado.success and self._auto_checkout_delay:
            try:
                auto_mark = self.schedule_auto_checkout(context.application)
            except ValueError:
                await context.bot.send_message(
                    chat_id=self._chat_id,
                    text="⚠️ No se programó la salida porque la hora calculada ya no es válida.",
                )
            else:
                await context.bot.send_message(
                    chat_id=self._chat_id,
                    text="🕐 Salida automática programada para las {}.".format(
                        auto_mark.when.strftime("%H:%M")
                    ),
                )
