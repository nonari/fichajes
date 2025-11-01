from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import List

import pytest
from importlib import import_module

from config import AppConfig
from utils import MADRID_TZ

mark_command = import_module("commands.mark")
start_command = import_module("commands.start")


@dataclass
class FakeCheckInResult:
    success: bool
    message: str


class FakeMessage:
    def __init__(self) -> None:
        self.replies: List[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeSchedulerManager:
    def __init__(self) -> None:
        self.scheduled: List[tuple[str, datetime]] = []
        self.cancelled_actions: List[str] = []

    def schedule(self, app, action: str, when: datetime):
        self.scheduled.append((action, when))
        return SimpleNamespace(action=action, when=when)

    def cancel_by_action(self, action: str) -> int:
        self.cancelled_actions.append(action)
        removed = sum(1 for item in self.scheduled if item[0] == action)
        self.scheduled = [item for item in self.scheduled if item[0] != action]
        return removed

def test_start_command_informs_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    message = FakeMessage()
    update = SimpleNamespace(message=message)
    dummy_config = AppConfig(
        telegram_token="token",
        telegram_chat_id="chat",
        usc_user="user",
        usc_pass="pass",
        daily_question_time=datetime(2024, 1, 1, 9, 15, tzinfo=MADRID_TZ).timetz(),
        auto_checkout_delay=timedelta(minutes=30),
        max_reminders=3,
        reminder_interval=timedelta(minutes=5),
    )
    monkeypatch.setattr(start_command, "get_config", lambda: dummy_config)

    asyncio.run(start_command.start(update, SimpleNamespace()))

    assert message.replies
    assert "Preguntaré cada día laborable a las 09:15" in message.replies[0]


def test_mark_command_without_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    message = FakeMessage()
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(args=[], application=SimpleNamespace(scheduler_manager=SimpleNamespace()))

    dummy_config = AppConfig(
        telegram_token="token",
        telegram_chat_id="chat",
        usc_user="user",
        usc_pass="pass",
        daily_question_time=datetime(2024, 1, 1, 9, 0, tzinfo=MADRID_TZ).timetz(),
        auto_checkout_delay=timedelta(minutes=15),
        max_reminders=3,
        reminder_interval=timedelta(minutes=5),
    )
    monkeypatch.setattr(mark_command, "get_config", lambda: dummy_config)

    asyncio.run(mark_command.mark(update, context))

    assert message.replies == ["Uso: /marcar entrada|salida [HH:MM]"]


def test_mark_command_schedules_future_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = FakeSchedulerManager()
    message = FakeMessage()
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(
        args=["entrada", "23:59"],
        application=SimpleNamespace(scheduler_manager=scheduler),
    )

    now = MADRID_TZ.localize(datetime(2024, 1, 1, 10, 0))
    dummy_config = AppConfig(
        telegram_token="token",
        telegram_chat_id="chat",
        usc_user="user",
        usc_pass="pass",
        daily_question_time=now.timetz(),
        auto_checkout_delay=timedelta(minutes=15),
        max_reminders=3,
        reminder_interval=timedelta(minutes=5),
    )
    monkeypatch.setattr(mark_command, "get_config", lambda: dummy_config)
    monkeypatch.setattr(mark_command, "get_madrid_now", lambda: now)

    asyncio.run(mark_command.mark(update, context))

    assert scheduler.scheduled
    scheduled_action, scheduled_when = scheduler.scheduled[0]
    assert scheduled_action == "entrada"
    assert scheduled_when.strftime("%H:%M") == "23:59"
    assert message.replies == ["🗓️ Marcaje programado de entrada para las 23:59."]


def test_mark_command_executes_immediate_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = FakeSchedulerManager()
    message = FakeMessage()
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(
        args=["entrada"],
        application=SimpleNamespace(scheduler_manager=scheduler),
    )

    now = MADRID_TZ.localize(datetime(2024, 1, 1, 9, 0))
    dummy_config = AppConfig(
        telegram_token="token",
        telegram_chat_id="chat",
        usc_user="user",
        usc_pass="pass",
        daily_question_time=now.timetz(),
        auto_checkout_delay=timedelta(minutes=45),
        max_reminders=3,
        reminder_interval=timedelta(minutes=5),
    )
    monkeypatch.setattr(mark_command, "get_config", lambda: dummy_config)
    monkeypatch.setattr(mark_command, "get_madrid_now", lambda: now)

    async def fake_check_in(action: str, _context):
        return FakeCheckInResult(success=True, message="✅ Entrada realizada")

    monkeypatch.setattr(mark_command, "execute_check_in_async", fake_check_in)

    asyncio.run(mark_command.mark(update, context))

    assert message.replies[0] == "✅ Entrada realizada"
    assert message.replies[1] == "🕐 Salida programada para las 09:45"
    assert scheduler.scheduled == [("salida", now + timedelta(minutes=45))]


def test_mark_command_executes_exit_and_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = FakeSchedulerManager()
    scheduler.scheduled.append(("salida", MADRID_TZ.localize(datetime(2024, 1, 1, 18, 0))))
    message = FakeMessage()
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(
        args=["salida"],
        application=SimpleNamespace(scheduler_manager=scheduler),
    )

    now = MADRID_TZ.localize(datetime(2024, 1, 1, 17, 30))
    dummy_config = AppConfig(
        telegram_token="token",
        telegram_chat_id="chat",
        usc_user="user",
        usc_pass="pass",
        daily_question_time=now.timetz(),
        auto_checkout_delay=timedelta(minutes=45),
        max_reminders=3,
        reminder_interval=timedelta(minutes=5),
    )
    monkeypatch.setattr(mark_command, "get_config", lambda: dummy_config)
    monkeypatch.setattr(mark_command, "get_madrid_now", lambda: now)

    async def fake_check_in(action: str, _context):
        return FakeCheckInResult(success=True, message="✅ Salida registrada")

    monkeypatch.setattr(mark_command, "execute_check_in_async", fake_check_in)

    asyncio.run(mark_command.mark(update, context))

    assert message.replies[0] == "✅ Salida registrada"
    assert message.replies[1] == "🗓️ Se cancelaron 1 marcajes de salida programados."
    assert not scheduler.scheduled
