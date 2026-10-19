from __future__ import annotations

import json
import keyword
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)

CONFIG_FILE = Path(__file__).parent.parent / "config.json"

# Top-level settings from before marking became the marcaje plugin. Rejected rather than ignored,
# so an upgrade cannot silently stop the daily question and scheduled marks.
MOVED_TO_MARCAJE = (
    "daily_question_time",
    "auto_checkout_delay_minutes",
    "auto_checkout_random_offset_minutes",
    "max_reminders",
    "reminder_interval_minutes",
)


@dataclass
class AppConfig:
    telegram_token: str
    telegram_chat_id: str
    usc_user: str
    usc_pass: str
    calendar_webapp_url: str
    vacations_webapp_url: str
    vacations_info_webapp_url: str
    absences_webapp_url: str = ""
    absence_confirmation_enabled: bool = True
    absence_confirmation_timeout_seconds: int = 60
    congress_confirmation_enabled: bool = True
    congress_confirmation_timeout_seconds: int = 60
    read_only: bool = False
    plugins: list[str] = field(default_factory=list)
    plugin_config: dict = field(default_factory=dict)
    vacation_confirmation_enabled: bool = False
    vacation_confirmation_timeout_seconds: int = 60


_config: Optional[AppConfig] = None


def load_config(path: Optional[Path] = None) -> AppConfig:
    config_path = path or CONFIG_FILE

    if not config_path.exists():
        raise FileNotFoundError(f"No se encontró el fichero de configuración: {config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    read_only = data.get("read_only", False)
    if not isinstance(read_only, bool):
        raise ValueError("'read_only' debe ser true o false")
    confirmation_enabled = data.get("vacation_confirmation_enabled", False)
    if not isinstance(confirmation_enabled, bool):
        raise ValueError("'vacation_confirmation_enabled' debe ser true o false")
    confirmation_timeout = data.get("vacation_confirmation_timeout_seconds", 60)
    if type(confirmation_timeout) is not int or confirmation_timeout <= 0:
        raise ValueError("'vacation_confirmation_timeout_seconds' debe ser un entero mayor que cero")

    absence_confirmation = data.get("absence_confirmation_enabled", True)
    if not isinstance(absence_confirmation, bool):
        raise ValueError("'absence_confirmation_enabled' debe ser true o false")
    absence_timeout = data.get("absence_confirmation_timeout_seconds", 60)
    if type(absence_timeout) is not int or absence_timeout <= 0:
        raise ValueError("'absence_confirmation_timeout_seconds' debe ser un entero mayor que cero")

    congress_confirmation = data.get("congress_confirmation_enabled", True)
    if not isinstance(congress_confirmation, bool):
        raise ValueError("'congress_confirmation_enabled' debe ser true o false")
    congress_timeout = data.get("congress_confirmation_timeout_seconds", 60)
    if type(congress_timeout) is not int or congress_timeout <= 0:
        raise ValueError("'congress_confirmation_timeout_seconds' debe ser un entero mayor que cero")

    plugins = data.get("plugins", [])
    if not isinstance(plugins, list) or any(
        not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name)
        for name in plugins
    ):
        raise ValueError("'plugins' debe ser una lista de nombres de paquetes Python, sin rutas ni puntos")
    if len(plugins) != len(set(plugins)):
        raise ValueError("'plugins' no puede contener nombres duplicados")

    moved = [key for key in MOVED_TO_MARCAJE if key in data]
    if moved:
        raise ValueError(
            f"{', '.join(moved)}: estos ajustes ahora están en plugin_config.marcaje. "
            "Muévelos allí y añade \"marcaje\" a 'plugins' para seguir fichando."
        )

    plugin_config = data.get("plugin_config", {})
    if not isinstance(plugin_config, dict) or any(
        not isinstance(name, str) or not isinstance(section, dict) for name, section in plugin_config.items()
    ):
        raise ValueError("'plugin_config' debe ser un objeto con una sección (objeto) por plugin")

    missing = {key for key in [
        "telegram_token",
        "telegram_chat_id",
        "usc_user",
        "usc_pass",
    ] if key not in data}

    if missing:
        raise KeyError(f"Faltan claves en el fichero de configuración: {', '.join(sorted(missing))}")

    calendar_webapp_url = str(data.get("calendar_webapp_url", "") or "").strip()
    vacations_webapp_url = str(data.get("vacations_webapp_url", calendar_webapp_url) or "").strip()
    vacations_info_webapp_url = str(data.get("vacations_info_webapp_url", "") or "").strip()

    return AppConfig(
        telegram_token=str(data["telegram_token"]),
        telegram_chat_id=str(data["telegram_chat_id"]),
        usc_user=str(data["usc_user"]),
        usc_pass=str(data["usc_pass"]),
        calendar_webapp_url=calendar_webapp_url,
        vacations_webapp_url=vacations_webapp_url,
        vacations_info_webapp_url=vacations_info_webapp_url,
        absences_webapp_url=str(data.get("absences_webapp_url", "") or "").strip(),
        absence_confirmation_enabled=absence_confirmation,
        absence_confirmation_timeout_seconds=absence_timeout,
        congress_confirmation_enabled=congress_confirmation,
        congress_confirmation_timeout_seconds=congress_timeout,
        read_only=read_only,
        vacation_confirmation_enabled=confirmation_enabled,
        vacation_confirmation_timeout_seconds=confirmation_timeout,
        plugins=plugins,
        plugin_config=plugin_config,
    )


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
        logger.info("Configuration loaded from %s", CONFIG_FILE)
    return _config
