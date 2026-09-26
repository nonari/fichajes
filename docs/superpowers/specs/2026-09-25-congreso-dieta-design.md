# `congreso_dieta` plugin — design

Status: **implemented** (plan: docs/superpowers/plans/2026-09-25-congreso-dieta.md).

## Purpose

Automate the paperwork around attending a congress: request the USC congress authorization,
request the matching absence, wait for the signed authorization, fill the per-diem spreadsheet,
join both PDFs, sign the result with AutoFirma and deliver it.

## User-facing behaviour (*Decided*)

- `/congreso_dieta` (no arguments) opens a calendar Mini App to select the congress days. There is a **single flow**:
  the spreadsheet template imposes one rigid kind of trip, so there are no named configurations to choose from.
  Several cases (trips) may be in progress at the same time; each run starts a new case.
  - Selection is a **continuous range** (start–end) within one calendar year (USC absence requests take a single
    year). The congress request uses start/end. The absence request uses every day in the range except weekends,
    Galicia holidays and USC non-working days.
- After the selection, the bot builds the congress request from the plugin configuration plus the dates,
  sends USC's preview PDF to Telegram and submits **only after the user confirms** (existing `Confirmation` flow),
  unless the global `congress_confirmation_enabled` is `false` (then it submits directly); the timeout is
  `congress_confirmation_timeout_seconds`.
- **N days before the start date**, at a configured time, the bot asks in Telegram whether to request the absence,
  with inline buttons ("Solicitar ausencia" / "Ahora no").
  - If the trip starts sooner than N days away, the first prompt is sent at the next slot inside the window.
  - Reminders every 30 minutes (configurable), **only inside a configured working-hours window**
    (e.g. 08:00–20:00), resuming the next day, until the user answers or the start date begins (00:00); then the
    user is told the absence was not requested and the rest of the case continues.
  - Inline buttons, not the "Sí/No" reply keyboard, so the prompt cannot be confused with the daily check-in question.
  - "Solicitar ausencia" requests the absence; the configured hours are sent only if the USC absence type requires
    hours. The screenshot confirmation is used only if the global `absence_confirmation_enabled` is `true`.
  - "Ahora no" cancels only the absence request; the rest of the flow continues.
- A **daily check** (configured time) of the USC requests page detects when the congress authorization is signed:
  the request detail shows state `Tramitada` and an **Autorización** link, which is downloaded and stored.
- The spreadsheet PDF is generated on **max(last absence day + 1, day the authorization is available)**, during the
  daily check run (`auth_check_time`); when the authorization is found on or after that day, generation, signing and
  delivery follow in the same run.
- The spreadsheet PDF and the authorization PDF are joined, signed with AutoFirma and
  **sent to Telegram and saved to a configured folder**.

## Architecture (*Decided*)

### Persistence: per-case state + core scheduled tasks

Each `/congreso_dieta` run creates a *case* persisted as JSON in `.plugin_data/congreso_dieta.json` (next to
`config.json`, written with `write_json_atomic`). A case stores a snapshot of the plugin config, the dates, its
state, its last problem and the paths of the artefacts produced so far; artefacts (authorization PDF, spreadsheet
PDF, joined and signed PDFs) live in `.plugin_data/congreso_dieta/<case id>/`, removed when the case is cancelled.

*Revised:* instead of a periodic tick, the plugin wakes up through the core task scheduler
(`docs/superpowers/specs/2026-09-25-task-scheduler-design.md`): each step schedules the next one-shot task
(kinds `congreso_dieta.*`, payload `{"case": "<id>"}`), and the daily authorization check is a declared
recurring job. The case state below remains the source of truth for what has been done.

Indicative states: `congress_submitted → absence_scheduled → absence_asking →
absence_done | absence_skipped | absence_simulated → awaiting_auth → auth_received → generated → signed → delivered`,
plus `cancelled`. There is no terminal failure state: failed steps record `last_problem` and the case waits or
retries as described in *Failures and notifications*.

- Restart recovery: persisted tasks are restored by the core scheduler; plugin kinds use misfire `RUN_LATE`, so
  steps missed while the bot was down run right after startup.
- State is persisted before and after every USC action. An interrupted submission is flagged to the user for manual
  checking, never retried automatically (interrupted policy `NOTIFY`); read-only checks may use `RETRY`.
