from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from telegram.ext import Application, ContextTypes, Job

from config import get_config
from logging_config import get_logger
from core import MADRID_TZ, ahora_madrid, ejecutar_fichaje_async

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
            when = when.replace(tzinfo=MADRID_TZ)
        else:
            when = when.astimezone(MADRID_TZ)
        return cls(identifier=data["id"], action=data["action"], when=when)


class SchedulerManager:
    def __init__(self) -> None:
        self._scheduled: Dict[str, ScheduledMark] = {}
        self._jobs: Dict[str, Job] = {}
        self._chat_id: Optional[str] = None

    def configure(self) -> None:
        config = get_config()
        self._chat_id = config.telegram_chat_id

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
        )
        self._scheduled[mark.identifier] = mark
        self._jobs[mark.identifier] = job
        self._persist()
        logger.info("Marcaje programado: %s a las %s", mark.action, mark.when.isoformat())

    def schedule(self, app: Application, action: str, when: datetime) -> ScheduledMark:
        if when <= ahora_madrid():
            raise ValueError("La hora indicada ya ha pasado")
        mark = self.create_mark(action, when)
        self.add_mark(app, mark)
        return mark

    def has_pending(self) -> bool:
        return bool(self._scheduled)

    def list_pending(self) -> List[ScheduledMark]:
        return sorted(self._scheduled.values(), key=lambda mark: mark.when)

    def cancel_all(self) -> None:
        for job in self._jobs.values():
            job.schedule_removal()
        if self._scheduled:
            logger.info("Se cancelan %s marcajes programados", len(self._scheduled))
        self._jobs.clear()
        self._scheduled.clear()
        self._persist()

    def cancel_by_action(self, action: str) -> int:
        identifiers = [mark_id for mark_id, mark in self._scheduled.items() if mark.action == action]
        for identifier in identifiers:
            job = self._jobs.pop(identifier, None)
            if job:
                job.schedule_removal()
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
            logger.warning("Formato inválido en %s. Se ignorará el contenido.", SCHEDULE_FILE)
            SCHEDULE_FILE.write_text("[]", encoding="utf-8")
            return []

        restored: List[ScheduledMark] = []
        now = ahora_madrid()
        for item in data:
            try:
                mark = ScheduledMark.from_dict(item)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Entrada inválida en programación: %s", exc)
                continue
            if mark.when <= now:
                logger.info(
                    "Marcaje programado caducado (%s a las %s). Se descarta.",
                    mark.action,
                    mark.when.isoformat(),
                )
                continue
            self.add_mark(app, mark)
            restored.append(mark)
        if restored:
            logger.info("Se restauraron %s marcajes pendientes", len(restored))
        else:
            self._persist()
        return restored

    async def execute_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        job_data = context.job.data if context.job else {}
        identifier = job_data.get("id") if job_data else None
        if not identifier:
            logger.error("Trabajo de marcaje sin identificador")
            return

        mark = self._scheduled.pop(identifier, None)
        job = self._jobs.pop(identifier, None)
        if job:
            job.schedule_removal()
        self._persist()

        if not mark:
            logger.warning("Marcaje %s no encontrado al ejecutar el job", identifier)
            return

        logger.info("Ejecutando marcaje programado %s (%s)", identifier, mark.action)
        resultado = await ejecutar_fichaje_async(mark.action, context)

        chat_id = self._chat_id or get_config().telegram_chat_id
        prefix = "🚪" if mark.action == "entrada" else "🏁"
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{prefix} Marcaje programado de {mark.action} ejecutado.",
        )
        await context.bot.send_message(chat_id=chat_id, text=resultado.message)

        if mark.action == "entrada" and resultado.success:
            auto_when = ahora_madrid() + timedelta(hours=7)
            auto_mark = self.create_mark("salida", auto_when)
            self.add_mark(context.application, auto_mark)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🕐 Salida automática programada para las {auto_when.strftime('%H:%M')}.",
            )


scheduler_manager = SchedulerManager()
