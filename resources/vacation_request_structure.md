# USC vacation requests

The selectors and endpoints below come from the downloaded USC request form and
its `Resumo` page. Personal details and authentication data are not copied here.

## Selection flow

1. `/vacaciones` reads the current USC options and balances, then the current
   annual calendar. It opens `docs/vacaciones.html`, which loads `vacaciones.js`.
2. The user chooses the **balance year**. Only the current and previous year are
   considered, and each must be offered by USC. The previous year appears only
   if at least one type has a positive remaining-day balance.
3. Every vacation type is listed with its remaining days. Types with less than
   one full day are disabled. Remaining hours are displayed separately and are
   not converted into days.
4. The user selects dates in the current year's calendar, starting today.
   A prior-year balance can therefore fund January dates in the current year.
   USC non-working days and existing approved/requested vacations are blocked.
   Alternative shifts are marked light blue and remain selectable as full days.
5. A summary lists the selected dates. **Crear borrador en USC** sends one
   `vacation_request` message to the bot. Telegram `sendData` closes the Mini App;
   the intermediate steps stay in the Mini App without sending messages.
6. The bot checks the launch identifier and rechecks the USC balances/calendar,
   fills one period per date, clicks **Seguinte**, and verifies the saved draft.
7. Telegram shows the draft's review link and **Solicitar en USC**. Only that
   final button invokes USC's separate submission action. The bot verifies the
   draft again and reads back the saved state after submission.

## Form contract

| Purpose | Selector or value |
|---|---|
| New request | `/pas/solicitude/0` |
| Request form | `#formularioSolicitude` (multipart POST) |
| Request type | `#idTipoSolicitude`, value `4`: Vacacións, permisos e licenzas |
| Balance year | `#ano`; USC uses hidden `#anoForm` when only one year is offered |
| Vacation type | `#idTipoVacacions`, using the option IDs and labels from USC |
| Applicant | `#idSolicitante`, read from the authenticated page |
| Add period | `#engadePeriodo` / `engadirPeriodo()` |
| Start/end | `periodos[n].dataInicio`, `periodos[n].dataFin`, `DD/MM/YYYY` |
| Save draft | `#seguinte` |
| Saved review | `/pas/solicitude/{id}/resumo` |
| Submit draft | Review link ending in `/resumo/solicitar` |

Each selected day is a separate period with identical start and end dates.
Hourly fractioning remains unchecked. No attempt is made to turn a partial
working day into a half-day vacation allowance.

USC's `actualizarDias()` reads `GET /pas/obterNumeroDias` with `idTipoVacacions`,
`idSolicitante`, and `ano`. Use its `numeroDiasDisponhibles` directly rather than
calculating an allowance from the vacation-info table. A failed or malformed
balance response stops the selection load; it does not become a zero balance.

The saved review exposes `Estado`, `Ano`, `Tipo de solicitude`, and
`Tipo de vacacións, permisos e licenzas` as `p.h5` labels followed by values.
`#taboaPeriodos` contains the saved start/end dates. Those values must match the
selection before submission. The observed initial review state is `Borrador`.

## State and validation

The Mini App URL carries its data in a fragment. The returned message contains
only the launch ID, balance year, vacation-type ID, and dates. The bot validates
these against server-held data and fresh USC data; client-provided labels or
balances are not authoritative.

Browser operations share a lock, so attendance jobs cannot navigate away while
a vacation request is being read, filled, or submitted. Launch IDs and draft
confirmation state live in Telegram's user data in memory. After a restart,
existing USC drafts can still be opened through their review links, but old bot
confirmation buttons expire.

Submission is never automatically retried on an uncertain outcome. The bot
reports the saved state or asks the user to inspect USC before repeating.

## Validation

Run the unit tests without touching the production log:

```sh
FICHAXE_LOG_DIR=/tmp/fichaxe-vacation-tests .venv/bin/python -m unittest discover -s tests -v
```

A local Chrome integration check used the recorded first-form field contract,
the downloaded datepicker resources, and the downloaded review HTML with sample
balances/dates and a local server. It covered draft creation, full-day period
values, review verification, final submission, repeated submission, calendar
markings, selection limits, month navigation, and mobile layouts. It did not
submit a request to the live USC website.
