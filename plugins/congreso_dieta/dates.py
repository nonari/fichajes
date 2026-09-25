"""Date rules for congress cases (Europe/Madrid)."""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta

from fichaxebot.utils import MADRID_TZ, is_galicia_holiday
from plugins.congreso_dieta.config import PromptConfig

MIN_NOTICE_DAYS = 5  # USC's congress form requires five days' notice


def earliest_start(today: date) -> date:
    return today + timedelta(days=MIN_NOTICE_DAYS)


def parse_non_working(entries) -> set[date]:
    """Days marked non-working ("N") in the USC calendar payload ("N2026-10-13" or "N<start>:<end>")."""
    days: set[date] = set()
    for entry in entries:
        if not isinstance(entry, str) or not entry.startswith("N"):
            continue
        first_text, _, last_text = entry[1:].partition(":")
        day = date.fromisoformat(first_text)
        last = date.fromisoformat(last_text) if last_text else day
        while day <= last:
            days.add(day)
            day += timedelta(days=1)
    return days


def absence_days(start: date, end: date, non_working: set[date]) -> list[date]:
    days = []
    day = start
    while day <= end:
        if day.weekday() < 5 and not is_galicia_holiday(day) and day not in non_working:
            days.append(day)
        day += timedelta(days=1)
    return days


def _at(day: date, moment: dtime) -> datetime:
    return datetime.combine(day, moment, tzinfo=MADRID_TZ)


def next_slot(moment: datetime, prompt: PromptConfig) -> datetime:
    """The same moment if inside the prompt window, otherwise the next window opening."""
    moment = moment.astimezone(MADRID_TZ)
    opens = _at(moment.date(), prompt.window_start)
    if moment < opens:
        return opens
    if moment < _at(moment.date(), prompt.window_end):
        return moment
    return _at(moment.date() + timedelta(days=1), prompt.window_start)


def first_prompt(start: date, days_before: int, prompt: PromptConfig, now: datetime) -> datetime:
    target = _at(start - timedelta(days=days_before), prompt.at)
    return target if target > now else next_slot(now, prompt)


def next_reminder(now: datetime, prompt: PromptConfig) -> datetime:
    return next_slot(now + timedelta(minutes=prompt.reminder_minutes), prompt)


def prompt_deadline(start: date) -> datetime:
    return _at(start, dtime(0, 0))


def generation_day(end: date, auth_day: date) -> date:
    return max(end + timedelta(days=1), auth_day)
