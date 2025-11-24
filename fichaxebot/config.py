from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time as dtime, timedelta
from pathlib import Path
from typing import Optional

from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)

CONFIG_FILE = Path(__file__).parent.parent / "config.json"


@dataclass
class AppConfig:
    telegram_token: str
    telegram_chat_id: str
    usc_user: str
    usc_pass: str
    daily_question_time: dtime
    auto_checkout_delay: Optional[timedelta]
    auto_checkout_random_offset_minutes: int
    max_reminders: int
    reminder_interval: timedelta
    calendar_webapp_url: str
    vacations_webapp_url: str


_config: Optional[AppConfig] = None


def _parse_time_field(value: object, field_name: str) -> dtime:
    if not isinstance(value, str):
        raise ValueError(f"El valor de '{field_name}' debe ser una cadena en formato HH:MM")

    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Formato inválido para '{field_name}': {value}")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:  # pragma: no cover - validado al cargar
        raise ValueError(f"Formato inválido para '{field_name}': {value}") from exc

    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(
            f"El valor de '{field_name}' debe ser una hora válida entre 00:00 y 23:59"
        )

    return dtime(hour=hour, minute=minute)


def _parse_int_field(value: object, field_name: str) -> int:
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - validado al cargar
        raise ValueError(f"El valor de '{field_name}' debe ser un número entero") from exc

    return integer


def load_config(path: Optional[Path] = None) -> AppConfig:
    config_path = path or CONFIG_FILE

    if not config_path.exists():
        raise FileNotFoundError(f"No se encontró el fichero de configuración: {config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))

    missing = {key for key in [
        "telegram_token",
        "telegram_chat_id",
        "usc_user",
        "usc_pass",
    ] if key not in data}

    if missing:
        raise KeyError(f"Faltan claves en el fichero de configuración: {', '.join(sorted(missing))}")

    daily_question_raw = data.get("daily_question_time", "09:00")
    daily_question_time = _parse_time_field(daily_question_raw, "daily_question_time")

    checkout_minutes_raw = data.get("auto_checkout_delay_minutes", 7 * 60)
    checkout_minutes = _parse_int_field(checkout_minutes_raw, "auto_checkout_delay_minutes")
    if checkout_minutes < 0:
        raise ValueError("El valor de 'auto_checkout_delay_minutes' no puede ser negativo")
    auto_checkout_delay = timedelta(minutes=checkout_minutes) if checkout_minutes else None

    random_offset_raw = data.get("auto_checkout_random_offset_minutes", 3)
    random_offset = _parse_int_field(random_offset_raw, "auto_checkout_random_offset_minutes")
    if random_offset < 0:
        raise ValueError(
            "El valor de 'auto_checkout_random_offset_minutes' no puede ser negativo"
        )

    max_reminders_raw = data.get("max_reminders", 3)
    max_reminders = _parse_int_field(max_reminders_raw, "max_reminders")
    if max_reminders < 0:
        raise ValueError("El valor de 'max_reminders' no puede ser negativo")

    reminder_interval_raw = data.get("reminder_interval_minutes", 5)
    reminder_interval_minutes = _parse_int_field(
        reminder_interval_raw, "reminder_interval_minutes"
    )
    if reminder_interval_minutes <= 0:
        raise ValueError("El valor de 'reminder_interval_minutes' debe ser mayor que cero")
    reminder_interval = timedelta(minutes=reminder_interval_minutes)

    calendar_webapp_url = str(data.get("calendar_webapp_url", "") or "").strip()
    vacations_webapp_url = str(data.get("vacations_webapp_url", calendar_webapp_url) or "").strip()

    return AppConfig(
        telegram_token=str(data["telegram_token"]),
        telegram_chat_id=str(data["telegram_chat_id"]),
        usc_user=str(data["usc_user"]),
        usc_pass=str(data["usc_pass"]),
        daily_question_time=daily_question_time,
        auto_checkout_delay=auto_checkout_delay,
        auto_checkout_random_offset_minutes=random_offset,
        max_reminders=max_reminders,
        reminder_interval=reminder_interval,
        calendar_webapp_url=calendar_webapp_url,
        vacations_webapp_url=vacations_webapp_url,
    )


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
        logger.info("Configuration loaded from %s", CONFIG_FILE)
    return _config
