"""Validate the plugin's optional section of config.json; every setting has a default."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime, timedelta
from typing import Optional

from fichaxebot.utils import parse_hour_minute

NAME = "marcaje"


@dataclass(frozen=True)
class MarcajeConfig:
    daily_question_time: dtime
    auto_checkout_delay: Optional[timedelta]
    auto_checkout_random_offset_minutes: int
    max_reminders: int
    reminder_interval: timedelta


def _fail(message: str):
    raise ValueError(f"plugin_config.{NAME}: {message}")


def _int(raw: dict, key: str, default: int, *, positive: bool = False) -> int:
    value = raw.get(key, default)
    if type(value) is not int or value < 0 or (positive and value == 0):
        _fail(f"'{key}' debe ser un entero {'mayor que cero' if positive else 'no negativo'}")
    return value


def parse_config(raw: dict) -> MarcajeConfig:
    question_time = raw.get("daily_question_time", "09:00")
    parsed_time = parse_hour_minute(question_time) if isinstance(question_time, str) else None
    if parsed_time is None:
        _fail("'daily_question_time' debe ser una hora HH:MM")
    checkout_minutes = _int(raw, "auto_checkout_delay_minutes", 7 * 60)
    return MarcajeConfig(
        daily_question_time=parsed_time,
        auto_checkout_delay=timedelta(minutes=checkout_minutes) if checkout_minutes else None,
        auto_checkout_random_offset_minutes=_int(raw, "auto_checkout_random_offset_minutes", 3),
        max_reminders=_int(raw, "max_reminders", 3),
        reminder_interval=timedelta(minutes=_int(raw, "reminder_interval_minutes", 5, positive=True)),
    )


def plugin_config(application) -> MarcajeConfig:
    """The configuration validated by setup()."""
    return application.bot_data[NAME]
