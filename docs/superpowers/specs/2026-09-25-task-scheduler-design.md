# Core task scheduler — design (draft)

Status: **implemented** (plan: docs/superpowers/plans/2026-09-25-task-scheduler.md). Replaces the marks-only `SchedulerManager` with a generic scheduler that core code and plugins
use the same way. Scope agreed on 2026-09-25: **Level 1** (typed persisted one-shot tasks) and
**Level 2** (declared recurring jobs). Level 3 (a reusable persisted prompt-with-reminders) is deferred.

## Section 1 — Typed persisted tasks (*Decided*)

### One scheduler, marks are an ordinary kind

- `TaskScheduler` registers kinds, schedules, cancels, lists, persists and restores tasks. It knows nothing about marks.
- `SchedulerManager` is removed. Mark logic moves to `fichaxebot/tasks/marks.py` as functions over the scheduler
  (`schedule`, `pending`, `cancel`, `schedule_auto_checkout`), registering kind `"mark"` exactly like a plugin
  registers its kinds. `/marcar`, `/pendientes`, `/cancelar`, `commands/messages.py` and the daily question call
  `marks.*`. Mark-only views filter by `kind == "mark"`, so plugin tasks never suppress the daily question or appear
  in `/pendientes` / `/cancelar`.

### Registry

```python
scheduler.register_kind(
    "mark", handler,
    misfire=Misfire.DISCARD_AND_NOTIFY,
    interrupted=Interrupted.NOTIFY,
)

async def handler(context, task: ScheduledTask) -> None: ...
```

- Plugin kinds are namespaced `<plugin>.<name>`. Duplicate kinds or non-async handlers fail at startup.
- `schedule(kind, when, payload)` validates that `payload` is JSON-serialisable at call time. The application is
  captured by `start(app)`, so scheduling before `start` raises.
- Kinds must be registered before tasks are restored: plugins register in their `setup()` hook, which runs before loading.

### Task lifecycle

```
pending ──(due)──▶ running (persisted) ──(handler returns or raises)──▶ removed
```

- The task is persisted as `running` **before** its handler starts, and removed when the handler finishes.
- A handler that raises is logged and the task removed. Kinds registered with `notify_errors=True` also send a
  Telegram message naming the task and the error.

### Restore on startup

| Saved state | Condition | Behaviour |
| --- | --- | --- |
| `pending` | due in the future | scheduled normally |
| `pending` | due time passed while the bot was down | kind's **misfire** policy |
| `running` | bot stopped during execution; outcome unknown | kind's **interrupted** policy |
| any | kind not registered (e.g. plugin disabled) | kept in the file untouched, not scheduled, warning logged |

- Misfire policies: `DISCARD` (log only), `DISCARD_AND_NOTIFY` (included in the startup Telegram message),
  `RUN_LATE` (run right after startup). Marks use `DISCARD_AND_NOTIFY`.
- Interrupted policies: `NOTIFY` (default; tell the user to check USC before repeating, remove the task, never re-run)
  and `RETRY` (re-run; only for kinds that are safe to repeat, such as read-only checks). Marks use `NOTIFY`.
- Nothing is dropped silently: every task runs, is reported, or is kept.

### Persistence

```json
{"version": 2, "tasks": [
  {"id": "…", "kind": "mark", "when": "2026-10-01T09:00:00+02:00",
   "payload": {"action": "entrada"}, "state": "pending"}
]}
```

- Same file `.schedule.data`, anchored next to `config.json`, written with `storage.write_json_atomic`.
- **No migration**: the old list format is treated as invalid, moved to `.schedule.data.corrupt`, and the bot starts empty.

## Section 2 — Declared recurring jobs (*Decided*)

Recurring work is declared in code at startup from configuration and is **not persisted**; only each job's
last-run date is.

```python
scheduler.register_daily("daily_question", at=time(9, 0), handler=ask_for_check_in,
                         weekdays=range(0, 5), catch_up=True)
scheduler.register_daily("congreso_dieta.auth_check", at=cfg.check_time, handler=check_auth, catch_up=True)
scheduler.register_interval(name, every=timedelta(minutes=30), handler=...)
```

