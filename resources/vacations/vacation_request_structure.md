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
5. **Solicitar en USC** sends one `vacation_request_submit` message directly
   from the date-selection screen. Telegram `sendData` closes the Mini App;
   the intermediate steps stay in the Mini App without sending messages.
6. The bot checks the launch identifier and rechecks the USC balances/calendar,
   fills one period per date, clicks **Seguinte**, verifies the transient USC
   summary, and immediately clicks **Solicitar** in the same browser session.
7. The bot reads the resulting page in place and reports USC's state and the
   selected dates. There is no second confirmation button or review link.

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
| Advance to transient summary | `#seguinte` |
| Transient summary | `/pas/solicitude/{id}/resumo`; never reopen it |
| Submit request | Current summary's link ending in `/resumo/solicitar` |

Each selected day is a separate period with identical start and end dates.
Hourly fractioning remains unchecked. No attempt is made to turn a partial
working day into a half-day vacation allowance.

USC's `actualizarDias()` reads `GET /pas/obterNumeroDias` with `idTipoVacacions`,
`idSolicitante`, and `ano`. Use its `numeroDiasDisponhibles` directly rather than
calculating an allowance from the vacation-info table. A failed or malformed
balance response stops the selection load; it does not become a zero balance.

The transient summary exposes `Estado`, `Ano`, `Tipo de solicitude`, and
`Tipo de vacacións, permisos e licenzas` as `p.h5` labels followed by values.
Its periods table has **no ID**, with headers `Dende`, `Ata`, and
`Número de días`. Each row must match one selected date and exactly one full
day before submission. USC labels the intermediate state `Borrador`, but this
does not mean it has saved a durable draft: the wizard must finish in the same
uninterrupted session.

## State and validation

The Mini App URL carries its data in a fragment. The returned message contains
only the launch ID, balance year, vacation-type ID, and dates. The bot validates
these against server-held data and fresh USC data; client-provided labels or
balances are not authoritative.

One browser lock covers fresh validation, filling the form, advancing to the
summary, final submission, and reading the result. Attendance jobs cannot
navigate away between those steps. The launch ID lives in Telegram's user data
in memory and is consumed before browser work, preventing duplicate messages
from submitting twice. Old launch IDs expire on restart.

Submission is never automatically retried on an uncertain outcome. The bot
reports USC's resulting state or asks the user to check their requests before
repeating. It never reloads the temporary summary to resume the wizard or
verify submission.

Deploy the HTML and JavaScript together with the bot change.

## Validation

Run the unit tests without touching the production log:

```sh
FICHAXE_LOG_DIR=/tmp/fichaxe-vacation-tests .venv/bin/python -m unittest discover -s tests -v
```

The unit tests cover validation, the browser lock across the complete request,
duplicate messages, unknown payload rejection, read-only mode, and uncertain
submission without retry. The offline Chrome tests exercise the current Mini
App script and a sanitized transient USC summary matching the recorded table
structure. HTTP/HTTPS is blocked in those tests; they do not submit to live USC.

```sh
FICHAXE_LOG_DIR=/tmp/fichaxe-vacation-tests CHROMEDRIVER=/path/to/chromedriver \
  .venv/bin/python -m unittest discover -s tests -p browser_vacation_flow.py -v
```