- Plugin tasks live in the core `.schedule.data` but are invisible to the marks views (`/pendientes`, `/cancelar`,
  the daily-question check), which filter by `kind == "mark"`.
- Atomic JSON persistence comes from the shared `fichaxebot/storage.py` helper (already implemented).

### Plugin system extensions

Today plugins can only export `COMMANDS`. This plugin additionally needs:

1. `WEBAPP_CONTROLLERS`: merged into the router's `type → handler` table (`webapp_controller/router.py`).
2. `setup(application)`: registers callback handlers, its task kinds and recurring jobs (core scheduler), and loads
   persisted cases. Defined in the task-scheduler spec; this plugin relies on it.
3. Plugin configuration in `config.json` under `"plugin_config": {"congreso_dieta": {...}}`, validated by the plugin.

## Mini App (*Decided*)

`/congreso_dieta` opens a single page (`docs/congreso.html` + `docs/congreso.js`, served like the other Mini Apps):

1. **"Trámites en curso"** (only when cases exist): one card per case with its dates, the current step in plain
   Spanish (e.g. "Pendiente de confirmar la ausencia el 09/10", "Esperando la autorización firmada") and a
   **Cancelar** button.
2. **New request**: calendar for a continuous range and a **Solicitar** button. Days before today + 5 (USC minimum
   notice) and days covered by open cases are disabled; weekends and holidays are selectable (they are only excluded
   from the absence request).

Communication (static page, no backend):

- The bot passes a snapshot of open cases and a one-time launch token in the page URL (same technique as `/vacaciones`).
- Each button sends one `Telegram.WebApp.sendData` message and closes the page:
  `{"type": "congreso_dieta_new", "token", "start", "end"}` or `{"type": "congreso_dieta_cancel", "token", "case"}`.
  Running `/congreso_dieta` again gives a fresh snapshot.
- The bot re-validates on receipt: token valid and unused, case still exists, no overlap with open cases.
- Handlers are registered through the plugin's `WEBAPP_CONTROLLERS` export, merged into the router's table.

Cancelling a case:

- The bot first asks for confirmation in the chat (inline Sí / No).
- It then stops following the case: removes its scheduled tasks and files. Nothing already sent to USC is undone; the
  bot lists what was submitted, with a link to the user's USC requests.

## Configuration (*Decided*)

`config.json` gains `plugin_config`; the core only checks it is an object and passes each plugin its section, which
the plugin validates in `setup()` (invalid config stops the bot at startup with a clear message).

```jsonc
"plugins": ["congreso_dieta"],
"plugin_config": {
  "congreso_dieta": {
    "webapp_url": "https://…/congreso.html",        // the Mini App page
    "output_dir": "/path/to/dietas",                 // signed PDFs are saved here
    "auth_check_time": "10:00",                      // daily authorization check
    "prompt": {"at": "09:00", "reminder_minutes": 30, "window": ["08:00", "20:00"]},
    "signing": {"store": "mozilla", "alias": "…", "password": null},   // or "pkcs12:/path/cert.p12"
    "days_before": 3,                                // absence prompt N days before the start (≥ 1)
    "spreadsheet_template": "/path/to/GL_VISITAS_PLANTA_FINSA_04_03_2026.xlsm",
    "absence": {"type": "<USC absence type name>", "start_time": "08:00", "end_time": "15:00"},
    "congress": { /* docs/congress_api.md request fields except start_date / end_date */ }
  }
}
```

- Absence type is given by **name**, matched against USC's live catalogue ignoring case and repeated whitespace when
  the absence is requested; no match → the user is notified and the case waits.
- The same `start_time`/`end_time` apply to every absence day.
- Startup validation: `HH:MM` times, window start before end, template and attachment files exist, `output_dir` is
  writable, congress fields pass the existing `validate_request` with placeholder dates.
- Each case snapshots the config at creation; editing `config.json` does not alter cases in progress.

## Failures and notifications (*Decided*)

Rule: steps that **write to USC** (congress request, absence request) are **never retried automatically**; failures
or unclear outcomes are reported and the case waits for the user. Read-only or local steps (authorization check,
generation, signing, delivery) **retry at the next daily check**.

