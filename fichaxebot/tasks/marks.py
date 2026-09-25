"""Clock-in/out marks as an ordinary task kind of the core scheduler."""

from __future__ import annotations

from datetime import datetime, timedelta
from random import randint
from typing import Callable, Optional

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scheduler import Interrupted, Misfire, ScheduledTask, TaskScheduler
from fichaxebot.utils import MADRID_TZ, execute_check_in_async, get_madrid_now

logger = get_logger(__name__)

KIND = "mark"
ACTIONS = ("entrada", "salida")


def register(scheduler: TaskScheduler) -> None:
    # A mark missed while the bot was down must not run late, and one cut off mid-run
    # may already be recorded in USC: report both, never repeat them.
    scheduler.register_kind(KIND, run_mark, misfire=Misfire.DISCARD_AND_NOTIFY,
                            interrupted=Interrupted.NOTIFY, describe=describe)


def describe(task: ScheduledTask) -> str:
    return f"{task.payload['action'].capitalize()} el {task.when.astimezone(MADRID_TZ):%d/%m %H:%M}"


def schedule(app, action: str, when: datetime) -> ScheduledTask:
    if action not in ACTIONS:
        raise ValueError("La acción de fichaje debe ser 'entrada' o 'salida'.")
    if when <= get_madrid_now():
        raise ValueError("La hora indicada ya ha pasado")
    return app.scheduler.schedule(KIND, when, {"action": action})


def pending(app) -> list[ScheduledTask]:
    return app.scheduler.pending(KIND)


def cancel(app, action: Optional[str] = None) -> int:
    if action is None:
        return app.scheduler.cancel(KIND)
    return app.scheduler.cancel(KIND, lambda task: task.payload["action"] == action)


def compute_auto_checkout_time(now: datetime, delay: timedelta, random_offset_minutes: int,
                               rand: Callable[[int, int], int] = randint) -> datetime:
    exit_time = now + delay
    limit = max(0, random_offset_minutes)
    if limit:
        offset = rand(-limit, limit)
        if offset:
            logger.info("Applying random offset of %s minutes to auto-checkout", offset)
        exit_time += timedelta(minutes=offset)
    if exit_time <= now:
        logger.info("Computed auto-checkout time %s is not in the future. Adjusting by one minute.",
                    exit_time.isoformat())
        exit_time = now + timedelta(minutes=1)
    return exit_time


def schedule_auto_checkout(app) -> ScheduledTask:
    config = get_config()
    if not config.auto_checkout_delay:
        raise ValueError("La salida automática no está configurada")
    when = compute_auto_checkout_time(get_madrid_now(), config.auto_checkout_delay,
                                      config.auto_checkout_random_offset_minutes)
    return schedule(app, "salida", when)


async def run_mark(context, task: ScheduledTask) -> None:
    action = task.payload["action"]
    chat_id = get_config().telegram_chat_id
    logger.info("Executing scheduled mark %s (%s)", task.id, action)
    result = await execute_check_in_async(action, context.application.web_session, context)

    prefix = "🚪" if action == "entrada" else "🏁"
    await context.bot.send_message(chat_id=chat_id, text=f"{prefix} Marcaje programado de {action} ejecutado.")
    await context.bot.send_message(chat_id=chat_id, text=result.message)

    if action == "entrada" and result.success and get_config().auto_checkout_delay:
        try:
            auto_task = schedule_auto_checkout(context.application)
        except ValueError:
            await context.bot.send_message(
                chat_id=chat_id, text="⚠️ No se programó la salida porque la hora calculada ya no es válida.",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🕐 Salida automática programada para las {}.".format(auto_task.when.strftime("%H:%M")),
            )
