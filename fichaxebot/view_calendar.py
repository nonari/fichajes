from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from selenium.common.exceptions import JavascriptException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from fichaxebot.config import get_config
from fichaxebot.fichador import _create_driver, _login
from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)

CALENDAR_URL = "https://fichaxe.usc.gal/pas/calendarioAnual"


@dataclass
class CalendarEntry:
    """Simplified calendar entry used by the WebApp."""

    start: str
    end: str
    code: str

    def as_payload(self) -> str:
        """Return the compact representation sent to the WebApp."""

        if self.start == self.end:
            return f"{self.code}{self.start}"
        return f"{self.code}{self.start}:{self.end}"


class CalendarFetchError(RuntimeError):
    """Raised when the calendar page cannot be processed."""


def _wait_until(wait: WebDriverWait, condition: Callable[..., Any]) -> Any:
    return wait.until(condition)


def _read_calendar_array(driver) -> list[dict[str, Any]]:
    """Return the raw calendario array from the loaded page."""

    try:
        data_json = driver.execute_script("return JSON.stringify(window.calendario || []);")
    except JavascriptException as exc:  # pragma: no cover - depends on remote content
        raise CalendarFetchError("No se pudo acceder al calendario en la página") from exc

    if not data_json:
        return []

    try:
        return json.loads(data_json)
    except json.JSONDecodeError as exc:  # pragma: no cover - depends on remote format
        raise CalendarFetchError("El calendario recibido tiene un formato desconocido") from exc


def _map_kind(tipo: str) -> Optional[str]:
    normalized = tipo.upper()
    if "VACACION" in normalized:
        return "V"
    if "NON_LABORABLE" in normalized:
        return "N"
    return None


def _normalize_date(value: Any) -> Optional[datetime]:
    """
    Convert ISO string or timestamp (ms/s) to a timezone-aware UTC datetime object.
    Returns None if parsing fails.
    """
    if value is None:
        return None

    # --- numeric timestamps ---
    if isinstance(value, (int, float)):
        if value > 1e11:  # milliseconds → seconds
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    # --- string (ISO 8601) ---
    text = str(value).strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _iter_relevant_entries(raw_entries: Iterable[dict[str, Any]]) -> Iterable[CalendarEntry]:
    for entry in raw_entries:
        start = _normalize_date(entry.get("startDate"))
        end = _normalize_date(entry.get("endDate"))
        tipo = entry.get("tipo", "")
        if not start or not end:
            logger.warning(f'Unexpected entry date format "{entry}"')
            continue
        start = start + timedelta(days=1)
        kind = _map_kind(str(tipo))
        if not kind:
            continue

        if kind == "N":
            current = start
            only_weekend = True
            while current <= end:
                if current.weekday() < 5:
                    only_weekend = False
                    break
                current += timedelta(days=1)
            if only_weekend:
                continue
        yield CalendarEntry(start=start.date().isoformat(), end=end.date().isoformat(), code=kind)


def fetch_calendar_summary() -> list[str]:
    """Return compact calendar entries relevant for the vacation viewer."""

    config = get_config()
    if not config.usc_user or not config.usc_pass:
        raise CalendarFetchError(
            "Las credenciales de USC no están configuradas; no se puede obtener el calendario.",
        )

    driver = _create_driver()
    wait = WebDriverWait(driver, 20)

    try:
        _login(driver, wait, config.usc_user, config.usc_pass)
        driver.get(CALENDAR_URL)
        try:
            _wait_until(
                wait,
                lambda d: d.execute_script(
                    "return Array.isArray(window.calendario) && window.calendario.length >= 0;"
                ),
            )
        except TimeoutException as exc:  # pragma: no cover - depends on remote load
            raise CalendarFetchError("No se pudo cargar el calendario en la página") from exc

        raw_entries = _read_calendar_array(driver)
        simplified = list(_iter_relevant_entries(raw_entries))
    finally:
        driver.quit()

    simplified.sort(key=lambda item: item.start)
    logger.info("Recovered %s calendar entries for the viewer", len(simplified))
    return [entry.as_payload() for entry in simplified]