| Step | Failure | Behaviour |
| --- | --- | --- |
| Congress request | preview rejected or confirmation timeout | no case is created |
| | USC rejects the data | error message; no case created |
| | submitted (API reports "attempted" only) | case created; message asks to check USC (*TODO: request id*) |
| Absence | screenshot confirmation rejected or timed out | back to asking, with the usual reminders |
| | absence type not found / USC rejects the data | message with the reason; back to asking |
| | unclear outcome | "check USC" message; the case continues, no retry |
| Authorization check | page unreachable | silent retry next day; notify after **3 consecutive failed days** |
| | state other than in-progress / `Tramitada` (e.g. denied, annulled) | notify once with USC's state text; case waits for cancel |
| Spreadsheet generation | LibreOffice error or timeout | message with the error; retry next day |
| Signing | AutoFirma error (expired certificate, wrong alias/password…) | message with AutoFirma's error; unsigned joined PDF saved as `…_SIN_FIRMAR.pdf` in `output_dir`; retry next day |
| Delivery | saving or Telegram send fails | message; retry next day |

- Mini App cards show the case's **last problem** as well as its current step.
- Read-only mode: USC writes stop at the final step (`ReadOnlyStop`); the case is marked **simulated**, the absence is
  recorded as simulated, and the authorization check cannot find a request that was never sent, so the case waits
  until cancelled. This allows exercising the flow safely.
- All plugin task kinds use `notify_errors=True`; an unexpected crash is logged and reported and the case keeps its state.

## Testing (*Decided*)

- **Case logic** as functions of (case, event, now) → (new case, actions), tested with the scheduler fakes
  (`tests/scheduler_fakes.py`) and an injectable clock: every row of the failure table, cancellation, overlap checks.
- **Date rules**: absence days (weekends, Galicia holidays, USC non-working days excluded), prompt day/time,
  next reminder slot inside the window, generation day = max(end + 1, authorization day).
- **Config validation**: each rule in the Configuration section.
- **USC pages**: parse the requests list and detail pages from **sanitised fixtures** in `tests/fixtures/` (the saved
  pages in `resources/` contain personal data and are not committed).
- **Spreadsheet**: an integration test running LibreOffice on a real template, **skipped unless** an environment
  variable points to one (same pattern as the `CHROMEDRIVER`-gated browser tests); unit tests cover cell writes and
  the soffice lifecycle/timeout with a fake.
- **Signing**: unit tests build the AutoFirma command and map its errors using a fake runner; a real signature on a
  dummy PDF runs only when an environment variable names the alias.
- **Chat and Mini App**: handler tests in the style of `tests/test_absence_chat.py`; a browser test of the page gated by
  `CHROMEDRIVER`.
- **Manual end-to-end** in read-only mode through the ngrok tunnel before enabling writes.

## Integration facts

### USC requests pages

- List: `resources/requests/Solicitudes.html`. Each entry links to
  `https://aplicacions.usc.es/intranet/solicitudes/solicitude/{id}/ver.htm`; congress entries have identifier
  `RRHH_InvAsistenciaCongresos/{year}/{n}` and a "Código da solicitude" equal to `{id}`.
- Detail: `resources/requests/Solicitude.html`. Shows the state (`Tramitada` when signed) and an **Autorización** link:
  `https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm?solicitudeId={id}&csv={code}`.
- States that will not become signed are recognised by the words deneg/anulad/desist/rexeit/rechaz/arquiv/revogad;
  any other state is treated as in progress. Only `Tramitada` has been observed on a real page.
- **TODO (user will provide):** obtaining the request id returned when the congress authorization is submitted.
  `submit_congress_request` currently returns `{"status": "unverified"}` without an id. Until then the daily check
  cannot target the case's request.

### Spreadsheet (`resources/per_diem/GL_VISITAS_PLANTA_FINSA_04_03_2026.xlsm`, sheet `FormularioCL`)

- Cells updated per case (as real dates): **I17** "Data de ida" = first day, **N17** "Data de volta" = last day,
  **Q78** "DECLARO … con data" = generation day.
- Macro `Módulo1.CreaPDF`: requires **Q3** (código orgánico) to be non-empty, writes the form code
  `P1 = "CL" & Q3 & yymmdd & hhmm`, hides the other sheets, exports `P1.pdf` next to the workbook and ends with a `MsgBox`.
- Always work on a **copy** in a temporary directory; the configured template is never modified.
- Only I17, N17 and Q78 are written. Other cells (e.g. per-diem days/nights, authorising body) are left exactly as in
  the template, by the user's decision, even though the declaration text next to Q78 mentions the authorization date.

