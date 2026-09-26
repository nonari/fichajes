# congreso_dieta Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `congreso_dieta` plugin that requests the USC congress authorization from a Mini App date range, asks N days before whether to request the absence, waits for the signed authorization, fills the per-diem workbook through its own macro, joins and signs the PDF with AutoFirma, and delivers it.

**Architecture:** A plugin package `plugins/congreso_dieta/` with small units — config validation, date rules, a persisted case store, USC request-page scraping, LibreOffice/UNO spreadsheet generation, PDF join/sign — orchestrated by `flow.py`, which wakes up through the core `TaskScheduler` (one-shot absence-prompt tasks and one daily job). The Mini App is a static page (`docs/congreso.html` + `docs/congreso.js`). Small core additions let plugins declare `plugin_config`, web app handlers, and run scrapers under the browser lock.

**Tech Stack:** Python 3.12, python-telegram-bot 20.7, Selenium (existing `UscWebSession`), LibreOffice 24.2 + system `python3-uno` (venv with system site-packages), poppler `pdfunite`, AutoFirma CLI, FullCalendar 6 in the Mini App, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-25-congreso-dieta-design.md` (depends on `docs/superpowers/specs/2026-09-25-task-scheduler-design.md`, implemented).

## Global Constraints

- User-facing text is Spanish, matching existing messages.
- All times Europe/Madrid (`fichaxebot.utils.MADRID_TZ`).
- Steps that write to USC (congress request, absence request) are never retried automatically.
- Persistence goes through `fichaxebot.storage.write_json_atomic`; plugin data lives in `.plugin_data/` next to `config.json` and is git-ignored.
- Personal data (saved USC pages in `resources/`, the real workbook, certificates) is never committed; tests use sanitised fixtures.
- LibreOffice/UNO and AutoFirma run only on the host install (not Docker). The venv must include system site-packages.
- Run tests with `.venv/bin/python -m unittest <module> -v` from the repository root (`pytest` is not installed).
- Commits: only with the user's approval.

## File Structure

| File | Responsibility |
| --- | --- |
| `fichaxebot/config.py` | Modify: `plugin_config` section |
| `fichaxebot/plugins.py` | Modify: merge plugin `WEBAPP_CONTROLLERS` into the router |
| `fichaxebot/usc_api.py` | Modify: `UscWebSession.run(operation, *args)` under the browser lock |
| `fichaxebot/webapp_controller/vacation_confirmation.py` | Modify: accept the `congreso` confirmation prefix |
| `install.sh`, `.gitignore` | Modify: `--system-site-packages`; ignore `.plugin_data/` |
| `plugins/congreso_dieta/__init__.py` | Create: `COMMANDS`, `WEBAPP_CONTROLLERS`, `setup()` |
| `plugins/congreso_dieta/config.py` | Create: plugin config validation |
| `plugins/congreso_dieta/dates.py` | Create: date rules |
| `plugins/congreso_dieta/cases.py` | Create: `Case`, `CaseStore` |
| `plugins/congreso_dieta/usc.py` | Create: request detail parsing, status fetch, authorization download |
| `plugins/congreso_dieta/spreadsheet.py` | Create: private headless LibreOffice, `CreaPDF` macro |
| `plugins/congreso_dieta/pdf.py` | Create: `pdfunite` join, AutoFirma sign |
| `plugins/congreso_dieta/flow.py` | Create: Mini App actions, absence prompt, daily document run |
| `docs/congreso.html`, `docs/congreso.js` | Create: Mini App |
| `docs/congreso_dieta.md` | Create: setup guide |
| `tests/test_core_plugin_support.py`, `tests/test_congreso_*.py`, `tests/browser_congreso_flow.py`, `tests/fixtures/congreso_solicitude.html` | Create: tests and sanitised fixture |

---

### Task 1: Core support for the plugin

**Files:**
- Modify: `fichaxebot/config.py`, `fichaxebot/plugins.py`, `fichaxebot/usc_api.py`, `fichaxebot/webapp_controller/vacation_confirmation.py`, `install.sh`, `.gitignore`
- Test: `tests/test_plugins.py` (append), `tests/test_core_plugin_support.py` (create)

**Interfaces:**
- Produces:
  - `AppConfig.plugin_config: dict[str, dict]` (default `{}`)
  - Plugins may export `WEBAPP_CONTROLLERS: Mapping[str, async (update, context, data)]`, merged into `fichaxebot.webapp_controller.router.WEBAPP_CONTROLLERS` (types match `[a-z0-9_]{1,64}`, no collisions, all-or-nothing)
  - `UscWebSession.run(operation, *args, **kwargs)` → `operation(session, *args, **kwargs)` holding the browser lock
  - `CALLBACK_PATTERN` accepts `congreso_(confirm|cancel):<32 hex>`

- [ ] **Step 1: Write the failing tests**

Append inside `class PluginConfigTests` in `tests/test_plugins.py`:

```python
    def test_plugin_config_defaults_to_empty_and_requires_objects(self):
        self.assertEqual(self.load().plugin_config, {})
        self.assertEqual(self.load(plugin_config={"x": {"a": 1}}).plugin_config, {"x": {"a": 1}})
        for value in ([], "x", {"x": 1}, {"x": []}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "plugin_config"):
                self.load(plugin_config=value)
```

Add `from fichaxebot.webapp_controller import router` to the imports of `tests/test_plugins.py`, then append inside `class PluginLoaderTests`:

```python
    async def test_webapp_controllers_are_merged_into_the_router(self):
        self.plugin("feature", """
            async def handle(update, context, data):
                pass
            COMMANDS = {}
            WEBAPP_CONTROLLERS = {"feature_submit": handle}
        """)
        with patch.dict(router.WEBAPP_CONTROLLERS):
            register_plugins(self.app, ["feature"])
            self.assertIn("feature_submit", router.WEBAPP_CONTROLLERS)
        self.assertNotIn("feature_submit", router.WEBAPP_CONTROLLERS)

    async def test_invalid_or_colliding_webapp_controllers_are_rejected(self):
        for index, source in enumerate((
            "COMMANDS = {}\nWEBAPP_CONTROLLERS = []\n",
            "async def h(u, c, d):\n    pass\nCOMMANDS = {}\nWEBAPP_CONTROLLERS = {'Bad Type': h}\n",
            "def h(u, c, d):\n    pass\nCOMMANDS = {}\nWEBAPP_CONTROLLERS = {'feature_submit': h}\n",
            "async def h(u, c, d):\n    pass\nCOMMANDS = {}\nWEBAPP_CONTROLLERS = {'absence_request_submit': h}\n",
        )):
            name = f"bad_controllers_{index}"
            self.plugin(name, source)
            with self.subTest(source=source), patch.dict(router.WEBAPP_CONTROLLERS), \
                    self.assertRaisesRegex(ValueError, name):
                register_plugins(self.app, [name])
```

Create `tests/test_core_plugin_support.py`:

```python
import re
import unittest
from threading import RLock

from fichaxebot.usc_api import UscWebSession
from fichaxebot.webapp_controller.vacation_confirmation import CALLBACK_PATTERN


class SessionRunTests(unittest.TestCase):
    def test_run_passes_the_session_and_holds_the_browser_lock(self):
        session = UscWebSession.__new__(UscWebSession)
        session._lock = RLock()

        def operation(received, value):
            self.assertIs(received, session)
            self.assertTrue(session._lock._is_owned())
            return value * 2

        self.assertEqual(session.run(operation, 21), 42)


class ConfirmationPatternTests(unittest.TestCase):
    def test_congress_confirmations_share_the_confirmation_callback(self):
        self.assertTrue(re.fullmatch(CALLBACK_PATTERN, "congreso_confirm:" + "a" * 32))
        self.assertFalse(re.fullmatch(CALLBACK_PATTERN, "cdieta_absence_yes:" + "a" * 32))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_plugins tests.test_core_plugin_support -v`
Expected: FAIL/ERROR — `AttributeError: 'AppConfig' object has no attribute 'plugin_config'`, controllers not merged, `AttributeError: ... 'run'`, pattern does not match `congreso_confirm`.

- [ ] **Step 3: Add `plugin_config` to `fichaxebot/config.py`**

In `class AppConfig`, after `plugins: list[str] = field(default_factory=list)` add:

```python
    plugin_config: dict = field(default_factory=dict)
```

In `load_config`, after the `if len(plugins) != len(set(plugins)):` block add:

```python
    plugin_config = data.get("plugin_config", {})
    if not isinstance(plugin_config, dict) or any(
        not isinstance(name, str) or not isinstance(section, dict) for name, section in plugin_config.items()
    ):
        raise ValueError("'plugin_config' debe ser un objeto con una sección (objeto) por plugin")
```

and replace the final `        plugins=plugins\n    )` of the `return AppConfig(...)` call with:

```python
        plugins=plugins,
        plugin_config=plugin_config,
    )
```

- [ ] **Step 4: Merge plugin web app controllers in `fichaxebot/plugins.py`**

Add the import `from fichaxebot.webapp_controller.router import WEBAPP_CONTROLLERS` below the telegram import. In `register_plugins`, after the `used_commands = {...}` set add:

```python
    used_types = set(WEBAPP_CONTROLLERS)
    pending_controllers = {}
```

Inside the per-plugin `try`, after the `for command, callback in commands.items():` loop (before `setup = getattr(...)`) add:

```python
            controllers = getattr(plugin, "WEBAPP_CONTROLLERS", {})
            if not isinstance(controllers, Mapping):
                raise ValueError("WEBAPP_CONTROLLERS must map web app data types to async handlers")
            for data_type, handler in controllers.items():
                if not isinstance(data_type, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", data_type):
                    raise ValueError(f"invalid web app data type: {data_type!r}")
                if data_type in used_types:
                    raise ValueError(f"web app data type '{data_type}' is already registered")
                if not inspect.iscoroutinefunction(handler):
                    raise ValueError(f"handler for web app data type '{data_type}' must be an async function")
                pending_controllers[data_type] = handler
                used_types.add(data_type)
```

After `for handler in pending: application.add_handler(handler)` add:

```python
    WEBAPP_CONTROLLERS.update(pending_controllers)
```

and add to the docstring: `Plugins may also export WEBAPP_CONTROLLERS (web app data type -> async (update, context, data) handler).`

- [ ] **Step 5: Add `UscWebSession.run` in `fichaxebot/usc_api.py`**

Add after `fetch_absence_selection_data`:

```python
    def run(self, operation, *args, **kwargs):
        """Run operation(session, *args) holding the browser lock; for plugin scrapers."""
        with self._lock:
            return operation(self, *args, **kwargs)
```

- [ ] **Step 6: Accept the `congreso` confirmation prefix**

In `fichaxebot/webapp_controller/vacation_confirmation.py` replace

```python
CALLBACK_PATTERN = r'^(?:vacation|absence)_(confirm|cancel):[0-9a-f]{32}$'
```

with

```python
CALLBACK_PATTERN = r'^(?:vacation|absence|congreso)_(confirm|cancel):[0-9a-f]{32}$'
```

- [ ] **Step 7: `install.sh` and `.gitignore`**

In `install.sh` replace `    python3 -m venv "${VENV_PATH}"` with:

```bash
    # System site-packages expose LibreOffice's python3-uno to plugins.
    python3 -m venv --system-site-packages "${VENV_PATH}"
```

Append to `.gitignore`:

```
.plugin_data/
```

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_plugins tests.test_core_plugin_support -v && .venv/bin/python -m unittest discover -s tests -p "test_*.py"`
Expected: all PASS.

- [ ] **Step 9: Commit** (only with user approval)

```bash
git add fichaxebot/config.py fichaxebot/plugins.py fichaxebot/usc_api.py \
        fichaxebot/webapp_controller/vacation_confirmation.py install.sh .gitignore \
        tests/test_plugins.py tests/test_core_plugin_support.py
git commit -m "feat: plugin config, web app controllers and locked scraper runs for plugins"
```

---

### Task 2: Plugin config validation

**Files:**
- Create: `plugins/congreso_dieta/__init__.py` (docstring only in this task), `plugins/congreso_dieta/config.py`
- Test: `tests/test_congreso_config.py`

**Interfaces:**
- Produces (module `plugins.congreso_dieta.config`):
  - `NAME = "congreso_dieta"`
  - `PromptConfig(at: time, reminder_minutes: int, window_start: time, window_end: time)`
  - `SigningConfig(store: str, alias: str, password: str | None)`
  - `AbsenceConfig(type_name: str, start_time: str "HH:MM", end_time: str "HH:MM")`
  - `PluginConfig(webapp_url, output_dir: Path, auth_check_time: time, prompt, signing, days_before: int, spreadsheet_template: Path, absence, congress: dict)`
  - `parse_config(raw, *, today: date, check_files: bool = True) -> PluginConfig` — raises `ValueError("plugin_config.congreso_dieta: …")` naming the setting
- Produces (test helper): `tests.test_congreso_config.raw_config(directory: str) -> dict` (valid raw config with real template/output files)

- [ ] **Step 1: Create the package marker**

`plugins/congreso_dieta/__init__.py`:

```python
"""congreso_dieta plugin: congress authorization, absence and signed per-diem document."""
```

- [ ] **Step 2: Write the failing tests**

`tests/test_congreso_config.py`:

```python
import copy
import re
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from plugins.congreso_dieta.config import parse_config

TODAY = date(2026, 10, 1)
CONGRESS = {
    "data_processing_authorized": True,
    "address": {"country": "España", "province": "Coruña, A", "municipality": "Santiago de Compostela",
                "postal_code": "15782", "line1": "Rúa Exemplo 1"},
    "reason": "Asistencia a congreso", "organization": "Universidade de Exemplo",
    "employment_category": "PREDOUTORAIS", "teaching_assigned": False, "supervisor_query": "Persoa Supervisora",
}


def raw_config(directory):
    root = Path(directory)
    (root / "dietas").mkdir()
    template = root / "plantilla.xlsm"
    template.write_bytes(b"xlsm")
    return {
        "webapp_url": "https://example.test/congreso.html",
        "output_dir": str(root / "dietas"),
        "auth_check_time": "10:00",
        "prompt": {"at": "09:00", "reminder_minutes": 30, "window": ["08:00", "20:00"]},
        "signing": {"store": "mozilla", "alias": "Alias de proba", "password": None},
        "days_before": 3,
        "spreadsheet_template": str(template),
        "absence": {"type": "Asistencia a congresos", "start_time": "8:00", "end_time": "15:00"},
        "congress": copy.deepcopy(CONGRESS),
    }


class ConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.raw = raw_config(directory.name)

    def test_valid_config_is_parsed_and_normalised(self):
        config = parse_config(self.raw, today=TODAY)
        self.assertEqual((config.prompt.at, config.prompt.window_start, config.prompt.window_end),
                         (time(9), time(8), time(20)))
        self.assertEqual((config.absence.start_time, config.absence.end_time), ("08:00", "15:00"))
        self.assertEqual((config.days_before, config.signing.alias), (3, "Alias de proba"))
        self.assertEqual(config.auth_check_time, time(10))
        self.assertEqual(config.congress, CONGRESS)

    def test_invalid_values_name_the_setting(self):
        cases = [
            (("webapp_url",), "http://example.test", "webapp_url"),
            (("prompt", "at"), "25:00", "prompt.at"),
            (("prompt", "window"), ["20:00", "08:00"], "prompt.window"),
            (("prompt", "at"), "21:00", "prompt.at"),
            (("prompt", "reminder_minutes"), 0, "prompt.reminder_minutes"),
            (("days_before",), 0, "days_before"),
            (("signing", "store"), "windows", "signing.store"),
            (("signing", "alias"), " ", "signing.alias"),
            (("absence", "end_time"), "07:00", "absence.start_time"),
            (("output_dir",), "/nonexistent/dietas", "output_dir"),
            (("spreadsheet_template",), "/nonexistent/plantilla.xlsm", "spreadsheet_template"),
            (("congress", "start_date"), "2026-11-01", "congress"),
            (("congress", "data_processing_authorized"), False, "congress"),
        ]
        for path, value, expected in cases:
            with self.subTest(path=path, value=value):
                raw = copy.deepcopy(self.raw)
                target = raw
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(ValueError, re.escape(expected)):
                    parse_config(raw, today=TODAY)

    def test_missing_section_and_skipping_file_checks(self):
        with self.assertRaisesRegex(ValueError, "congreso_dieta"):
            parse_config(None, today=TODAY)
        raw = copy.deepcopy(self.raw)
        raw["output_dir"] = "/nonexistent/dietas"
        self.assertEqual(parse_config(raw, today=TODAY, check_files=False).output_dir, Path("/nonexistent/dietas"))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_config -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'plugins.congreso_dieta.config'`

- [ ] **Step 4: Implement `plugins/congreso_dieta/config.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_config -v`
Expected: all PASS.

- [ ] **Step 6: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/__init__.py plugins/congreso_dieta/config.py tests/test_congreso_config.py
git commit -m "feat(congreso_dieta): validate plugin configuration"
```

---

### Task 3: Date rules

**Files:**
- Create: `plugins/congreso_dieta/dates.py`
- Test: `tests/test_congreso_dates.py`

**Interfaces:**
- Consumes: `PromptConfig` (Task 2)
- Produces (module `plugins.congreso_dieta.dates`):
  - `MIN_NOTICE_DAYS = 5`; `earliest_start(today: date) -> date`
  - `parse_non_working(entries: list[str]) -> set[date]` (USC calendar payload codes `N<start>[:<end>]`)
  - `absence_days(start, end, non_working: set[date]) -> list[date]`
  - `next_slot(moment: datetime, prompt) -> datetime`; `first_prompt(start: date, days_before: int, prompt, now) -> datetime`
  - `next_reminder(now, prompt) -> datetime`; `prompt_deadline(start: date) -> datetime` (start 00:00)
  - `generation_day(end: date, auth_day: date) -> date`

- [ ] **Step 1: Write the failing tests**

`tests/test_congreso_dates.py`:

```python
import unittest
from datetime import date, datetime, time

from fichaxebot.utils import MADRID_TZ
from plugins.congreso_dieta import dates
from plugins.congreso_dieta.config import PromptConfig

PROMPT = PromptConfig(at=time(9, 0), reminder_minutes=30, window_start=time(8, 0), window_end=time(20, 0))


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=MADRID_TZ)


