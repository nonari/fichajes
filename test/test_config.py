import json
from datetime import timedelta
from pathlib import Path

import pytest

from config import AppConfig, load_config


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_config_success(tmp_path: Path) -> None:
    config_data = {
        "telegram_token": "token",
        "telegram_chat_id": "chat",
        "usc_user": "user",
        "usc_pass": "pass",
        "daily_question_time": "08:30",
        "auto_checkout_delay_minutes": 15,
        "max_reminders": 4,
        "reminder_interval_minutes": 10,
    }
    config_path = write_config(tmp_path, config_data)

    config = load_config(config_path)

    assert isinstance(config, AppConfig)
    assert config.telegram_token == "token"
    assert config.telegram_chat_id == "chat"
    assert config.usc_user == "user"
    assert config.usc_pass == "pass"
    assert config.daily_question_time.hour == 8
    assert config.daily_question_time.minute == 30
    assert config.auto_checkout_delay == timedelta(minutes=15)
    assert config.max_reminders == 4
    assert config.reminder_interval == timedelta(minutes=10)


def test_load_config_missing_required_key(tmp_path: Path) -> None:
    config_data = {
        "telegram_token": "token",
        "usc_user": "user",
        "usc_pass": "pass",
        "reminder_interval_minutes": 10,
    }
    config_path = write_config(tmp_path, config_data)

    with pytest.raises(KeyError):
        load_config(config_path)


def test_load_config_invalid_reminder_interval(tmp_path: Path) -> None:
    config_data = {
        "telegram_token": "token",
        "telegram_chat_id": "chat",
        "usc_user": "user",
        "usc_pass": "pass",
        "reminder_interval_minutes": 0,
    }
    config_path = write_config(tmp_path, config_data)

    with pytest.raises(ValueError, match="reminder_interval_minutes"):
        load_config(config_path)
