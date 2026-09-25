"""Validate the plugin's section of config.json."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from datetime import date, time as dtime, timedelta
from pathlib import Path
from typing import Optional

from fichaxebot.scrap_functions.congress_request import validate_request
from fichaxebot.utils import parse_hour_minute

NAME = "congreso_dieta"


@dataclass(frozen=True)
class PromptConfig:
    at: dtime
    reminder_minutes: int
    window_start: dtime
    window_end: dtime


@dataclass(frozen=True)
class SigningConfig:
    store: str
    alias: str
    password: Optional[str]


@dataclass(frozen=True)
class AbsenceConfig:
    type_name: str
    start_time: str
    end_time: str


@dataclass(frozen=True)
class PluginConfig:
    webapp_url: str
    output_dir: Path
    auth_check_time: dtime
    prompt: PromptConfig
    signing: SigningConfig
    days_before: int
    spreadsheet_template: Path
    absence: AbsenceConfig
    congress: dict


def _fail(message: str):
    raise ValueError(f"plugin_config.{NAME}: {message}")


def _section(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        _fail(f"'{key}' debe ser un objeto")
    return value


def _hhmm(value, label: str) -> dtime:
    parsed = parse_hour_minute(value) if isinstance(value, str) else None
    if parsed is None:
        _fail(f"'{label}' debe ser una hora HH:MM")
    return parsed


def _positive_int(value, label: str) -> int:
    if type(value) is not int or value <= 0:
        _fail(f"'{label}' debe ser un entero mayor que cero")
    return value


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"'{label}' es obligatorio")
    return value.strip()


def parse_config(raw, *, today: date, check_files: bool = True) -> PluginConfig:
    """Validate a raw plugin section. check_files=False re-reads a case snapshot."""
    if not isinstance(raw, dict):
        _fail("falta la sección de configuración del plugin")
    webapp_url = raw.get("webapp_url")
    if not isinstance(webapp_url, str) or not webapp_url.startswith("https://"):
        _fail("'webapp_url' debe ser una URL https")

    prompt_raw = _section(raw, "prompt")
    window = prompt_raw.get("window")
    if not isinstance(window, list) or len(window) != 2:
        _fail("'prompt.window' debe ser [\"HH:MM\", \"HH:MM\"]")
    window_start = _hhmm(window[0], "prompt.window")
    window_end = _hhmm(window[1], "prompt.window")
    if window_start >= window_end:
        _fail("'prompt.window' debe empezar antes de terminar")
    prompt = PromptConfig(
        at=_hhmm(prompt_raw.get("at"), "prompt.at"),
        reminder_minutes=_positive_int(prompt_raw.get("reminder_minutes"), "prompt.reminder_minutes"),
        window_start=window_start, window_end=window_end,
    )
    if not window_start <= prompt.at < window_end:
        _fail("'prompt.at' debe estar dentro de 'prompt.window'")

    signing_raw = _section(raw, "signing")
    store = signing_raw.get("store")
    if store != "mozilla" and not (isinstance(store, str) and store.startswith("pkcs12:") and len(store) > 7):
        _fail("'signing.store' debe ser \"mozilla\" o \"pkcs12:/ruta/al/certificado.p12\"")
    alias = _text(signing_raw.get("alias"), "signing.alias")
    password = signing_raw.get("password")
    if password is not None and not isinstance(password, str):
        _fail("'signing.password' debe ser texto o null")

    absence_raw = _section(raw, "absence")
    type_name = _text(absence_raw.get("type"), "absence.type")
    absence_start = _hhmm(absence_raw.get("start_time"), "absence.start_time")
    absence_end = _hhmm(absence_raw.get("end_time"), "absence.end_time")
    if absence_start >= absence_end:
        _fail("'absence.start_time' debe ser anterior a 'absence.end_time'")

    congress = _section(raw, "congress")
    if "start_date" in congress or "end_date" in congress:
        _fail("'congress' no debe incluir fechas: se eligen en el calendario")

    output_dir = Path(_text(raw.get("output_dir"), "output_dir"))
    template = Path(_text(raw.get("spreadsheet_template"), "spreadsheet_template"))
    if check_files:
        if not output_dir.is_dir() or not os.access(output_dir, os.W_OK):
            _fail(f"'output_dir' debe ser una carpeta con permiso de escritura: {output_dir}")
        if template.suffix.lower() != ".xlsm" or not template.is_file():
            _fail(f"'spreadsheet_template' debe ser un fichero .xlsm existente: {template}")
        if store.startswith("pkcs12:") and not Path(store[len("pkcs12:"):]).is_file():
            _fail(f"'signing.store': no existe el certificado {store[len('pkcs12:'):]}")
        placeholder = (today + timedelta(days=30)).isoformat()
        try:
            validate_request({**congress, "start_date": placeholder, "end_date": placeholder}, today)
        except ValueError as exc:
            _fail(f"'congress' no es válido: {exc}")

    return PluginConfig(
        webapp_url=webapp_url,
        output_dir=output_dir,
        auth_check_time=_hhmm(raw.get("auth_check_time"), "auth_check_time"),
        prompt=prompt,
        signing=SigningConfig(store, alias, password),
        days_before=_positive_int(raw.get("days_before"), "days_before"),
        spreadsheet_template=template,
        absence=AbsenceConfig(type_name, f"{absence_start:%H:%M}", f"{absence_end:%H:%M}"),
        congress=copy.deepcopy(congress),
    )