class DateRuleTests(unittest.TestCase):
    def test_absence_days_skip_weekends_holidays_and_usc_non_working_days(self):
        non_working = dates.parse_non_working(["N2026-10-13", "V2026-10-14", "N2026-12-24:2026-12-26"])
        self.assertIn(date(2026, 12, 25), non_working)
        self.assertNotIn(date(2026, 10, 14), non_working)
        # 10-11 weekend, 12 national holiday, 13 USC non-working.
        self.assertEqual(dates.absence_days(date(2026, 10, 9), date(2026, 10, 14), non_working),
                         [date(2026, 10, 9), date(2026, 10, 14)])

    def test_earliest_start_and_generation_day(self):
        self.assertEqual(dates.earliest_start(date(2026, 10, 1)), date(2026, 10, 6))
        self.assertEqual(dates.generation_day(date(2026, 10, 14), date(2026, 10, 2)), date(2026, 10, 15))
        self.assertEqual(dates.generation_day(date(2026, 10, 14), date(2026, 10, 20)), date(2026, 10, 20))

    def test_next_slot_stays_inside_the_window(self):
        self.assertEqual(dates.next_slot(at(1, 7), PROMPT), at(1, 8))
        self.assertEqual(dates.next_slot(at(1, 12, 5), PROMPT), at(1, 12, 5))
        self.assertEqual(dates.next_slot(at(1, 20), PROMPT), at(2, 8))

    def test_first_prompt_reminders_and_deadline(self):
        self.assertEqual(dates.first_prompt(date(2026, 10, 20), 3, PROMPT, at(1, 10)), at(17, 9))
        self.assertEqual(dates.first_prompt(date(2026, 10, 6), 7, PROMPT, at(1, 21)), at(2, 8))
        self.assertEqual(dates.next_reminder(at(1, 10), PROMPT), at(1, 10, 30))
        self.assertEqual(dates.next_reminder(at(1, 19, 45), PROMPT), at(2, 8))
        self.assertEqual(dates.prompt_deadline(date(2026, 10, 6)), at(6, 0))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_dates -v`
Expected: ERROR — `ImportError: cannot import name 'dates'`

- [ ] **Step 3: Implement `plugins/congreso_dieta/dates.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_dates -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/dates.py tests/test_congreso_dates.py
git commit -m "feat(congreso_dieta): date rules for absence days, prompts and generation"
```

---

### Task 4: Case store

**Files:**
- Create: `plugins/congreso_dieta/cases.py`
- Test: `tests/test_congreso_cases.py`

**Interfaces:**
- Produces (module `plugins.congreso_dieta.cases`):
  - `CASES_FILE`, `FILES_DIR` (under `CONFIG_FILE.parent / ".plugin_data"`)
  - `Case` dataclass: `id, start, end` (ISO strings), `config: dict`, `request_id=None`, `simulated=False`, `absence="scheduled"` (`scheduled|asking|requesting|requested|uncertain|skipped|simulated|not_requested`), `stage="awaiting_auth"` (`awaiting_auth|auth_received|generated|signed`), `prompt_token=None`, `auth_date=None`, `last_problem=None`, `check_failures=0`, `notified_state=None`, `unsigned_saved=False`; properties `start_date`, `end_date`; `Case.new(start: date, end: date, config: dict, *, simulated=False)`
  - `CaseStore(path=CASES_FILE, files_dir=FILES_DIR)`: `load()`, `save()`, `add(case)`, `get(id)`, `by_prompt_token(token)`, `open_cases()` (sorted by start), `overlaps(start, end) -> bool`, `directory(case) -> Path` (created), `remove(id)` (deletes its directory)

- [ ] **Step 1: Write the failing tests**

`tests/test_congreso_cases.py`:

```python
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fichaxebot import config
from plugins.congreso_dieta import cases
from plugins.congreso_dieta.cases import Case, CaseStore


class CaseStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.path, self.files = root / "cases.json", root / "files"
        self.store = CaseStore(self.path, self.files)

    def reloaded(self):
        store = CaseStore(self.path, self.files)
        store.load()
        return store

    def test_cases_round_trip_and_sort_by_start(self):
        late = Case.new(date(2026, 11, 2), date(2026, 11, 3), {"k": 1})
        early = Case.new(date(2026, 10, 20), date(2026, 10, 21), {"k": 2}, simulated=True)
        self.store.add(late)
        self.store.add(early)
        store = self.reloaded()
        self.assertEqual([case.id for case in store.open_cases()], [early.id, late.id])
        self.assertTrue(store.get(early.id).simulated)
        self.assertEqual(store.get(late.id).config, {"k": 1})
        self.assertEqual(store.get(late.id).start_date, date(2026, 11, 2))

    def test_overlaps_include_touching_days(self):
        self.store.add(Case.new(date(2026, 10, 20), date(2026, 10, 22), {}))
        self.assertTrue(self.store.overlaps(date(2026, 10, 22), date(2026, 10, 23)))
        self.assertTrue(self.store.overlaps(date(2026, 10, 18), date(2026, 10, 25)))
        self.assertFalse(self.store.overlaps(date(2026, 10, 23), date(2026, 10, 24)))

    def test_remove_deletes_the_case_and_its_directory(self):
        case = Case.new(date(2026, 10, 20), date(2026, 10, 22), {})
        self.store.add(case)
        folder = self.store.directory(case)
        (folder / "autorizacion.pdf").write_bytes(b"%PDF-")
        self.store.remove(case.id)
        self.assertIsNone(self.store.get(case.id))
        self.assertFalse(folder.exists())
        self.assertEqual(self.reloaded().open_cases(), [])

    def test_prompt_token_lookup(self):
        case = Case.new(date(2026, 10, 20), date(2026, 10, 22), {})
        case.prompt_token = "a" * 32
        self.store.add(case)
        self.assertIs(self.store.by_prompt_token("a" * 32), case)
        self.assertIsNone(self.store.by_prompt_token("b" * 32))

    def test_default_locations_live_next_to_config(self):
        self.assertEqual(cases.CASES_FILE, config.CONFIG_FILE.parent / ".plugin_data" / "congreso_dieta.json")
        self.assertEqual(cases.FILES_DIR, config.CONFIG_FILE.parent / ".plugin_data" / "congreso_dieta")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_cases -v`
Expected: ERROR — `ImportError: cannot import name 'cases'`

- [ ] **Step 3: Implement `plugins/congreso_dieta/cases.py`**

```python
"""Persisted congress cases and their working files."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fichaxebot.config import CONFIG_FILE
from fichaxebot.storage import write_json_atomic

DATA_DIR = CONFIG_FILE.parent / ".plugin_data"
CASES_FILE = DATA_DIR / "congreso_dieta.json"
FILES_DIR = DATA_DIR / "congreso_dieta"


@dataclass
class Case:
    id: str
    start: str
    end: str
    config: dict
    request_id: Optional[str] = None
    simulated: bool = False
    absence: str = "scheduled"
    stage: str = "awaiting_auth"
    prompt_token: Optional[str] = None
    auth_date: Optional[str] = None
    last_problem: Optional[str] = None
    check_failures: int = 0
    notified_state: Optional[str] = None
    unsigned_saved: bool = False

    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.start)

    @property
    def end_date(self) -> date:
        return date.fromisoformat(self.end)

    @classmethod
    def new(cls, start: date, end: date, config: dict, *, simulated: bool = False) -> "Case":
        return cls(id=uuid4().hex, start=start.isoformat(), end=end.isoformat(), config=config, simulated=simulated)


class CaseStore:
    def __init__(self, path: Path = CASES_FILE, files_dir: Path = FILES_DIR) -> None:
        self._path = Path(path)
        self._files = Path(files_dir)
        self._cases: dict[str, Case] = {}

    def load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._cases = {item["id"]: Case(**item) for item in data["cases"]}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self._path, {"version": 1, "cases": [asdict(case) for case in self.open_cases()]})

    def add(self, case: Case) -> None:
        self._cases[case.id] = case
        self.save()

    def get(self, case_id) -> Optional[Case]:
        return self._cases.get(case_id)

    def by_prompt_token(self, token: str) -> Optional[Case]:
        return next((case for case in self._cases.values() if case.prompt_token == token), None)

    def open_cases(self) -> list[Case]:
        return sorted(self._cases.values(), key=lambda case: (case.start, case.id))

    def overlaps(self, start: date, end: date) -> bool:
        return any(case.start_date <= end and start <= case.end_date for case in self._cases.values())

    def directory(self, case: Case) -> Path:
        path = self._files / case.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def remove(self, case_id: str) -> None:
        self._cases.pop(case_id, None)
        shutil.rmtree(self._files / case_id, ignore_errors=True)
        self.save()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_cases -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/cases.py tests/test_congreso_cases.py
git commit -m "feat(congreso_dieta): persisted case store"
```

---

### Task 5: USC request pages

**Files:**
- Create: `plugins/congreso_dieta/usc.py`, `tests/fixtures/congreso_solicitude.html` (sanitised)
- Test: `tests/test_congreso_usc.py`

**Interfaces:**
- Produces (module `plugins.congreso_dieta.usc`):
  - `REQUEST_URL` (format with `id`), `REQUESTS_LIST_URL`, `UscPageError(RuntimeError)`
  - `RequestStatus(state: str, authorization_url: str | None)` with `signed` (state `Tramitada` and a link) and `rejected` (state contains deneg/anulad/desist/rexeit/rechaz/arquiv/revogad)
  - `parse_request_detail(html: str) -> RequestStatus`
  - `fetch_request_status(session, request_id: str) -> RequestStatus` — run through `session.run(...)`
  - `download_authorization(session, url: str) -> bytes` — run through `session.run(...)`

- [ ] **Step 1: Create the sanitised fixture**

`tests/fixtures/congreso_solicitude.html` (same structure as the real detail page, fake data):

```html
<!DOCTYPE html>
<html><body>
<div class="container">
  <h2>Autorización de permisos para a asistencia a congresos</h2>
  <div class="mt-2">
    <p>Recorde o código da solicitude para resolver posibles incidencias:&nbsp;100001.</p>
    <p>É aconsellable que descargue e imprima o
      <a class="recortar" href="https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm?solicitudeId=100001&amp;csv=AAAA-1111-BBBB-2222">xustificante.
      </a>
    </p>
  </div>
  <div class="mt-2">
    <p>Código da solicitude: 100001</p>
    <p>Interesado: PERSOA DE PROBA [00000000T]</p>
  </div>
  <div class="mt-2">
    <h3>Datos de tramitación</h3>
    <p>Estado: Tramitada</p>
    <p>Data de estado: 25/09/2026 09:14</p>
  </div>
  <h3>Documentos da solicitude</h3>
  <p>A solicitude consta dos seguintes documentos electrónicos:
    <ul>
      <li class="mt-2"><a class="recortar" href="https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm?solicitudeId=100001&amp;csv=CCCC-3333-DDDD-4444">Solicitude
      </a></li>
      <li class="mt-2"><a class="recortar" href="https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm?solicitudeId=100001&amp;csv=AAAA-1111-BBBB-2222">Xustificante solicitude
      </a></li>
      <li class="mt-2"><a class="recortar" href="https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm?solicitudeId=100001&amp;csv=EEEE-5555-FFFF-6666">Autorización
      </a></li>
    </ul>
  </p>
</div>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

`tests/test_congreso_usc.py`:

```python
import base64
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from plugins.congreso_dieta import usc

FIXTURE = (Path(__file__).parent / "fixtures" / "congreso_solicitude.html").read_text(encoding="utf-8")
AUTH_URL = ("https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm"
            "?solicitudeId=100001&csv=EEEE-5555-FFFF-6666")
WITHOUT_AUTHORIZATION = re.sub(r'<li class="mt-2"><a[^>]*>Autorización\s*</a></li>', "", FIXTURE)


class DetailParsingTests(unittest.TestCase):
    def test_signed_request_exposes_the_authorization_link(self):
        status = usc.parse_request_detail(FIXTURE)
        self.assertEqual((status.state, status.authorization_url), ("Tramitada", AUTH_URL))
        self.assertTrue(status.signed)
        self.assertFalse(status.rejected)

    def test_in_progress_and_rejected_states(self):
        pending = usc.parse_request_detail(WITHOUT_AUTHORIZATION.replace("Estado: Tramitada", "Estado: En tramitación"))
        self.assertEqual((pending.state, pending.authorization_url), ("En tramitación", None))
        self.assertFalse(pending.signed or pending.rejected)
        rejected = usc.parse_request_detail(FIXTURE.replace("Estado: Tramitada", "Estado: Denegada"))
        self.assertTrue(rejected.rejected)
        self.assertFalse(rejected.signed)

    def test_page_without_state_or_with_foreign_link_is_an_error(self):
        with self.assertRaises(usc.UscPageError):
            usc.parse_request_detail("<html><body><p>Login</p></body></html>")
        foreign = FIXTURE.replace("https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm"
                                  "?solicitudeId=100001&amp;csv=EEEE", "https://evil.test/csv.htm?csv=EEEE")
        with self.assertRaises(usc.UscPageError):
            usc.parse_request_detail(foreign)


class FetchTests(unittest.TestCase):
    def test_fetch_opens_the_detail_page(self):
        session = SimpleNamespace(driver=SimpleNamespace(page_source=FIXTURE), _ensure_access_to=Mock())
        self.assertTrue(usc.fetch_request_status(session, "100001").signed)
        session._ensure_access_to.assert_called_once_with(
            "https://aplicacions.usc.es/intranet/solicitudes/solicitude/100001/ver.htm")
        with self.assertRaises(usc.UscPageError):
            usc.fetch_request_status(session, "../100001")

    def test_download_returns_pdf_bytes_only(self):
        driver = Mock()
        session = SimpleNamespace(driver=driver)
        driver.execute_async_script.return_value = {"data": base64.b64encode(b"%PDF-1.7 auth").decode()}
        self.assertEqual(usc.download_authorization(session, AUTH_URL), b"%PDF-1.7 auth")
        driver.execute_async_script.return_value = {"data": base64.b64encode(b"<html>login</html>").decode()}
        with self.assertRaises(usc.UscPageError):
            usc.download_authorization(session, AUTH_URL)
        driver.execute_async_script.return_value = {"error": "HTTP 500"}
        with self.assertRaises(usc.UscPageError):
            usc.download_authorization(session, AUTH_URL)
        with self.assertRaises(usc.UscPageError):
            usc.download_authorization(session, "https://evil.test/autorizacion.pdf")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_usc -v`
Expected: ERROR — `ImportError: cannot import name 'usc'`

- [ ] **Step 4: Implement `plugins/congreso_dieta/usc.py`**

```python
"""USC requests intranet: congress authorization status and download."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlsplit

