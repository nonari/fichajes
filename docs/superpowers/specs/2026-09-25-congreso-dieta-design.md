# `congreso_dieta` plugin — design (draft)

Status: **draft**. Sections marked *Decided* were agreed during brainstorming on 2026-09-25.
Sections marked *Open* are still under discussion and must be settled before the implementation plan.

## Purpose

Automate the paperwork around attending a congress: request the USC congress authorization,
request the matching absence, wait for the signed authorization, fill the per-diem spreadsheet,
join both PDFs, sign the result with AutoFirma and deliver it.

## User-facing behaviour (*Decided*)

- `/congreso_dieta` without arguments lists the named configurations available in the plugin config.
- `/congreso_dieta <name>` opens a calendar Mini App to select the congress days.
  - Selection is a **continuous range** (start–end). The congress request uses start/end.
    The absence request uses every day in the range except weekends, Galicia holidays and
    USC non-working days.
- After the selection, the bot builds the congress request from the named configuration plus the dates,
  sends USC's preview PDF to Telegram and submits **only after the user confirms** (existing `Confirmation` flow).
- **N days before the start date**, at a configured time, the bot asks in Telegram whether to request the absence,
  with inline buttons ("Solicitar ausencia" / "Ahora no").
  - Reminders every 30 minutes (configurable), **only inside a configured working-hours window**
    (e.g. 08:00–20:00), resuming the next day, until the user answers or the absence starts.
  - Inline buttons, not the "Sí/No" reply keyboard, so the prompt cannot be confused with the daily check-in question.
  - "Solicitar ausencia" requests the absence with the configured hours; the screenshot confirmation is used
    only if the global `absence_confirmation_enabled` is `true`.
  - "Ahora no" cancels only the absence request; the rest of the flow continues.
- A **daily check** (configured time) of the USC requests page detects when the congress authorization is signed:
  the request detail shows state `Tramitada` and an **Autorización** link, which is downloaded and stored.
- The spreadsheet PDF is generated on **max(last absence day + 1, day the authorization is available)**.
- The spreadsheet PDF and the authorization PDF are joined, signed with AutoFirma and
  **sent to Telegram and saved to a configured folder**.

## Architecture (*Decided*)

### Persistence: per-case state + core scheduled tasks

Each `/congreso_dieta <name>` run creates a *case* persisted as JSON in the plugin's own data file
(e.g. `.plugin_data/congreso_dieta.json`, anchored to the project root). A case stores the named config
snapshot, the dates, its state and the artefacts produced so far.

*Revised:* instead of a periodic tick, the plugin wakes up through the core task scheduler
(`docs/superpowers/specs/2026-09-25-task-scheduler-design.md`): each step schedules the next one-shot task
(kinds `congreso_dieta.*`, payload `{"case": "<id>"}`), and the daily authorization check is a declared
recurring job. The case state below remains the source of truth for what has been done.

Indicative states: `congress_requested → absence_due → absence_asking → absence_done | absence_skipped →
awaiting_auth → generating → signed → delivered`, plus `failed`.

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

## Integration facts

### USC requests pages

- List: `resources/Solicitudes.html`. Each entry links to
  `https://aplicacions.usc.es/intranet/solicitudes/solicitude/{id}/ver.htm`; congress entries have identifier
  `RRHH_InvAsistenciaCongresos/{year}/{n}` and a "Código da solicitude" equal to `{id}`.
- Detail: `resources/Solicitude.html`. Shows the state (`Tramitada` when signed) and an **Autorización** link:
  `https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm?solicitudeId={id}&csv={code}`.
- **TODO (user will provide):** obtaining the request id returned when the congress authorization is submitted.
  `submit_congress_request` currently returns `{"status": "unverified"}` without an id. Until then the daily check
  cannot target the case's request.

### Spreadsheet (`resources/GL_VISITAS_PLANTA_FINSA_04_03_2026.xlsm`, sheet `FormularioCL`)

- Cells updated per case (as real dates): **I17** "Data de ida" = first day, **N17** "Data de volta" = last day,
  **Q78** "DECLARO … con data" = generation day.
- Macro `Módulo1.CreaPDF`: requires **Q3** (código orgánico) to be non-empty, writes the form code
  `P1 = "CL" & Q3 & yymmdd & hhmm`, hides the other sheets, exports `P1.pdf` next to the workbook and ends with a `MsgBox`.
- Always work on a **copy** in a temporary directory; the configured template is never modified.

### Signing (AutoFirma CLI, verified locally)

```
autofirma sign -i joined.pdf -o signed.pdf -format pades -store <store> -alias "<alias>" [-password <pw>]
```

- `-store` is **configurable**: `mozilla` (Firefox NSS store) or `pkcs12:/path/cert.p12`; optional password.
- `autofirma listaliases -store mozilla` works headless and lists the personal certificate without a password prompt.
- AutoFirma reads the legacy `~/.mozilla/firefox` profile, not the snap profile in use. The certificate is currently
  identical in both (valid until 2027-08-25); after renewal it must be re-imported or the store switched to pkcs12.
- Joining PDFs: `pdfunite` (poppler) is available.

## Open (to discuss before the implementation plan)

- Full plugin config schema: named-flow fields (congress request data, absence type and hours, N days, prompt time,
  reminder interval, working-hours window), daily-check time, template path, output folder, signing settings.
- Spreadsheet PDF generation: run the `CreaPDF` macro headless via LibreOffice (needs document macros enabled and the
  `MsgBox` must not block — requires a spike) **vs.** reproduce its steps through the LibreOffice UNO API from Python.
- Calendar Mini App: new page vs. reuse of the absences calendar; data payload `type`.
- Order of join (spreadsheet first or authorization first), visible vs. invisible PAdES signature.
- Error handling and user notifications per state; cancellation of a running case.
- Testing strategy (case transitions as functions of state and time with the scheduler's injectable clock,
  fake USC session, fixtures from `resources/`).

## Prerequisites

1. **Done (2026-09-25):** marks scheduler defects fixed — file anchored next to `config.json`, atomic writes via
   `fichaxebot/storage.py` with invalid files moved to `.corrupt`, cancelled jobs removed from the queue,
   naive timestamps read as Madrid time. Tests: `tests/test_scheduler.py`.
2. **Core task scheduler** (`2026-09-25-task-scheduler-design.md`): must be implemented before this plugin.