#### PDF generation (*Decided*, spike 2026-09-25)

Run the workbook's own `CreaPDF` macro headless (option A):

- Separate LibreOffice process with its own temporary profile (`-env:UserInstallation=file://<tmp>/lo-profile`,
  `--headless --invisible --norestore --nodefault`), driven via Python-UNO over a local socket; it does not interfere
  with a LibreOffice window the user has open.
- Load the copy with `Hidden=True` and `MacroExecutionMode=4` (always execute, no warning); activate `FormularioCL`;
  write the dates as date serials (days since 1899-12-30).
- Invoke `vnd.sun.star.script:VBAProject.Módulo1.CreaPDF?language=Basic&location=document`. The final `MsgBox` does not
  block in headless mode. Read the form code from `P1`; the PDF is `<copy dir>/<P1>.pdf` (one A4 page).
- Verified: output text identical to reproducing the steps through UNO (option B), which remains the fallback if the
  macro ever stops running unattended. LibreOffice 24.2 and the system `python3-uno` are required.
- UNO runs **in-process** (user decision): `uno` is LibreOffice's distro binding (`python3-uno`), not a pip package,
  so the bot's virtualenv is created with **system site-packages** (venv packages still take precedence).
  The local `.venv` was switched on 2026-09-25 (`include-system-site-packages = true`); `install.sh` must create the
  venv with `python3 -m venv --system-site-packages`. The venv's Python must match the system Python minor version.
- UNO calls block, so they run in a worker thread (`asyncio.to_thread`). The plugin starts and stops the headless
  `soffice` process itself and kills it on timeout, so a hung LibreOffice cannot leave the case stuck.
  Generations run one at a time (a lock), since they share the LibreOffice profile and socket.
- Deployment: the plugin needs LibreOffice, `python3-uno`, AutoFirma and the certificate store on the host, so it is
  supported only with the host (systemd) installation, not the Docker image.

### Signing (AutoFirma CLI, verified locally)

```
autofirma sign -i joined.pdf -o signed.pdf -format pades -store <store> -alias "<alias>" [-password <pw>]
```

- `-store` is **configurable**: `mozilla` (Firefox NSS store) or `pkcs12:/path/cert.p12`; optional password.
- `autofirma listaliases -store mozilla` works headless and lists the personal certificate without a password prompt.
- AutoFirma prints the aliases on **stderr** and decodes UTF-8 nicknames as Latin-1 (`Ñ` → `Ã` + U+0091), so the
  correctly spelled alias is rejected. The plugin runs `listaliases` first, repairs each name and signs with
  AutoFirma's raw spelling of the configured one (verified 2026-09-25: valid PAdES signature per `pdfsig`).
- AutoFirma reads the legacy `~/.mozilla/firefox` profile, not the snap profile in use. The certificate is currently
  identical in both (valid until 2027-08-25); after renewal it must be re-imported or the store switched to pkcs12.
- Joining PDFs: `pdfunite` (poppler) is available.
- **Order and signing (*Decided*)**: join spreadsheet PDF first, authorization PDF second, then sign the joined file
  **once** (invisible PAdES). Signing the spreadsheet before joining was rejected: joining writes a new file, which
  drops or breaks any existing signature. The authorization's own embedded signature is also lost in the join; its
  printed CSV code remains verifiable online.

## Known gaps

- **Congress request id (TODO, user will provide).** Without it the daily authorization check cannot target the
  case's request, so cases stop at `awaiting_auth`; everything before that step (congress request, absence) and after
  it (generation, signing, delivery, given an authorization PDF) can be built and tested independently.

## Prerequisites

1. **Done (2026-09-25):** marks scheduler defects fixed — file anchored next to `config.json`, atomic writes via
   `fichaxebot/storage.py` with invalid files moved to `.corrupt`, cancelled jobs removed from the queue,
   naive timestamps read as Madrid time. Tests: `tests/test_scheduler.py`.
2. **Done (2026-09-25):** core task scheduler (`2026-09-25-task-scheduler-design.md`), including the plugin
   `setup(application)` hook. Plugin support added with this plugin: `plugin_config`, `WEBAPP_CONTROLLERS`,
   `UscWebSession.run`, the `congreso` confirmation prefix.