REQUEST_URL = "https://aplicacions.usc.es/intranet/solicitudes/solicitude/{id}/ver.htm"
REQUESTS_LIST_URL = "https://aplicacions.usc.es/intranet/solicitudes/solicitudes/listaxe.htm"
DOCUMENT_PATH = "/intranet/solicitudes/documento/csv.htm"
SIGNED_STATE = "tramitada"
# Only the signed state is known from real pages; these words mark states that will not become signed.
_NEGATIVE_WORDS = ("deneg", "anulad", "desist", "rexeit", "rechaz", "arquiv", "revogad")


class UscPageError(RuntimeError):
    """The USC requests page could not be read or returned unexpected content."""


@dataclass(frozen=True)
class RequestStatus:
    state: str
    authorization_url: Optional[str]

    @property
    def signed(self) -> bool:
        return self.state.casefold() == SIGNED_STATE and self.authorization_url is not None

    @property
    def rejected(self) -> bool:
        return any(word in self.state.casefold() for word in _NEGATIVE_WORDS)


class _DetailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.state = None
        self.authorization_url = None
        self._paragraph = None
        self._link = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self._paragraph = []
        elif tag == "a":
            self._link = (dict(attrs).get("href"), [])

    def handle_endtag(self, tag):
        if tag == "p" and self._paragraph is not None:
            text = " ".join("".join(self._paragraph).split())
            if text.startswith("Estado:") and self.state is None:
                self.state = text[len("Estado:"):].strip()
            self._paragraph = None
        elif tag == "a" and self._link is not None:
            href, parts = self._link
            if " ".join("".join(parts).split()) == "Autorización" and href and self.authorization_url is None:
                self.authorization_url = href
            self._link = None

    def handle_data(self, data):
        if self._paragraph is not None:
            self._paragraph.append(data)
        if self._link is not None:
            self._link[1].append(data)


def _checked_document_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "aplicacions.usc.es" or parts.path != DOCUMENT_PATH:
        raise UscPageError("El enlace de la autorización no corresponde a USC.")
    return url


def parse_request_detail(html: str) -> RequestStatus:
    parser = _DetailParser()
    parser.feed(html)
    if not parser.state:
        raise UscPageError("No se encontró el estado de la solicitude en USC.")
    url = _checked_document_url(parser.authorization_url) if parser.authorization_url else None
    return RequestStatus(parser.state, url)


def fetch_request_status(session, request_id: str) -> RequestStatus:
    if not str(request_id).isdecimal():
        raise UscPageError("El identificador de la solicitude no es válido.")
    session._ensure_access_to(REQUEST_URL.format(id=request_id))
    return parse_request_detail(session.driver.page_source)


def download_authorization(session, url: str) -> bytes:
    _checked_document_url(url)
    # Fetch in the page so the authenticated cookie jar is used.
    response = session.driver.execute_async_script("""
        const url = arguments[0], done = arguments[arguments.length - 1];
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
        fetch(url, {credentials: 'same-origin', signal: controller.signal})
          .then(async response => {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const bytes = new Uint8Array(await response.arrayBuffer());
            let binary = '';
            for (let i = 0; i < bytes.length; i += 8192)
                binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
            done({data: btoa(binary)});
          }).catch(error => done({error: String(error)})).finally(() => clearTimeout(timer));
    """, url)
    try:
        document = base64.b64decode(response["data"], validate=True)
        if not document.startswith(b"%PDF-"):
            raise ValueError("not a PDF")
    except (KeyError, TypeError, ValueError) as exc:
        raise UscPageError("No se pudo descargar la autorización en PDF.") from exc
    return document
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_usc -v`
Expected: all PASS.

- [ ] **Step 6: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/usc.py tests/test_congreso_usc.py tests/fixtures/congreso_solicitude.html
git commit -m "feat(congreso_dieta): read congress authorization status and document"
```

---

### Task 6: Spreadsheet PDF through the workbook macro

**Files:**
- Create: `plugins/congreso_dieta/spreadsheet.py`
- Test: `tests/test_congreso_spreadsheet.py`

**Interfaces:**
- Produces (module `plugins.congreso_dieta.spreadsheet`):
  - `SpreadsheetError(RuntimeError)`; `date_serial(day) -> int`
  - `SheetDates(departure: date, return_: date, declaration: date)` with `cells() -> {"I17", "N17", "Q78"}`
  - `LibreOffice(*, timeout=180, command=<factory>)` context manager with public `process`; `generate(workbook: Path, dates) -> Path` (blocking)
  - `generate_pdf(template: Path, workdir: Path, dates: SheetDates, *, office_factory=LibreOffice) -> Path` — copies the template into `workdir`, runs the macro, returns `<workdir>/<P1>.pdf`

- [ ] **Step 1: Write the failing tests**

`tests/test_congreso_spreadsheet.py`:

```python
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from plugins.congreso_dieta import spreadsheet
from plugins.congreso_dieta.spreadsheet import SheetDates

DATES = SheetDates(date(2026, 10, 12), date(2026, 10, 14), date(2026, 10, 15))


class FakeOffice:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def generate(self, workbook, dates):
        FakeOffice.calls.append((workbook, dates))
        pdf = workbook.parent / "CL1.pdf"
        pdf.write_bytes(b"%PDF-sheet")
        return pdf


class GeneratePdfTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_works_on_a_copy_and_returns_the_macro_pdf(self):
        template = self.root / "plantilla.xlsm"
        template.write_bytes(b"original")
        workdir = self.root / "case" / "hoja"
        pdf = spreadsheet.generate_pdf(template, workdir, DATES, office_factory=FakeOffice)
        self.assertEqual(pdf, workdir / "CL1.pdf")
        workbook, received = FakeOffice.calls[-1]
        self.assertEqual((workbook.parent, workbook.read_bytes(), received), (workdir, b"original", DATES))
        self.assertEqual(template.read_bytes(), b"original")

    def test_cells_and_date_serials(self):
        self.assertEqual(spreadsheet.date_serial(date(2026, 10, 12)), 46307)
        self.assertEqual(DATES.cells(), {"I17": date(2026, 10, 12), "N17": date(2026, 10, 14), "Q78": date(2026, 10, 15)})


class WatchdogTests(unittest.TestCase):
    def test_hung_office_process_is_killed_after_the_timeout(self):
        office = spreadsheet.LibreOffice(
            timeout=0.3, command=lambda profile, pipe: [sys.executable, "-c", "import time; time.sleep(30)"])
        with office:
            time.sleep(1.5)
            self.assertIsNotNone(office.process.poll())


@unittest.skipUnless(os.environ.get("CONGRESO_TEMPLATE"), "Set CONGRESO_TEMPLATE to a real workbook to run LibreOffice")
class LibreOfficeIntegrationTests(unittest.TestCase):
    def test_macro_fills_the_dates_and_exports_the_form(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = spreadsheet.generate_pdf(Path(os.environ["CONGRESO_TEMPLATE"]), Path(directory) / "hoja", DATES)
            text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                                  capture_output=True, text=True, check=True).stdout
        for value in ("12/10/2026", "14/10/2026", "15/10/2026"):
            self.assertIn(value, text)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_spreadsheet -v`
Expected: ERROR — `ImportError: cannot import name 'spreadsheet'`

- [ ] **Step 3: Implement `plugins/congreso_dieta/spreadsheet.py`**

```python
"""Fill the per-diem workbook and run its CreaPDF macro in a private headless LibreOffice."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

SHEET = "FormularioCL"
MACRO = "vnd.sun.star.script:VBAProject.Módulo1.CreaPDF?language=Basic&location=document"
TIMEOUT_SECONDS = 180
CONNECT_SECONDS = 60
ALWAYS_EXECUTE_NO_WARN = 4  # com.sun.star.document.MacroExecMode


class SpreadsheetError(RuntimeError):
    """The workbook PDF could not be generated."""


@dataclass(frozen=True)
class SheetDates:
    departure: date
    return_: date
    declaration: date

    def cells(self) -> dict[str, date]:
        return {"I17": self.departure, "N17": self.return_, "Q78": self.declaration}


def date_serial(day: date) -> int:
    """Spreadsheet date value (days since 1899-12-30)."""
    return (day - date(1899, 12, 30)).days


def _soffice_command(profile: Path, pipe: str) -> list[str]:
    return ["soffice", "--headless", "--invisible", "--nologo", "--norestore", "--nodefault",
            f"-env:UserInstallation={profile.as_uri()}", f"--accept=pipe,name={pipe};urp;"]


class LibreOffice:
    """One private soffice process with its own profile; generate() blocks (use a worker thread)."""

    def __init__(self, *, timeout: float = TIMEOUT_SECONDS, command=_soffice_command) -> None:
        self._timeout = timeout
        self._command = command
        self._pipe = f"fichaxe_{uuid4().hex}"
        self._profile = None
        self._watchdog = None
        self.process = None

    def __enter__(self) -> "LibreOffice":
        self._profile = tempfile.TemporaryDirectory(prefix="fichaxe-lo-")
        self.process = subprocess.Popen(self._command(Path(self._profile.name), self._pipe),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # A hung LibreOffice is killed; blocked UNO calls then fail instead of waiting forever.
        self._watchdog = threading.Timer(self._timeout, self.process.kill)
        self._watchdog.daemon = True
        self._watchdog.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._watchdog.cancel()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._profile.cleanup()
        return False

    def _connect(self):
        import uno  # LibreOffice's python3-uno, visible through the venv's system site-packages

        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        deadline = time.monotonic() + CONNECT_SECONDS
        while True:
            try:
                return resolver.resolve(f"uno:pipe,name={self._pipe};urp;StarOffice.ComponentContext")
            except Exception as exc:  # noqa: BLE001 - NoConnectException until soffice listens
                if time.monotonic() > deadline or self.process.poll() is not None:
                    raise SpreadsheetError("No se pudo iniciar LibreOffice.") from exc
                time.sleep(0.5)

    def generate(self, workbook: Path, dates: SheetDates) -> Path:
        try:
            import uno  # noqa: F401 - installs the import hook that makes com.sun.star importable
            from com.sun.star.beans import PropertyValue

            def prop(name, value):
                item = PropertyValue()
                item.Name, item.Value = name, value
                return item

            context = self._connect()
            desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
            document = desktop.loadComponentFromURL(
                workbook.as_uri(), "_blank", 0,
                (prop("Hidden", True), prop("MacroExecutionMode", ALWAYS_EXECUTE_NO_WARN)))
            try:
                sheet = document.Sheets.getByName(SHEET)
                document.CurrentController.setActiveSheet(sheet)
                for cell, day in dates.cells().items():
                    sheet.getCellRangeByName(cell).setValue(date_serial(day))
                document.getScriptProvider().getScript(MACRO).invoke((), (), ())
                code = sheet.getCellRangeByName("P1").getString().strip()
            finally:
                document.close(True)
        except SpreadsheetError:
            raise
        except Exception as exc:  # noqa: BLE001 - UNO raises its own exception types
            raise SpreadsheetError(f"LibreOffice no pudo generar la hoja: {exc}") from exc
        pdf = workbook.parent / f"{code}.pdf"
        if not code or not pdf.is_file():
            raise SpreadsheetError("La macro CreaPDF no generó el PDF.")
        return pdf


def generate_pdf(template: Path, workdir: Path, dates: SheetDates, *, office_factory=LibreOffice) -> Path:
    """Copy the template into workdir, fill the dates and run CreaPDF; the template is never modified."""
    workdir.mkdir(parents=True, exist_ok=True)
    workbook = workdir / "hoja.xlsm"
    shutil.copyfile(template, workbook)
    with office_factory() as office:
        return office.generate(workbook, dates)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_spreadsheet -v`
