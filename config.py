from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from logging_config import get_logger

logger = get_logger(__name__)

CONFIG_FILE = Path("config.json")


@dataclass
class AppConfig:
    telegram_token: str
    telegram_chat_id: str
    usc_user: str
    usc_pass: str


_config: Optional[AppConfig] = None


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

    return AppConfig(
        telegram_token=str(data["telegram_token"]),
        telegram_chat_id=str(data["telegram_chat_id"]),
        usc_user=str(data["usc_user"]),
        usc_pass=str(data["usc_pass"]),
    )


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
        logger.info("Configuration loaded from %s", CONFIG_FILE)
    return _config
