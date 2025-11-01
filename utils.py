from __future__ import annotations

import asyncio
from datetime import date, datetime, time as dtime
from typing import Optional, Final
from zoneinfo import ZoneInfo

from holidays.countries.spain import Spain
from telegram.ext import ContextTypes

from fichador import perform_check_in
from logging_config import get_logger

MADRID_TZ: Final[ZoneInfo] = ZoneInfo("Europe/Madrid")

logger = get_logger(__name__)


def get_madrid_now() -> datetime:
    return datetime.now(MADRID_TZ)


async def execute_check_in_async(
    action: str, context: ContextTypes.DEFAULT_TYPE
):
    result = await asyncio.to_thread(perform_check_in, action)
    logger.info("Check-in result for %s: %s", action, result.message)
    return result


def is_galicia_holiday(day: date) -> bool:
    galicia_holidays = Spain(years=day.year, subdiv="GA")
    return day in galicia_holidays


def parse_hour_minute(value: str) -> Optional[dtime]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None

    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None

    return dtime(hour=hour, minute=minute)


def cancel_reminder(app, job_key: str, attempts_key: str) -> None:
    job = app.bot_data.pop(job_key, None)
    if job:
        job.schedule_removal()
    app.bot_data.pop(attempts_key, None)