Expected: PASS; `LibreOfficeIntegrationTests` skipped.

- [ ] **Step 5: Run the integration test against the real workbook**

Run: `CONGRESO_TEMPLATE="$PWD/resources/per_diem/GL_VISITAS_PLANTA_FINSA_04_03_2026.xlsm" .venv/bin/python -m unittest tests.test_congreso_spreadsheet.LibreOfficeIntegrationTests -v`
Expected: PASS (reproduces the 2026-09-25 spike through the plugin code; the template file is unchanged afterwards — check with `git status resources/` showing no new files besides the untracked template itself).

- [ ] **Step 6: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/spreadsheet.py tests/test_congreso_spreadsheet.py
git commit -m "feat(congreso_dieta): generate the per-diem PDF with the workbook macro"
```

---

### Task 7: Join and sign

**Files:**
- Create: `plugins/congreso_dieta/pdf.py`
- Test: `tests/test_congreso_pdf.py`

**Interfaces:**
- Consumes: `SigningConfig` (Task 2)
- Produces (module `plugins.congreso_dieta.pdf`):
  - `PdfError(RuntimeError)`; `run(command, timeout) -> subprocess.CompletedProcess`
  - `join_pdfs(first: Path, second: Path, out: Path, *, runner=run) -> Path`
  - `sign_command(src, out, signing) -> list`; `sign_pdf(src: Path, out: Path, signing, *, runner=run) -> Path`

- [ ] **Step 1: Write the failing tests**

`tests/test_congreso_pdf.py`:

```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from plugins.congreso_dieta import pdf
from plugins.congreso_dieta.config import SigningConfig

SIGNING = SigningConfig("mozilla", "Alias", None)


def completed(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def minimal_pdf() -> bytes:
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


class PdfTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)


class JoinTests(PdfTestCase):
    def test_join_runs_pdfunite_spreadsheet_first(self):
        calls = []

        def runner(command, timeout):
            calls.append(command)
            Path(command[-1]).write_bytes(b"%PDF-joined")
            return completed()

        out = self.root / "unido.pdf"
        self.assertEqual(pdf.join_pdfs(self.root / "hoja.pdf", self.root / "auth.pdf", out, runner=runner), out)
        self.assertEqual(calls, [["pdfunite", self.root / "hoja.pdf", self.root / "auth.pdf", out]])

    def test_join_failure_reports_the_tool_message(self):
        with self.assertRaisesRegex(pdf.PdfError, "Syntax Error"):
            pdf.join_pdfs(self.root / "a.pdf", self.root / "b.pdf", self.root / "unido.pdf",
                          runner=lambda command, timeout: completed(1, err="Syntax Error: bad xref"))


class SignTests(PdfTestCase):
    def test_sign_command_uses_store_alias_and_optional_password(self):
        self.assertEqual(pdf.sign_command(Path("in.pdf"), Path("out.pdf"), SIGNING),
                         ["autofirma", "sign", "-i", Path("in.pdf"), "-o", Path("out.pdf"), "-format", "pades",
                          "-store", "mozilla", "-alias", "Alias"])
        self.assertEqual(pdf.sign_command(Path("in.pdf"), Path("out.pdf"),
                                          SigningConfig("pkcs12:/c.p12", "A", "pw"))[-2:], ["-password", "pw"])

    def test_sign_success_and_failures(self):
        out = self.root / "firmado.pdf"

        def ok(command, timeout):
            Path(command[5]).write_bytes(b"%PDF-signed")
            return completed()

        self.assertEqual(pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=ok), out)
        java = "Exception in thread main\n    at es.gob.Foo(Foo.java:1)\nNo se encontro el alias"
        with self.assertRaisesRegex(pdf.PdfError, "No se encontro el alias"):
            pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=lambda command, timeout: completed(0, out=java))
        self.assertFalse(out.exists())

        def hang(command, timeout):
            raise subprocess.TimeoutExpired(command, timeout)

        with self.assertRaisesRegex(pdf.PdfError, "no respondió"):
            pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=hang)


@unittest.skipUnless(os.environ.get("CONGRESO_SIGN_ALIAS"), "Set CONGRESO_SIGN_ALIAS to sign a dummy PDF with AutoFirma")
class RealSigningTests(PdfTestCase):
    def test_autofirma_signs_a_dummy_pdf(self):
        source = self.root / "dummy.pdf"
        source.write_bytes(minimal_pdf())
        signing = SigningConfig(os.environ.get("CONGRESO_SIGN_STORE", "mozilla"), os.environ["CONGRESO_SIGN_ALIAS"],
                                os.environ.get("CONGRESO_SIGN_PASSWORD"))
        signed = pdf.sign_pdf(source, self.root / "signed.pdf", signing)
        self.assertIn(b"/ByteRange", signed.read_bytes())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_pdf -v`
Expected: ERROR — `ImportError: cannot import name 'pdf'`

- [ ] **Step 3: Implement `plugins/congreso_dieta/pdf.py`**

```python
"""Join the spreadsheet and authorization PDFs and sign the result with AutoFirma's command line."""
from __future__ import annotations

import subprocess
from pathlib import Path

from plugins.congreso_dieta.config import SigningConfig

JOIN_TIMEOUT = 60
SIGN_TIMEOUT = 180


class PdfError(RuntimeError):
    """Joining or signing failed."""


def run(command, timeout) -> subprocess.CompletedProcess:
    return subprocess.run([str(part) for part in command], capture_output=True, text=True, timeout=timeout)


def _message(result) -> str:
    # AutoFirma prints Java stack traces; the last line that is not a frame is the useful one.
    lines = [line.strip() for line in f"{result.stderr or ''}\n{result.stdout or ''}".splitlines()]
    lines = [line for line in lines if line and not line.startswith("at ")]
    return lines[-1] if lines else f"código de salida {result.returncode}"


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.read_bytes()[:5] == b"%PDF-"