- Same registration rules as task kinds: namespaced names, async handlers, unique, registered before startup.
- `weekdays` uses Python numbering (0 = Monday) and is converted internally for PTB (0 = Sunday).
- All times are Europe/Madrid.
- `catch_up=True`: if the bot starts after today's run time, on a scheduled day, and the job has not run today,
  it runs once right after startup. Last-run dates are persisted in `.schedule.data` under
  `"recurring": {"<name>": "YYYY-MM-DD"}`.
  - Replaces the ad-hoc startup block in `bot.py` that triggers the daily question after 09:00
    (`ask_for_check_in` already checks weekends, holidays and pending marks itself).
  - Behaviour fix: restarting after the question was already sent today no longer asks again.
- A handler that raises is logged; the job keeps its schedule. Jobs registered with `notify_errors=True` also send
  a Telegram message.
- Out of scope (Level 3, deferred): the in-memory check-in reminders inside `ask_for_check_in` stay as they are.
  Plugin reminders are chained one-shot tasks (next = now + interval, moved to the next working-hours window),
  with misfire `RUN_LATE` and interrupted `RETRY`.

## Section 3 — Integration and testing (*Decided*)

### Files

- `fichaxebot/scheduler.py` — rewritten: `TaskScheduler`, `ScheduledTask`, `Misfire`, `Interrupted`, recurring jobs.
  `SchedulerManager` is removed.
- `fichaxebot/tasks/marks.py` — new: registers kind `mark`; `schedule`, `pending`, `cancel`,
  `schedule_auto_checkout` (random-offset logic moved unchanged).
- Callers switch from `scheduler_manager.*` to `marks.*`: `commands/mark.py`, `commands/cancel.py`,
  `commands/pending.py`, `commands/messages.py`, `ask_for_check_in`. The scheduler is exposed as `app.scheduler`
  (replaces `app.scheduler_manager`).
- `fichaxebot/plugins.py` — also calls an optional plugin `setup(application)` hook, where plugins register kinds
  and recurring jobs. (`WEBAPP_CONTROLLERS` is part of the plugin spec and lands with the plugin.)

### Startup order (`bot.py`)

1. Create `TaskScheduler`; register kind `mark` and recurring job `daily_question`.
2. `register_plugins`: commands, then each plugin's `setup()`.
3. Create the USC session; `app.initialize()`, `app.start()`.
4. `scheduler.start(app)`: restore tasks, apply misfire/interrupted policies, schedule recurring jobs, run catch-ups.
   Registering kinds or jobs after `start` raises.
5. One startup message listing restored marks, marks missed while offline and interrupted tasks
   (replaces the current "♻️ Marcajes restaurados" message).

### Concurrency and shutdown

- All scheduler state changes run on the event loop; no locks. USC work inside handlers keeps using the browser lock.
- No special shutdown handling: a handler cut off mid-run stays `running` in the file and is handled as interrupted
  on the next start.

### Testing

The scheduler takes an injectable `clock` so tests control time.

- Lifecycle: persisted as `running` before the handler starts; removed after it returns or raises.
- Restore: future / overdue / running / unknown-kind, every misfire and interrupted policy, unknown kinds kept intact.
- Registry: duplicate kinds, non-async handlers, registration after `start`, non-JSON payloads.
- Recurring: weekday conversion, catch-up at most once per day across restarts, last-run persistence.
- Marks: only marks visible to `/pendientes`, `/cancelar` and `marks.pending` (used by the daily question);
  auto-checkout offset; cancelled jobs
  removed from the job queue.
- Startup: plugin `setup()` runs before restore; single startup message.
- Existing file-location and atomic-write tests are kept; `SchedulerManager` tests move to the marks module.

### Rollout

No migration: marks scheduled at upgrade time are **not** restored (the old file is moved to
`.schedule.data.corrupt`). Cancel or note pending marks before deploying and re-add them afterwards.