def join_pdfs(first: Path, second: Path, out: Path, *, runner=run) -> Path:
    try:
        result = runner(["pdfunite", first, second, out], JOIN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfError(f"No se pudieron unir los PDF: {exc}") from exc
    if result.returncode != 0 or not _is_pdf(out):
        raise PdfError(f"No se pudieron unir los PDF: {_message(result)}")
    return out


def sign_command(src: Path, out: Path, signing: SigningConfig) -> list:
    command = ["autofirma", "sign", "-i", src, "-o", out, "-format", "pades",
               "-store", signing.store, "-alias", signing.alias]
    if signing.password:
        command += ["-password", signing.password]
    return command


def sign_pdf(src: Path, out: Path, signing: SigningConfig, *, runner=run) -> Path:
    out.unlink(missing_ok=True)
    try:
        result = runner(sign_command(src, out, signing), SIGN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfError(f"AutoFirma no respondió: {exc}") from exc
    if result.returncode != 0 or not _is_pdf(out):
        out.unlink(missing_ok=True)
        raise PdfError(f"AutoFirma no firmó el documento: {_message(result)}")
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_pdf -v`
Expected: PASS; `RealSigningTests` skipped.

- [ ] **Step 5: Real signature on a dummy PDF** (the user runs it; it uses their certificate)

Run: `CONGRESO_SIGN_ALIAS="<alias from 'autofirma listaliases -store mozilla'>" .venv/bin/python -m unittest tests.test_congreso_pdf.RealSigningTests -v`
Expected: PASS. Repeat with Firefox open to confirm the store can be read while Firefox runs.

- [ ] **Step 6: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/pdf.py tests/test_congreso_pdf.py
git commit -m "feat(congreso_dieta): join PDFs and sign with AutoFirma"
```

---

### Task 8: Workflow — Mini App launch, new request, cancellation

**Files:**
- Create: `plugins/congreso_dieta/flow.py`
- Test: `tests/test_congreso_flow.py`

**Interfaces:**
- Consumes: Tasks 2–7; `TaskScheduler.schedule/cancel/pending`; `PendingVacation`, `ACTIVE_KEY`, `STOPPING_KEY`; `_build_vacations_url`; `UscWebSession.submit_congress_request(data, confirm)`
- Produces (module `plugins.congreso_dieta.flow`):
  - `PROMPT_KIND = "congreso_dieta.absence_prompt"`, `DAILY_JOB = "congreso_dieta.daily"`, `CALLBACK_PATTERN`, `ABSENCES_URL`
  - `describe(case) -> str`, `span(case) -> str`, `document_name(case) -> str`
  - `CongresoDieta(application, config, raw_config, store, *, clock=get_madrid_now, generate=spreadsheet.generate_pdf, join=pdf.join_pdfs, sign=pdf.sign_pdf)` with `launch_token`, `open_app(update, context)`, `check_range(start, end, today) -> str | None`, `handle_new(update, context, data)`, `handle_cancel(update, context, data)`, `handle_callback(update, context)`, `schedule_prompt(case, now)`, `schedule_reminder(case)`, `cancel_prompts(case)`, `notify(text, **kwargs)`
  - Tasks 9–10 add `run_absence_prompt(context, task)` and `run_daily(context)`
- Produces (test harness in `tests/test_congreso_flow.py`): `NOW`, `FakeSession`, `FlowTestCase` (`plugin`, `store`, `scheduler`, `session`, `app`, `clock`, `config`, `raw`, `generated`, `add_case(...)`, `texts()`, `message()`, `update(message)`, `fake_generate/fake_join/fake_sign`)

- [ ] **Step 1: Write the failing tests (harness + this task's tests)**

`tests/test_congreso_flow.py`:

```python
import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote
from uuid import uuid4

from fichaxebot.scheduler import Interrupted, Misfire, TaskScheduler
from fichaxebot.scrap_functions.absence_request import AbsenceRequestError, AbsenceRequestUncertain
from fichaxebot.scrap_functions.commit import ReadOnlyStop
from fichaxebot.scrap_functions.congress_request import CongressRequestError
from fichaxebot.utils import MADRID_TZ
from fichaxebot.webapp_controller.vacation_confirmation import ACTIVE_KEY
from plugins.congreso_dieta import flow, pdf, usc
from plugins.congreso_dieta.cases import Case, CaseStore
from plugins.congreso_dieta.config import parse_config
from plugins.congreso_dieta.spreadsheet import SheetDates
from tests.scheduler_fakes import Clock, fake_app, fire
from tests.test_congreso_config import raw_config

NOW = datetime(2026, 10, 1, 10, 0, tzinfo=MADRID_TZ)  # Thursday
AUTH_URL = "https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm?solicitudeId=100001&csv=E"


class FakeSession:
    def __init__(self):
        self.congress_calls, self.absence_calls = [], []
        self.congress_result = {"status": "unverified", "submission_attempted": True}
        self.absence_result = {"id": "9", "state": "Solicitada"}
        self.catalog = {"years": [2026], "types": [{"id": "7", "name": "Asistencia  a congresos", "requiresHours": True}]}
        self.calendar = ["N2026-10-13"]

    def submit_congress_request(self, data, confirm=None):
        self.congress_calls.append(data)
        if isinstance(self.congress_result, Exception):
            raise self.congress_result
        return self.congress_result

    def submit_absence_request(self, data, confirm=None):
        self.absence_calls.append(data)
        if isinstance(self.absence_result, Exception):
            raise self.absence_result
        return self.absence_result

    def fetch_absence_selection_data(self):
        return self.catalog

    def fetch_calendar_summary(self, *, for_vacation_selection=False):
        return self.calendar

    def run(self, operation, *args):
        return operation(self, *args)


class FlowTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.raw = raw_config(directory.name)
        self.config = parse_config(self.raw, today=NOW.date())
        self.clock = Clock(NOW)
        self.app = fake_app()
        self.app.bot.send_document = AsyncMock()
        self.tasks = []

        def create_task(coro, **kwargs):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

        self.app.create_task = create_task
        self.app.web_session = self.session = FakeSession()
        self.app.scheduler = self.scheduler = TaskScheduler("123", path=self.root / "schedule.json", clock=self.clock)
        self.store = CaseStore(self.root / "cases.json", self.root / "files")
        self.global_config = SimpleNamespace(
            telegram_chat_id="123", absence_confirmation_enabled=False, absence_confirmation_timeout_seconds=60,
            vacation_confirmation_timeout_seconds=60)
        patcher = patch.object(flow, "get_config", return_value=self.global_config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.generated = []
        self.plugin = flow.CongresoDieta(self.app, self.config, self.raw, self.store, clock=self.clock,
                                         generate=self.fake_generate, join=self.fake_join, sign=self.fake_sign)
        self.scheduler.register_kind(flow.PROMPT_KIND, self.plugin.run_absence_prompt, misfire=Misfire.RUN_LATE,
                                     interrupted=Interrupted.RETRY)
        self.scheduler.start(self.app)

    async def asyncTearDown(self):
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def fake_generate(self, template, workdir, sheet_dates):
        workdir.mkdir(parents=True, exist_ok=True)
        out = workdir / "CL1.pdf"
        out.write_bytes(b"%PDF-sheet")
        self.generated.append(sheet_dates)
        return out

    def fake_join(self, first, second, out):
        out.write_bytes(first.read_bytes() + second.read_bytes())
        return out

    def fake_sign(self, src, out, signing):
        out.write_bytes(b"%PDF-signed")
        return out

    def texts(self):
        return [call.kwargs["text"] for call in self.app.bot.send_message.await_args_list]

    def message(self):
        return SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())))

    def update(self, message=None):
        return SimpleNamespace(effective_message=message, message=message,
                               effective_chat=SimpleNamespace(id=123), effective_user=SimpleNamespace(id=456))

    def add_case(self, start=date(2026, 10, 20), end=date(2026, 10, 22), **fields):
        case = Case.new(start, end, self.raw)
        for key, value in fields.items():
            setattr(case, key, value)
        self.store.add(case)
        return case


class OpenAndRangeTests(FlowTestCase):
    async def test_open_app_sends_snapshot_with_token(self):
        case = self.add_case(last_problem="Error al firmar")
        message = SimpleNamespace(reply_text=AsyncMock())
        await self.plugin.open_app(SimpleNamespace(message=message), None)
        url = message.reply_text.await_args.kwargs["reply_markup"].keyboard[0][0].web_app.url
        self.assertTrue(url.startswith("https://example.test/congreso.html#data="))
        payload = json.loads(unquote(url.split("#data=", 1)[1]))
        self.assertEqual((payload["token"], payload["minStart"]), (self.plugin.launch_token, "2026-10-06"))
        self.assertEqual([(item["id"], item["problem"]) for item in payload["cases"]], [(case.id, "Error al firmar")])
        self.assertIn("Esperando la autorización", payload["cases"][0]["status"])

    def test_range_rules(self):
        self.add_case(date(2026, 10, 20), date(2026, 10, 22))
        today = NOW.date()
        self.assertIsNone(self.plugin.check_range(date(2026, 10, 6), date(2026, 10, 7), today))
        self.assertIn("como pronto", self.plugin.check_range(date(2026, 10, 5), date(2026, 10, 7), today))
        self.assertIn("anterior", self.plugin.check_range(date(2026, 10, 8), date(2026, 10, 7), today))
        self.assertIn("cambio de año", self.plugin.check_range(date(2026, 12, 30), date(2027, 1, 2), today))
        self.assertIn("solapan", self.plugin.check_range(date(2026, 10, 22), date(2026, 10, 23), today))


class NewRequestTests(FlowTestCase):
    async def submit(self, start="2026-10-20", end="2026-10-22"):
        self.plugin.launch_token = "t" * 32
        message = self.message()
        await self.plugin.handle_new(self.update(message), None, {"token": "t" * 32, "start": start, "end": end})
        await asyncio.gather(*self.tasks)
        return message

    def status_text(self, message):
        return message.reply_text.return_value.edit_text.await_args.args[0]

    async def test_stale_token_is_rejected(self):
        self.plugin.launch_token = "a" * 32
        message = self.message()
        await self.plugin.handle_new(self.update(message), None,
                                     {"token": "b" * 32, "start": "2026-10-20", "end": "2026-10-22"})
        self.assertIn("ya no es válida", message.reply_text.await_args.args[0])
        self.assertEqual((self.session.congress_calls, self.plugin.launch_token), ([], "a" * 32))

    async def test_submitted_request_creates_case_and_schedules_the_prompt(self):
        message = await self.submit()
        [call] = self.session.congress_calls
        self.assertEqual((call["start_date"], call["end_date"], call["reason"]),
                         ("2026-10-20", "2026-10-22", "Asistencia a congreso"))
        [case] = self.store.open_cases()
        self.assertFalse(case.simulated)
        self.assertEqual([task.when for task in self.scheduler.pending(flow.PROMPT_KIND)],
                         [datetime(2026, 10, 17, 9, 0, tzinfo=MADRID_TZ)])
        self.assertIn("enviada", self.status_text(message))
        self.assertIsNone(self.plugin.launch_token)
        self.assertNotIn(ACTIVE_KEY, self.app.bot_data)

    async def test_read_only_creates_a_simulated_case(self):
        self.session.congress_result = ReadOnlyStop()
        message = await self.submit()
        [case] = self.store.open_cases()
        self.assertTrue(case.simulated)
        self.assertIn("solo lectura", self.status_text(message))

    async def test_rejected_request_creates_no_case(self):
        self.session.congress_result = CongressRequestError("Supervisor ambiguo")
        message = await self.submit()
        self.assertEqual(self.store.open_cases(), [])
        self.assertIn("Supervisor ambiguo", self.status_text(message))

    async def test_invalid_range_is_reported_before_contacting_usc(self):
        message = await self.submit(start="2026-10-02", end="2026-10-03")
        self.assertIn("como pronto", message.reply_text.await_args.args[0])
        self.assertEqual(self.session.congress_calls, [])


class CancelTests(FlowTestCase):
    async def ask_cancel(self, case):
        self.plugin.launch_token = "t" * 32
        message = SimpleNamespace(reply_text=AsyncMock())
        await self.plugin.handle_cancel(self.update(message), None, {"token": "t" * 32, "case": case.id})
        return message

    async def press(self, callback_data):
        query = SimpleNamespace(data=callback_data, answer=AsyncMock(), edit_message_text=AsyncMock())
        await self.plugin.handle_callback(SimpleNamespace(callback_query=query), None)
        return query

    async def test_confirmed_cancel_stops_following_the_case(self):
        case = self.add_case(absence="requested")
        self.scheduler.schedule(flow.PROMPT_KIND, NOW + timedelta(days=1), {"case": case.id})
        folder = self.store.directory(case)
        message = await self.ask_cancel(case)
        yes = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        query = await self.press(yes)
        self.assertIsNone(self.store.get(case.id))
        self.assertFalse(folder.exists())
        self.assertEqual(self.scheduler.pending(flow.PROMPT_KIND), [])
        text = query.edit_message_text.await_args.args[0]
        self.assertIn(usc.REQUESTS_LIST_URL, text)
        self.assertIn(flow.ABSENCES_URL, text)

    async def test_declining_keeps_the_case(self):
        case = self.add_case()
        message = await self.ask_cancel(case)
        no = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
        await self.press(no)
        self.assertIsNotNone(self.store.get(case.id))

    async def test_unknown_case_or_used_token(self):
        self.plugin.launch_token = "t" * 32
        message = SimpleNamespace(reply_text=AsyncMock())
        await self.plugin.handle_cancel(self.update(message), None, {"token": "t" * 32, "case": "missing"})
        self.assertIn("ya no existe", message.reply_text.await_args.args[0])
        query = await self.press("cdieta_cancel_yes:" + "f" * 32)
        query.answer.assert_awaited_once_with("Esta cancelación ya no está disponible.")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_flow -v`
Expected: ERROR — `ImportError: cannot import name 'flow'`

- [ ] **Step 3: Implement `plugins/congreso_dieta/flow.py` (launch, new request, cancel)**

```python
"""congreso_dieta workflow: Mini App actions, absence prompt and the daily document run."""
from __future__ import annotations

import asyncio
import shutil
from datetime import date
from typing import Optional
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from fichaxebot.commands.vacations import _build_vacations_url
from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.absence_request import (
    AbsenceRequestCancelled, AbsenceRequestError, AbsenceRequestUncertain,
)
from fichaxebot.scrap_functions.commit import ReadOnlyStop
from fichaxebot.scrap_functions.congress_request import CongressRequestCancelled, CongressRequestError
from fichaxebot.utils import get_madrid_now
from fichaxebot.webapp_controller.vacation_confirmation import ACTIVE_KEY, STOPPING_KEY, PendingVacation
from plugins.congreso_dieta import dates, pdf, spreadsheet, usc
from plugins.congreso_dieta.cases import Case, CaseStore
from plugins.congreso_dieta.config import PluginConfig, parse_config

logger = get_logger(__name__)

PROMPT_KIND = "congreso_dieta.absence_prompt"
DAILY_JOB = "congreso_dieta.daily"
CALLBACK_PATTERN = r"^cdieta_(?:absence_yes|absence_no|cancel_yes|cancel_no):[0-9a-f]{32}$"
ABSENCES_URL = "https://fichaxe.usc.gal/pas/solicitudesPropias"
FAILURES_BEFORE_NOTICE = 3

STAGE_LABELS = {
    "awaiting_auth": "Esperando la autorización firmada",
    "auth_received": "Autorización recibida",
    "generated": "Documento generado; pendiente de firma",
    "signed": "Firmado; pendiente de entrega",
}
ABSENCE_LABELS = {
    "scheduled": "ausencia pendiente de preguntar",
    "asking": "ausencia pendiente de tu respuesta",
    "requesting": "solicitando la ausencia",
    "requested": "ausencia solicitada",
    "uncertain": "ausencia sin confirmar (revisa USC)",
    "skipped": "ausencia no solicitada",
    "simulated": "ausencia simulada",
    "not_requested": "ausencia no solicitada",
}


class PendingCongress(PendingVacation):
    callback_prefix = "congreso"
    filename = "solicitud-congreso.pdf"


class PendingPluginAbsence(PendingVacation):
    callback_prefix = "absence"
    filename = "solicitud-ausencia.png"


def span(case: Case) -> str:
    return f"{case.start_date:%d/%m} al {case.end_date:%d/%m/%Y}"


def describe(case: Case) -> str:
    text = f"{STAGE_LABELS[case.stage]} · {ABSENCE_LABELS[case.absence]}"
    return f"{text} · simulado" if case.simulated else text


def document_name(case: Case) -> str:
    return f"dieta_{case.start_date:%Y%m%d}_{case.end_date:%Y%m%d}"


def find_absence_type(catalog: dict, name: str) -> Optional[dict]:
    wanted = " ".join(name.split()).casefold()
    return next((kind for kind in catalog.get("types", [])
                 if " ".join(kind["name"].split()).casefold() == wanted), None)


def build_absence_request(case: Case, kind: dict, days: list[date], absence) -> dict:
    hours = {"startTime": absence.start_time, "endTime": absence.end_time} if kind["requiresHours"] else {}
    return {"year": case.start_date.year, "absenceTypeId": kind["id"],
            "periods": [{"date": day.isoformat(), **hours} for day in days],
            "observations": "", "attachments": []}


class CongresoDieta:
    def __init__(self, application, config: PluginConfig, raw_config: dict, store: CaseStore, *,
                 clock=get_madrid_now, generate=spreadsheet.generate_pdf, join=pdf.join_pdfs,
                 sign=pdf.sign_pdf) -> None:
        self.app = application
        self.config = config
        self.raw_config = raw_config
        self.store = store
        self.clock = clock
        self._generate_pdf = generate
        self._join_pdfs = join
        self._sign_pdf = sign
        self.launch_token: Optional[str] = None
        self.cancel_requests: dict[str, str] = {}
        self.generation_lock = asyncio.Lock()

    # Helpers -----------------------------------------------------------------

    @property
    def scheduler(self):
        return self.app.scheduler

    @property
    def session(self):
        return self.app.web_session

    def case_config(self, case: Case) -> PluginConfig:
        return parse_config(case.config, today=self.clock().date(), check_files=False)

    def alive(self, case: Case) -> bool:
        return self.store.get(case.id) is case

    async def notify(self, text: str, **kwargs) -> None:
        await self.app.bot.send_message(chat_id=get_config().telegram_chat_id, text=text, **kwargs)

    def take_token(self, data: dict) -> bool:
        if self.launch_token is None or data.get("token") != self.launch_token:
            return False
        self.launch_token = None
        return True

    def cancel_prompts(self, case: Case) -> None:
        self.scheduler.cancel(PROMPT_KIND, lambda task: task.payload.get("case") == case.id)

    def schedule_prompt(self, case: Case, now) -> None:
        config = self.case_config(case)
        when = dates.first_prompt(case.start_date, config.days_before, config.prompt, now)
        self.scheduler.schedule(PROMPT_KIND, min(when, dates.prompt_deadline(case.start_date)), {"case": case.id})

    def schedule_reminder(self, case: Case) -> None:
        config = self.case_config(case)
        when = min(dates.next_reminder(self.clock(), config.prompt), dates.prompt_deadline(case.start_date))
        self.scheduler.schedule(PROMPT_KIND, when, {"case": case.id})

    # Mini App ----------------------------------------------------------------

    async def open_app(self, update, context) -> None:
        if not update.message:
            return
        today = self.clock().date()
        self.launch_token = uuid4().hex
        payload = {
            "token": self.launch_token,
            "today": today.isoformat(),
            "minStart": dates.earliest_start(today).isoformat(),
            "cases": [{"id": case.id, "start": case.start, "end": case.end, "status": describe(case),
                       "problem": case.last_problem} for case in self.store.open_cases()],
        }
        url = _build_vacations_url(self.config.webapp_url, payload)
        keyboard = ReplyKeyboardMarkup([[KeyboardButton("Congreso y dieta", web_app=WebAppInfo(url=url))]],
                                       resize_keyboard=True)
        await update.message.reply_text("Elige las fechas del congreso o gestiona los trámites en curso.",
                                        reply_markup=keyboard)

    def check_range(self, start: date, end: date, today: date) -> Optional[str]:
        earliest = dates.earliest_start(today)
        if start < earliest:
            return f"El congreso debe empezar como pronto el {earliest:%d/%m/%Y}."
        if end < start:
            return "La fecha de fin no puede ser anterior a la de inicio."
        if start.year != end.year:
            return "El congreso no puede cruzar el cambio de año."
        if self.store.overlaps(start, end):
            return "Las fechas se solapan con un trámite en curso."
        return None

    async def handle_new(self, update, context, data: dict) -> None:
        message = update.effective_message
        if not self.take_token(data):
            await message.reply_text("Esta selección ya no es válida. Abre /congreso_dieta de nuevo.")
            return
        try:
            start, end = date.fromisoformat(data.get("start")), date.fromisoformat(data.get("end"))
        except (TypeError, ValueError):
            await message.reply_text("Las fechas no son válidas. Abre /congreso_dieta de nuevo.")
            return
        problem = self.check_range(start, end, self.clock().date())
        if problem:
            await message.reply_text(problem)
            return
        if self.app.bot_data.get(ACTIVE_KEY) or self.app.bot_data.get(STOPPING_KEY):
            await message.reply_text("Hay otra solicitud en curso. Espera a que termine.")
            return
        pending = PendingCongress(self.app, chat_id=update.effective_chat.id, user_id=update.effective_user.id,
                                  timeout=get_config().vacation_confirmation_timeout_seconds)
        self.app.bot_data[ACTIVE_KEY] = pending
        pending.task = self.app.create_task(self._submit_congress(message, pending, start, end), update=update)

    async def _submit_congress(self, message, pending, start: date, end: date) -> None:
        status = None
        try:
            status = await message.reply_text("🔄 Preparando la solicitud de congreso en USC…")
            request = {**self.config.congress, "start_date": start.isoformat(), "end_date": end.isoformat()}
            try:
                await pending.run(self.session.submit_congress_request, request)
                simulated = False
            except ReadOnlyStop:
                simulated = True
            case = Case.new(start, end, self.raw_config, simulated=simulated)
            self.store.add(case)
            self.schedule_prompt(case, self.clock())
            if simulated:
                text = ("🧪 Modo de solo lectura: la solicitud de congreso llegó al paso final, pero no se envió "
                        f"a USC. Trámite simulado creado para el {span(case)}.")
            else:
                # TODO(request id): store the id returned by the submission once the user provides that step.
                text = (f"✅ Solicitud de congreso enviada para el {span(case)}. USC aún no confirma el envío: "
                        f"revísala en {usc.REQUESTS_LIST_URL}")
            await status.edit_text(text)
        except CongressRequestCancelled as exc:
            if status:
                await status.edit_text(pending.reason if pending.decision.done() else str(exc))
        except CongressRequestError as exc:
            if status:
                await status.edit_text(f"❌ {exc}")
        except Exception:
            logger.exception("Could not submit the congress request")
            if status:
                await status.edit_text("❌ No se pudo completar la solicitud de congreso. Comprueba USC antes "
                                       f"de repetirla: {usc.REQUESTS_LIST_URL}")
        finally:
            pending.abort("Solicitud cancelada. No se envió a USC.")
            await pending.finish()

    async def handle_cancel(self, update, context, data: dict) -> None:
        message = update.effective_message
        if not self.take_token(data):
            await message.reply_text("Esta selección ya no es válida. Abre /congreso_dieta de nuevo.")
            return
        case = self.store.get(data.get("case")) if isinstance(data.get("case"), str) else None
        if case is None:
            await message.reply_text("Ese trámite ya no existe.")
            return
        token = uuid4().hex
        self.cancel_requests[token] = case.id
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("Sí, cancelar", callback_data=f"cdieta_cancel_yes:{token}")],
            [InlineKeyboardButton("No", callback_data=f"cdieta_cancel_no:{token}")],
        ])
        await message.reply_text(f"¿Cancelar el trámite del {span(case)}? Se dejará de seguir; lo ya enviado a USC "
                                  "no se anula.", reply_markup=buttons)

    # Callbacks -----------------------------------------------------------------

    async def handle_callback(self, update, context) -> None:
        query = update.callback_query
        action, token = query.data.split(":", 1)
        if action in ("cdieta_cancel_yes", "cdieta_cancel_no"):
            await self._answer_cancel(query, action, token)
            return
        await self._answer_absence(update, query, action, token)

    async def _answer_cancel(self, query, action: str, token: str) -> None:
        case_id = self.cancel_requests.pop(token, None)
        case = self.store.get(case_id) if case_id else None
        if case is None:
            await query.answer("Esta cancelación ya no está disponible.")
            return
        if action == "cdieta_cancel_no":
            await query.answer("Se mantiene el trámite.")
            await query.edit_message_text(f"El trámite del {span(case)} sigue en curso.")
            return
        self.cancel_prompts(case)
        self.store.remove(case.id)
        sent = []
        if not case.simulated:
            sent.append(f"la solicitud de congreso ({usc.REQUESTS_LIST_URL})")
        if case.absence in ("requested", "uncertain"):
            sent.append(f"la ausencia ({ABSENCES_URL})")
        text = f"🗑️ Trámite del {span(case)} cancelado."
        if sent:
            text += " Ya se había enviado a USC " + " y ".join(sent) + "; anúlalo allí si es necesario."
        await query.answer("Trámite cancelado.")
        await query.edit_message_text(text)

    async def _answer_absence(self, update, query, action: str, token: str) -> None:
        raise NotImplementedError  # replaced in Task 9

    async def run_absence_prompt(self, context, task) -> None:
        raise NotImplementedError  # replaced in Task 9
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_flow -v`
Expected: `OpenAndRangeTests`, `NewRequestTests`, `CancelTests` PASS. (The two `NotImplementedError` methods exist
so the test harness can register the prompt kind; they are not exercised until Task 9 replaces them.)

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/flow.py tests/test_congreso_flow.py
git commit -m "feat(congreso_dieta): Mini App launch, congress request and cancellation"
```

---

### Task 9: Workflow — absence prompt and request

**Files:**
- Modify: `plugins/congreso_dieta/flow.py` (replace the two `NotImplementedError` methods; add helpers)
- Test: `tests/test_congreso_flow.py` (append)

**Interfaces:**
- Consumes: Task 8 (`schedule_reminder`, `cancel_prompts`, `notify`, `alive`, `find_absence_type`, `build_absence_request`), `dates`, `UscWebSession.fetch_absence_selection_data/fetch_calendar_summary/submit_absence_request`
- Produces: `CongresoDieta.run_absence_prompt(context, task)` (task kind handler) and absence callbacks `cdieta_absence_yes|no:<prompt_token>`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_congreso_flow.py`:

```python
class AbsencePromptTests(FlowTestCase):
    async def run_prompt(self, case):
        task = self.scheduler.schedule(flow.PROMPT_KIND, self.clock.now + timedelta(minutes=1), {"case": case.id})
        job = next(job for job in self.app.job_queue.of("once") if job.data["id"] == task.id)
        await fire(self.app, job)

    async def answer(self, case, action):
        query = SimpleNamespace(data=f"cdieta_absence_{action}:{case.prompt_token}", answer=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=123),
                                 effective_user=SimpleNamespace(id=456))
        await self.plugin.handle_callback(update, None)
        await asyncio.gather(*self.tasks)
        return query

    def reminders_for(self, case):
        return [task for task in self.scheduler.pending(flow.PROMPT_KIND) if task.payload["case"] == case.id]

    async def test_prompt_asks_and_schedules_a_reminder(self):
        case = self.add_case()
        await self.run_prompt(case)
        kwargs = self.app.bot.send_message.await_args.kwargs
        self.assertIn("¿Solicito la ausencia", kwargs["text"])
        stored = self.store.get(case.id)
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
                         f"cdieta_absence_yes:{stored.prompt_token}")
        self.assertEqual(stored.absence, "asking")
        self.assertEqual([task.when for task in self.reminders_for(case)], [NOW + timedelta(minutes=30)])
        await self.run_prompt(case)
        self.assertIn("Recordatorio", self.texts()[-1])

    async def test_outside_the_window_the_prompt_waits(self):
        self.clock.now = NOW.replace(hour=21)
        case = self.add_case()
        await self.run_prompt(case)
        self.app.bot.send_message.assert_not_awaited()
        self.assertEqual([task.when for task in self.reminders_for(case)],
                         [datetime(2026, 10, 2, 8, 0, tzinfo=MADRID_TZ)])

    async def test_after_the_start_the_absence_is_not_requested(self):
        self.clock.now = datetime(2026, 10, 20, 0, 0, tzinfo=MADRID_TZ)
        case = self.add_case()
        await self.run_prompt(case)
        self.assertEqual(self.store.get(case.id).absence, "not_requested")
        self.assertIn("No se solicitó la ausencia", self.texts()[-1])

    async def test_not_now_skips_the_absence_and_stops_reminders(self):
        case = self.add_case(absence="asking", prompt_token=uuid4().hex)
        self.scheduler.schedule(flow.PROMPT_KIND, NOW + timedelta(minutes=30), {"case": case.id})
        await self.answer(case, "no")
        self.assertEqual(self.store.get(case.id).absence, "skipped")
        self.assertEqual(self.reminders_for(case), [])

    async def test_yes_requests_the_working_days_with_hours(self):
        case = self.add_case(date(2026, 10, 9), date(2026, 10, 14), absence="asking", prompt_token=uuid4().hex)
        await self.answer(case, "yes")
        hours = {"startTime": "08:00", "endTime": "15:00"}
        self.assertEqual(self.session.absence_calls, [{
            "year": 2026, "absenceTypeId": "7", "observations": "", "attachments": [],
            "periods": [{"date": "2026-10-09", **hours}, {"date": "2026-10-14", **hours}]}])
        self.assertEqual(self.store.get(case.id).absence, "requested")
        self.assertIn("Ausencia solicitada", self.texts()[-1])

    async def test_read_only_rejection_and_unclear_outcomes(self):
        for result, state, text in ((ReadOnlyStop(), "simulated", "solo lectura"),
                                    (AbsenceRequestError("Día cerrado"), "asking", "Día cerrado"),
                                    (AbsenceRequestUncertain("USC no confirmó"), "uncertain", "USC no confirmó")):
            with self.subTest(result=type(result).__name__):
                case = self.add_case(absence="asking", prompt_token=uuid4().hex)
                self.session.absence_result = result
                await self.answer(case, "yes")
                self.assertEqual(self.store.get(case.id).absence, state)
                self.assertIn(text, self.texts()[-1])
                self.assertEqual(bool(self.reminders_for(case)), state == "asking")

    async def test_unknown_absence_type_keeps_asking(self):
        self.session.catalog = {"years": [2026], "types": [{"id": "1", "name": "Outro", "requiresHours": False}]}
        case = self.add_case(absence="asking", prompt_token=uuid4().hex)
        await self.answer(case, "yes")
        self.assertEqual(self.store.get(case.id).absence, "asking")
        self.assertIn("Asistencia a congresos", self.texts()[-1])
        self.assertEqual(self.session.absence_calls, [])

    async def test_stale_prompt_button(self):
        case = self.add_case(absence="requested", prompt_token=uuid4().hex)
        query = await self.answer(case, "yes")
        query.answer.assert_awaited_once_with("Esta pregunta ya no está disponible.")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_flow.AbsencePromptTests -v`
Expected: ERROR — `NotImplementedError`.

- [ ] **Step 3: Replace the stubs in `flow.py`**

Replace the `run_absence_prompt` stub with:

```python
    async def run_absence_prompt(self, context, task) -> None:
        case = self.store.get(task.payload.get("case"))
        if case is None or case.absence not in ("scheduled", "asking"):
            return
        config = self.case_config(case)
        now = self.clock()
        deadline = dates.prompt_deadline(case.start_date)
        if now >= deadline:
            case.absence = "not_requested"
            self.store.save()
            await self.notify(f"ℹ️ No se solicitó la ausencia del {span(case)}: el congreso ya ha empezado.")
            return
        slot = dates.next_slot(now, config.prompt)
        if slot > now:
            self.scheduler.schedule(PROMPT_KIND, min(slot, deadline), {"case": case.id})
            return
        first = case.absence == "scheduled"
        case.prompt_token = case.prompt_token or uuid4().hex
        case.absence = "asking"
        self.store.save()
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("Solicitar ausencia", callback_data=f"cdieta_absence_yes:{case.prompt_token}"),
            InlineKeyboardButton("Ahora no", callback_data=f"cdieta_absence_no:{case.prompt_token}"),
        ]])
        question = "📋 ¿Solicito la ausencia" if first else "⏰ Recordatorio: ¿solicito la ausencia"
        await self.notify(f"{question} del {span(case)} para el congreso?", reply_markup=buttons)
        self.schedule_reminder(case)
```

Replace the `_answer_absence` stub with:

```python
    async def _answer_absence(self, update, query, action: str, token: str) -> None:
        case = self.store.by_prompt_token(token)
        if case is None or case.absence != "asking":
            await query.answer("Esta pregunta ya no está disponible.")
            return
        self.cancel_prompts(case)
        if action == "cdieta_absence_no":
            case.absence = "skipped"
            self.store.save()
            await query.answer("No se solicitará la ausencia.")
            return
        case.absence = "requesting"
        self.store.save()
        await query.answer("Solicitando la ausencia…")
        self.app.create_task(
            self._request_absence(case, update.effective_chat.id, update.effective_user.id), update=update)

    async def _ask_again(self, case: Case, text: str) -> None:
        if self.alive(case):
            case.absence = "asking"
            self.store.save()
            self.schedule_reminder(case)
        await self.notify(text)

    async def _request_absence(self, case: Case, chat_id: int, user_id: int) -> None:
        config = self.case_config(case)
        try:
            catalog = await asyncio.to_thread(self.session.fetch_absence_selection_data)
            kind = find_absence_type(catalog, config.absence.type_name)
            if kind is None:
                raise AbsenceRequestError(f"USC no ofrece el tipo de ausencia «{config.absence.type_name}».")
            entries = await asyncio.to_thread(self.session.fetch_calendar_summary)
        except Exception as exc:  # noqa: BLE001 - nothing was sent yet; asking again is safe
            logger.exception("Could not prepare the congress absence request")
            await self._ask_again(case, f"❌ No se pudo preparar la ausencia: {exc} Te lo volveré a preguntar.")
            return
        if not self.alive(case):
            return
        days = dates.absence_days(case.start_date, case.end_date, dates.parse_non_working(entries))
        if not days:
            case.absence = "not_requested"
            self.store.save()
            await self.notify(f"ℹ️ Del {span(case)} no hay días laborables: no hace falta solicitar ausencia.")
            return
        request = build_absence_request(case, kind, days, config.absence)
        global_config = get_config()
        try:
            if global_config.absence_confirmation_enabled:
                await self._submit_absence_confirmed(request, chat_id, user_id,
                                                     global_config.absence_confirmation_timeout_seconds)
            else:
                await asyncio.to_thread(self.session.submit_absence_request, request)
        except ReadOnlyStop:
            outcome, text = "simulated", "🧪 Modo de solo lectura: la ausencia llegó al paso final, pero no se envió a USC."
        except AbsenceRequestCancelled as exc:
            await self._ask_again(case, f"{exc} Te lo volveré a preguntar.")
            return
        except AbsenceRequestError as exc:
            await self._ask_again(case, f"❌ {exc} Te lo volveré a preguntar.")
            return
        except AbsenceRequestUncertain as exc:
            outcome, text = "uncertain", f"⚠️ {exc} {ABSENCES_URL}"
        except Exception:
            logger.exception("Could not submit the congress absence request")
            outcome, text = "uncertain", f"⚠️ No se pudo confirmar la ausencia. Comprueba USC antes de repetirla: {ABSENCES_URL}"
        else:
            outcome, text = "requested", f"✅ Ausencia solicitada del {span(case)}."
        if self.alive(case):
            case.absence = outcome
            self.store.save()
        await self.notify(text)

    async def _submit_absence_confirmed(self, request: dict, chat_id: int, user_id: int, timeout: int) -> None:
        if self.app.bot_data.get(ACTIVE_KEY) or self.app.bot_data.get(STOPPING_KEY):
            raise AbsenceRequestCancelled("Hay otra solicitud en curso.")
        pending = PendingPluginAbsence(self.app, chat_id=chat_id, user_id=user_id, timeout=timeout)
        self.app.bot_data[ACTIVE_KEY] = pending
        pending.task = asyncio.current_task()
        try:
            await pending.run(self.session.submit_absence_request, request)
        finally:
            pending.abort("Solicitud cancelada. No se envió a USC.")
            await pending.finish()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_flow -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/flow.py tests/test_congreso_flow.py
git commit -m "feat(congreso_dieta): absence prompt with reminders and absence request"
```

---

### Task 10: Workflow — daily authorization check and document run

**Files:**
- Modify: `plugins/congreso_dieta/flow.py` (add methods)
- Test: `tests/test_congreso_flow.py` (append)

**Interfaces:**
- Consumes: `usc.fetch_request_status/download_authorization` (called via `session.run`), `dates.generation_day`, injected `generate/join/sign`, `SheetDates`
- Produces: `CongresoDieta.run_daily(context)` (recurring job handler)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_congreso_flow.py`:

```python
class DailyTests(FlowTestCase):
    def status(self, state="Tramitada", url=AUTH_URL):
        return usc.RequestStatus(state, url)

    async def daily(self):
        await self.plugin.run_daily(SimpleNamespace(bot=self.app.bot, application=self.app))

    async def test_cases_without_request_id_or_simulated_wait(self):
        self.add_case()
        self.add_case(date(2026, 11, 2), date(2026, 11, 3), simulated=True, request_id="1")
        with patch.object(usc, "fetch_request_status") as fetch:
            await self.daily()
        fetch.assert_not_called()

    async def test_authorization_before_generation_day_is_stored_and_waits(self):
        case = self.add_case(request_id="100001")
        with patch.object(usc, "fetch_request_status", return_value=self.status()), \
             patch.object(usc, "download_authorization", return_value=b"%PDF-auth"):
            await self.daily()
        stored = self.store.get(case.id)
        self.assertEqual((stored.stage, stored.auth_date), ("auth_received", "2026-10-01"))
        self.assertEqual((self.store.directory(stored) / "autorizacion.pdf").read_bytes(), b"%PDF-auth")
        self.assertEqual(self.generated, [])
        self.assertIn("Autorización firmada recibida", self.texts()[-1])

    async def test_full_document_run_on_generation_day(self):
        case = self.add_case(date(2026, 10, 6), date(2026, 10, 7), request_id="100001")
        self.clock.now = datetime(2026, 10, 8, 10, 0, tzinfo=MADRID_TZ)
        with patch.object(usc, "fetch_request_status", return_value=self.status()), \
             patch.object(usc, "download_authorization", return_value=b"%PDF-auth"):
            await self.daily()
        self.assertEqual(self.generated, [SheetDates(date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8))])
        target = self.config.output_dir / "dieta_20261006_20261007.pdf"
        self.assertEqual(target.read_bytes(), b"%PDF-signed")
        self.app.bot.send_document.assert_awaited_once()
        self.assertIsNone(self.store.get(case.id))

    async def test_check_failures_are_reported_on_the_third_day(self):
        case = self.add_case(request_id="100001")
        with patch.object(usc, "fetch_request_status", side_effect=RuntimeError("timeout")):
            for _ in range(4):
                await self.daily()
        self.assertEqual(sum("No se pudo consultar" in text for text in self.texts()), 1)
        self.assertEqual(self.store.get(case.id).check_failures, 4)

    async def test_rejected_request_is_reported_once(self):
        case = self.add_case(request_id="100001")
        with patch.object(usc, "fetch_request_status", return_value=self.status("Denegada", None)):
            await self.daily()
            await self.daily()
        self.assertEqual(sum("Denegada" in text for text in self.texts()), 1)
        self.assertEqual(self.store.get(case.id).stage, "awaiting_auth")

    async def test_signing_failure_keeps_an_unsigned_copy_and_retries(self):
        case = self.add_case(date(2026, 10, 6), date(2026, 10, 7), stage="auth_received", auth_date="2026-10-02")
        (self.store.directory(case) / "autorizacion.pdf").write_bytes(b"%PDF-auth")
        self.clock.now = datetime(2026, 10, 8, 10, 0, tzinfo=MADRID_TZ)

        def failing(src, out, signing):
            raise pdf.PdfError("Certificado caducado")

        self.plugin._sign_pdf = failing
        await self.daily()
        unsigned = self.config.output_dir / "dieta_20261006_20261007_SIN_FIRMAR.pdf"
        stored = self.store.get(case.id)
        self.assertEqual(stored.stage, "generated")
        self.assertIn("Certificado caducado", stored.last_problem)
        self.assertTrue(unsigned.exists())
        self.plugin._sign_pdf = self.fake_sign
        await self.daily()
        self.assertIsNone(self.store.get(case.id))
        self.assertFalse(unsigned.exists())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_flow.DailyTests -v`
Expected: ERROR — `AttributeError: 'CongresoDieta' object has no attribute 'run_daily'`

- [ ] **Step 3: Add the daily run to `CongresoDieta` in `flow.py`**

```python
    # Daily run -----------------------------------------------------------------

    async def run_daily(self, context) -> None:
        today = self.clock().date()
        for case in list(self.store.open_cases()):
            try:
                await self._advance(case, today)
            except Exception:  # noqa: BLE001 - one broken case must not block the others
                logger.exception("Unexpected error advancing congress case %s", case.id)
                await self._problem(case, "Error inesperado; se reintentará mañana.")

    async def _problem(self, case: Case, text: str) -> None:
        if self.alive(case):
            case.last_problem = text
            self.store.save()
        await self.notify(f"⚠️ {text}")

    async def _advance(self, case: Case, today: date) -> None:
        if case.stage == "awaiting_auth":
            if case.simulated or not case.request_id:
                return  # TODO(request id): see "Known gaps" in the spec
            await self._check_authorization(case, today)
        if case.stage == "auth_received" and today >= dates.generation_day(
                case.end_date, date.fromisoformat(case.auth_date)):
            await self._generate(case, today)
        if case.stage == "generated":
            await self._sign(case)
        if case.stage == "signed":
            await self._deliver(case)

    async def _check_authorization(self, case: Case, today: date) -> None:
        try:
            status = await asyncio.to_thread(self.session.run, usc.fetch_request_status, case.request_id)
            document = None
            if status.signed:
                document = await asyncio.to_thread(self.session.run, usc.download_authorization,
                                                   status.authorization_url)
        except Exception:  # noqa: BLE001 - read-only check; retried tomorrow
            logger.exception("Could not check the congress authorization for case %s", case.id)
            case.check_failures += 1
            case.last_problem = "No se pudo consultar la autorización en USC."
            self.store.save()
            if case.check_failures == FAILURES_BEFORE_NOTICE:
                await self.notify(f"⚠️ No se pudo consultar la autorización del congreso del {span(case)} durante "
                                  f"{FAILURES_BEFORE_NOTICE} días seguidos. Seguiré intentándolo.")
            return
        case.check_failures = 0
        if status.rejected:
            first_notice = case.notified_state != status.state
            case.notified_state = status.state
            case.last_problem = f"USC: {status.state}"
            self.store.save()
            if first_notice:
                await self.notify(f"⚠️ La solicitud de congreso del {span(case)} está en estado «{status.state}». "
                                  "Cancela el trámite con /congreso_dieta si ya no sigue adelante.")
            return
        if document is None:
            self.store.save()
            return
        (self.store.directory(case) / "autorizacion.pdf").write_bytes(document)
        case.stage, case.auth_date, case.last_problem = "auth_received", today.isoformat(), None
        self.store.save()
        await self.notify(f"📄 Autorización firmada recibida para el congreso del {span(case)}.")

    async def _generate(self, case: Case, today: date) -> None:
        config = self.case_config(case)
        folder = self.store.directory(case)
        workdir = folder / "hoja"
        shutil.rmtree(workdir, ignore_errors=True)
        sheet_dates = spreadsheet.SheetDates(case.start_date, case.end_date, today)
        try:
            async with self.generation_lock:
                sheet = await asyncio.to_thread(self._generate_pdf, config.spreadsheet_template, workdir, sheet_dates)
            await asyncio.to_thread(self._join_pdfs, sheet, folder / "autorizacion.pdf", folder / "unido.pdf")
        except (spreadsheet.SpreadsheetError, pdf.PdfError, OSError) as exc:
            await self._problem(case, f"Error al generar el documento: {exc} Se reintentará mañana.")
            return
        case.stage, case.last_problem = "generated", None
        self.store.save()

    async def _sign(self, case: Case) -> None:
        config = self.case_config(case)
        folder = self.store.directory(case)
        try:
            await asyncio.to_thread(self._sign_pdf, folder / "unido.pdf", folder / "firmado.pdf", config.signing)
        except pdf.PdfError as exc:
            if not case.unsigned_saved:
                try:
                    shutil.copyfile(folder / "unido.pdf", config.output_dir / f"{document_name(case)}_SIN_FIRMAR.pdf")
                    case.unsigned_saved = True
                except OSError:
                    logger.exception("Could not save the unsigned document")
            await self._problem(case, f"Error al firmar: {exc} El documento sin firmar está en "
                                      f"{config.output_dir}. Se reintentará mañana.")
            return
        case.stage, case.last_problem = "signed", None
        self.store.save()

    async def _deliver(self, case: Case) -> None:
        config = self.case_config(case)
        signed = self.store.directory(case) / "firmado.pdf"
        target = config.output_dir / f"{document_name(case)}.pdf"
        try:
            shutil.copyfile(signed, target)
            with signed.open("rb") as handle:
                await self.app.bot.send_document(
                    chat_id=get_config().telegram_chat_id, document=handle, filename=target.name,
                    caption=f"✅ Documento de dieta firmado del congreso del {span(case)}. Guardado en {target}.")
        except Exception as exc:  # noqa: BLE001 - delivery is retried tomorrow
            logger.exception("Could not deliver congress case %s", case.id)
            await self._problem(case, f"No se pudo entregar el documento firmado: {exc} Se reintentará mañana.")
            return
        (config.output_dir / f"{document_name(case)}_SIN_FIRMAR.pdf").unlink(missing_ok=True)
        self.store.remove(case.id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_congreso_flow -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/flow.py tests/test_congreso_flow.py
git commit -m "feat(congreso_dieta): daily authorization check, generation, signing and delivery"
```

---

### Task 11: Plugin wiring

**Files:**
- Modify: `plugins/congreso_dieta/__init__.py`
- Test: `tests/test_congreso_plugin.py`

**Interfaces:**
- Consumes: Tasks 1–10; `register_plugins` (setup hook, `WEBAPP_CONTROLLERS`), `TaskScheduler.register_kind/register_daily`
- Produces: `COMMANDS = {"congreso_dieta": …}`, `WEBAPP_CONTROLLERS = {"congreso_dieta_new": …, "congreso_dieta_cancel": …}`, `setup(application)`, `PLUGIN_KEY`

- [ ] **Step 1: Write the failing tests**

`tests/test_congreso_plugin.py`:

```python
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from telegram.ext import ApplicationBuilder, CommandHandler

import plugins.congreso_dieta as plugin_module
from fichaxebot.plugins import register_plugins
from fichaxebot.scheduler import TaskScheduler
from fichaxebot.utils import MADRID_TZ
from fichaxebot.webapp_controller import router
from plugins.congreso_dieta import flow
from plugins.congreso_dieta.cases import CaseStore
from tests.scheduler_fakes import Clock, fake_app
from tests.test_congreso_config import raw_config


class PluginWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.app = ApplicationBuilder().token("123456:TEST_TOKEN").build()
        self.app.scheduler = TaskScheduler("123", path=self.root / "schedule.json",
                                           clock=Clock(datetime(2026, 10, 1, 8, 0, tzinfo=MADRID_TZ)))
        store = patch.object(plugin_module, "CaseStore",
                             lambda: CaseStore(self.root / "cases.json", self.root / "files"))
        store.start()
        self.addCleanup(store.stop)

    def configure(self, section):
        patcher = patch.object(plugin_module, "get_config",
                               return_value=SimpleNamespace(plugin_config={"congreso_dieta": section}))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_setup_registers_command_webapp_types_task_kind_and_daily_job(self):
        self.configure(raw_config(str(self.root)))
        with patch.dict(router.WEBAPP_CONTROLLERS):
            register_plugins(self.app, ["congreso_dieta"])
            self.assertTrue({"congreso_dieta_new", "congreso_dieta_cancel"} <= set(router.WEBAPP_CONTROLLERS))
        commands = {command for handlers in self.app.handlers.values() for handler in handlers
                    if isinstance(handler, CommandHandler) for command in handler.commands}
        self.assertIn("congreso_dieta", commands)
        self.assertIsInstance(self.app.bot_data[plugin_module.PLUGIN_KEY], flow.CongresoDieta)
        started = fake_app()
        self.app.scheduler.start(started)
        self.assertEqual([job.name for job in started.job_queue.of("daily")], [flow.DAILY_JOB])

    async def test_invalid_config_stops_startup(self):
        self.configure({})
        with patch.dict(router.WEBAPP_CONTROLLERS), self.assertRaisesRegex(ValueError, "congreso_dieta"):
            register_plugins(self.app, ["congreso_dieta"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_congreso_plugin -v`
Expected: ERROR/FAIL — `Plugin 'congreso_dieta': COMMANDS must map command names to async callbacks`.

- [ ] **Step 3: Implement `plugins/congreso_dieta/__init__.py`**

```python
"""congreso_dieta plugin: congress authorization, absence and signed per-diem document."""
from telegram.ext import CallbackQueryHandler

from fichaxebot.config import get_config
from fichaxebot.scheduler import Interrupted, Misfire
from fichaxebot.utils import get_madrid_now
from plugins.congreso_dieta.cases import CaseStore
from plugins.congreso_dieta.config import NAME, parse_config
from plugins.congreso_dieta.flow import CALLBACK_PATTERN, DAILY_JOB, PROMPT_KIND, CongresoDieta

PLUGIN_KEY = "congreso_dieta"


def _plugin(context) -> CongresoDieta:
    return context.application.bot_data[PLUGIN_KEY]


async def congreso_dieta(update, context):
    await _plugin(context).open_app(update, context)


async def _new_request(update, context, data):
    await _plugin(context).handle_new(update, context, data)


async def _cancel_request(update, context, data):
    await _plugin(context).handle_cancel(update, context, data)


COMMANDS = {"congreso_dieta": congreso_dieta}
WEBAPP_CONTROLLERS = {"congreso_dieta_new": _new_request, "congreso_dieta_cancel": _cancel_request}


def setup(application) -> None:
    raw = get_config().plugin_config.get(NAME)
    config = parse_config(raw, today=get_madrid_now().date())
    store = CaseStore()
    store.load()
    plugin = CongresoDieta(application, config, raw, store)
    application.bot_data[PLUGIN_KEY] = plugin
    # Sending a question twice is harmless, so interrupted prompts are retried.
    application.scheduler.register_kind(PROMPT_KIND, plugin.run_absence_prompt, misfire=Misfire.RUN_LATE,
                                        interrupted=Interrupted.RETRY, notify_errors=True)
    application.scheduler.register_daily(DAILY_JOB, plugin.run_daily, at=config.auth_check_time,
                                         catch_up=True, notify_errors=True)
    application.add_handler(CallbackQueryHandler(plugin.handle_callback, pattern=CALLBACK_PATTERN))
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_congreso_plugin -v && .venv/bin/python -m unittest discover -s tests -p "test_*.py"`
Expected: all PASS.

- [ ] **Step 5: Commit** (only with user approval)

```bash
git add plugins/congreso_dieta/__init__.py tests/test_congreso_plugin.py
git commit -m "feat(congreso_dieta): register command, web app handlers and scheduled work"
```

---

### Task 12: Mini App page

**Files:**
- Create: `docs/congreso.html`, `docs/congreso.js`
- Test: `tests/browser_congreso_flow.py` (runs only with `CHROMEDRIVER`)

**Interfaces:**
- Consumes: payload `{token, today, minStart, cases: [{id, start, end, status, problem}]}` in `#data=` (Task 8 `open_app`)
- Produces: `sendData` payloads `{"type": "congreso_dieta_new", "token", "start", "end"}` and `{"type": "congreso_dieta_cancel", "token", "case"}`

- [ ] **Step 1: Write the browser test**

`tests/browser_congreso_flow.py`:

```python
"""Offline browser checks of the congress Mini App; set CHROMEDRIVER to a local driver."""
import json
import os
import re
import unittest
from pathlib import Path
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("CHROMEDRIVER"), "Set CHROMEDRIVER for local browser tests")
class CongresoBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = webdriver.ChromeOptions()
        for flag in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage"):
            options.add_argument(flag)
        cls.browser = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=options)
        cls.addClassCleanup(cls.browser.quit)
        cls.browser.execute_cdp_cmd("Network.enable", {})
        cls.browser.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["http://*", "https://*"]})

    def open_app(self, cases=()):
        html = re.sub(r"<script\b[^>]*src=[^>]+>\s*</script>", "", (ROOT / "docs/congreso.html").read_text())
        data = {"token": "launch", "today": "2026-10-01", "minStart": "2026-10-06", "cases": list(cases)}
        self.browser.get("about:blank")
        self.browser.get("data:text/html;charset=utf-8," + quote(html) + "#data=" + quote(json.dumps(data)))
        self.browser.execute_script("""
            window.sent = [];
            window.Telegram = {WebApp: {ready(){}, expand(){}, sendData(value){window.sent.push(JSON.parse(value));}}};
            window.FullCalendar = {Calendar: class {
                constructor(el, options){this.el=el;this.options=options;window.testCalendar=this;}
                render(){}
            }};
        """)
        self.browser.execute_script((ROOT / "docs/congreso.js").read_text())

    def click_day(self, day):
        self.browser.execute_script(f"window.testCalendar.options.dateClick({{dateStr:'{day}'}});")

    def sent(self):
        return self.browser.execute_script("return window.sent;")

    def test_range_selection_sends_a_new_request(self):
        self.open_app()
        self.click_day("2026-10-20")
        self.click_day("2026-10-22")
        send = self.browser.find_element(By.ID, "send")
        self.assertTrue(send.is_enabled())
        send.click()
        self.assertEqual(self.sent(), [{"type": "congreso_dieta_new", "token": "launch",
                                        "start": "2026-10-20", "end": "2026-10-22"}])

    def test_blocked_days_and_overlaps_disable_sending(self):
        self.open_app([{"id": "c1", "start": "2026-10-20", "end": "2026-10-22", "status": "Esperando",
                        "problem": None}])
        self.click_day("2026-10-02")   # before minStart: ignored
        self.click_day("2026-10-21")   # inside an open case: ignored
        self.click_day("2026-10-19")
        self.click_day("2026-10-23")   # spans the open case
        self.assertFalse(self.browser.find_element(By.ID, "send").is_enabled())
        self.assertIn("solapan", self.browser.find_element(By.ID, "status").text)

    def test_open_cases_show_problems_and_can_be_cancelled(self):
        self.open_app([{"id": "c1", "start": "2026-10-20", "end": "2026-10-22", "status": "Esperando",
                        "problem": "Error al firmar"}])
        card = self.browser.find_element(By.CSS_SELECTOR, ".case")
        self.assertIn("Error al firmar", card.text)
        card.find_element(By.TAG_NAME, "button").click()
        self.assertEqual(self.sent(), [{"type": "congreso_dieta_cancel", "token": "launch", "case": "c1"}])
```

- [ ] **Step 2: Run it to verify it fails** (requires a local chromedriver)

Run: `CHROMEDRIVER=/path/to/chromedriver .venv/bin/python -m unittest tests.browser_congreso_flow -v`
Expected: ERROR — `docs/congreso.html` does not exist. Without `CHROMEDRIVER` the class is skipped.

- [ ] **Step 3: Create `docs/congreso.html`**

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Congreso y dieta</title>
  <script src="https://unpkg.com/fullcalendar@6.1.8/index.global.min.js"></script>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; padding: 12px 16px; background: var(--tg-theme-bg-color, #fff); color: var(--tg-theme-text-color, #111); }
    h2 { font-size: 1.05rem; margin: 16px 0 8px; }
    .case { border: 1px solid #d0d7de; border-radius: 8px; padding: 10px; margin-bottom: 8px; }
    .case p { margin: 4px 0; }
    .problem { color: #b42318; }
    button { width: 100%; padding: 10px; border: 0; border-radius: 8px; background: var(--tg-theme-button-color, #2481cc); color: var(--tg-theme-button-text-color, #fff); font-size: 1rem; }
    button.secondary { background: #eaeef2; color: #b42318; margin-top: 6px; }
    button:disabled { opacity: 0.5; }
    .dia-seleccionado { background: rgba(36, 129, 204, 0.25); }
    .dia-bloqueado { background: rgba(0, 0, 0, 0.06); }
    #status { color: #b42318; min-height: 1.2em; }
  </style>
</head>
<body>
  <section id="cases-section" hidden>
    <h2>Trámites en curso</h2>
    <div id="cases"></div>
  </section>
  <section>
    <h2>Nueva solicitud</h2>
    <p>Pulsa el primer y el último día del congreso.</p>
    <div id="calendar"></div>
    <p id="selection">Sin fechas seleccionadas</p>
    <p id="status" role="status"></p>
    <button id="send" disabled>Solicitar</button>
  </section>
<script src="congreso.js?v=1"></script>
</body>
</html>
```

- [ ] **Step 4: Create `docs/congreso.js`**

```javascript
/* Congress cases: cancel an open one or pick a continuous date range for a new one. */
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const el = id => document.getElementById(id);
const label = value => new Date(`${value}T12:00:00`).toLocaleDateString('es', {day: 'numeric', month: 'long', year: 'numeric'});
let data, start = null, end = null, sent = false, calendar;

function send(payload) {
  if (sent) return;
  sent = true;
  el('send').disabled = true;
  tg?.sendData(JSON.stringify({...payload, token: data.token}));
}
const insideCase = day => data.cases.some(c => c.start <= day && day <= c.end);
const blocked = day => day < data.minStart || insideCase(day);
function problem() {
  if (!start) return 'Pulsa el primer día del congreso.';
  if (!end) return 'Pulsa el último día (o el mismo día si dura uno).';
  if (start.slice(0, 4) !== end.slice(0, 4)) return 'El congreso no puede cruzar el cambio de año.';
  if (data.cases.some(c => c.start <= end && start <= c.end)) return 'Las fechas se solapan con un trámite en curso.';
  return '';
}
function refresh() {
  el('selection').textContent = start ? (end ? `Del ${label(start)} al ${label(end)}` : `Desde el ${label(start)}`) : 'Sin fechas seleccionadas';
  const message = problem();
  el('status').textContent = start && end ? message : '';
  el('send').disabled = sent || Boolean(message);
  calendar?.el.querySelectorAll('.fc-daygrid-day[data-date]').forEach(cell => {
    const day = cell.dataset.date;
    cell.classList.toggle('dia-seleccionado', Boolean(start) && day >= start && day <= (end || start));
    cell.classList.toggle('dia-bloqueado', blocked(day));
  });
}
function pick(day) {
  if (sent || blocked(day)) return;
  if (!start || end || day < start) { start = day; end = null; } else { end = day; }
  refresh();
}
function renderCases() {
  el('cases-section').hidden = !data.cases.length;
  el('cases').replaceChildren(...data.cases.map(item => {
    const card = document.createElement('div'); card.className = 'case';
    const title = document.createElement('strong'); title.textContent = `Del ${label(item.start)} al ${label(item.end)}`;
    const status = document.createElement('p'); status.textContent = item.status;
    card.append(title, status);
    if (item.problem) { const p = document.createElement('p'); p.className = 'problem'; p.textContent = item.problem; card.append(p); }
    const cancel = document.createElement('button'); cancel.className = 'secondary'; cancel.textContent = 'Cancelar';
    cancel.onclick = () => send({type: 'congreso_dieta_cancel', case: item.id});
    card.append(cancel);
    return card;
  }));
}
try {
  data = JSON.parse(decodeURIComponent(location.hash.replace(/^#data=/, '')));
} catch {
  el('status').textContent = 'No se pudieron leer los datos. Abre /congreso_dieta de nuevo.';
}
if (data) {
  renderCases();
  calendar = new FullCalendar.Calendar(el('calendar'), {
    initialView: 'dayGridMonth', locale: 'es', firstDay: 1, height: 'auto', initialDate: data.minStart,
    dateClick: info => pick(info.dateStr), datesSet: refresh,
  });
  calendar.render();
  el('send').onclick = () => { if (!problem()) send({type: 'congreso_dieta_new', start, end}); };
  refresh();
}
```

- [ ] **Step 5: Run the browser test**

Run: `CHROMEDRIVER=/path/to/chromedriver .venv/bin/python -m unittest tests.browser_congreso_flow -v`
Expected: all PASS. If no chromedriver is available, open the page through the ngrok tunnel (`./devtools/launch_server.sh --ngrok`) from `/congreso_dieta` and check the three behaviours by hand.

- [ ] **Step 6: Commit** (only with user approval)

```bash
git add docs/congreso.html docs/congreso.js tests/browser_congreso_flow.py
git commit -m "feat(congreso_dieta): Mini App to manage and start congress cases"
```

---

### Task 13: Documentation, spec sync and end-to-end check

**Files:**
- Create: `docs/congreso_dieta.md`
- Modify: `README.md`, `docs/superpowers/specs/2026-09-25-congreso-dieta-design.md`

- [ ] **Step 1: Write `docs/congreso_dieta.md`**

~~~markdown
# congreso_dieta plugin

`/congreso_dieta` opens a Mini App to request the USC congress authorization for a date range, manage cases in
progress and cancel them. N days before the start the bot asks whether to request the matching absence. When USC
marks the authorization `Tramitada`, the bot fills the per-diem workbook through its own `CreaPDF` macro, joins the
spreadsheet PDF with the authorization, signs the result with AutoFirma, sends it in Telegram and saves it.

## Requirements (host installation only)

- LibreOffice with `python3-uno`; the virtualenv must be created with `--system-site-packages` (`install.sh` does it).
- `pdfunite` (poppler-utils) and the AutoFirma command line (`autofirma`).
- A certificate in the Firefox store AutoFirma reads (`autofirma listaliases -store mozilla`) or a `.p12` file.

## Configuration

Add `"congreso_dieta"` to `plugins` and a `plugin_config` section:

```json
"plugins": ["congreso_dieta"],
"plugin_config": {
  "congreso_dieta": {
    "webapp_url": "https://nonari.github.io/fichajes/congreso.html",
    "output_dir": "/home/user/Documentos/dietas",
    "auth_check_time": "10:00",
    "prompt": {"at": "09:00", "reminder_minutes": 30, "window": ["08:00", "20:00"]},
    "signing": {"store": "mozilla", "alias": "NOMBRE APELLIDOS - 00000000T", "password": null},
    "days_before": 3,
    "spreadsheet_template": "/home/user/plantillas/GL_VISITAS_PLANTA_FINSA.xlsm",
    "absence": {"type": "Asistencia a congresos", "start_time": "08:00", "end_time": "15:00"},
    "congress": {
      "data_processing_authorized": true,
      "address": {"country": "España", "province": "Coruña, A", "municipality": "Santiago de Compostela",
                  "postal_code": "15782", "line1": "…"},
      "reason": "…", "organization": "…", "employment_category": "PREDOUTORAIS",
      "teaching_assigned": false, "supervisor_query": "…"
    }
  }
}
```

`congress` accepts the fields of [the congress API](congress_api.md) except the dates. The absence type is matched
by name against USC. Invalid settings stop the bot at startup with a message naming the setting.

## Known gap

The request id of a submitted congress request is not read yet, so cases wait at "Esperando la autorización
firmada" until that step is added. Everything else can be exercised in read-only mode.

## Optional local tests

- `CONGRESO_TEMPLATE=/path/workbook.xlsm` runs the real LibreOffice macro test.
- `CONGRESO_SIGN_ALIAS="<alias>"` (optionally `CONGRESO_SIGN_STORE`, `CONGRESO_SIGN_PASSWORD`) signs a dummy PDF.
- `CHROMEDRIVER=/path/chromedriver` runs the Mini App browser test.
~~~

- [ ] **Step 2: Link it from `README.md`**

After the congress API paragraph (`The [congress permission API](docs/congress_api.md) …`) add:

```markdown
The optional [`congreso_dieta` plugin](docs/congreso_dieta.md) automates congress authorization, absence and the signed per-diem document (host installation only).
```

- [ ] **Step 3: Sync the spec with implementation details**

In `docs/superpowers/specs/2026-09-25-congreso-dieta-design.md`:
- In the configuration example add `"webapp_url": "https://…/congreso.html",` as the first key, and note `days_before` must be ≥ 1.
- In *Integration facts → USC requests pages* add: states that will not become signed are recognised by the words deneg/anulad/desist/rexeit/rechaz/arquiv/revogad; other states are treated as in progress.
- In *Prerequisites* item 2 replace "Still to add with this plugin: …" with "Plugin support added: `plugin_config`, `WEBAPP_CONTROLLERS`, `UscWebSession.run`, `congreso` confirmation prefix."
- Change the status line to `Status: **implemented** (plan: docs/superpowers/plans/2026-09-25-congreso-dieta.md).`

- [ ] **Step 4: Full verification**

Run: `.venv/bin/python -m unittest discover -s tests -p "test_*.py"`
Expected: all PASS (environment-gated classes skipped).

- [ ] **Step 5: End-to-end check in read-only mode** (the user runs it with the real bot)

1. `config.json`: `"read_only": true`, `"plugins": ["congreso_dieta"]`, the `plugin_config` section with `webapp_url` pointing at the ngrok domain (`…ngrok-free.dev/congreso.html`).
2. `./devtools/launch_server.sh --ngrok`, then start the bot. It must start without configuration errors.
3. `/congreso_dieta`: the Mini App opens; pick a range ≥ 5 days ahead; confirm the USC preview PDF in Telegram. Expect "🧪 Modo de solo lectura … Trámite simulado creado".
4. `/congreso_dieta` again: the case card appears; days of that case are disabled.
5. Cancel it from the card and confirm in the chat: the case disappears.
6. Restart the bot with a case whose prompt is due: the prompt is sent right after startup (misfire `RUN_LATE`).

- [ ] **Step 6: Commit** (only with user approval)

```bash
git add docs/congreso_dieta.md README.md docs/superpowers/specs/2026-09-25-congreso-dieta-design.md
git commit -m "docs(congreso_dieta): setup guide and spec sync"
```
