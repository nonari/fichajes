# Authorized absence requests

`UscWebSession` provides the Python API; it does not require a running Telegram bot or the Mini App.
It uses the existing project dependencies and configuration (including the
Telegram configuration keys).
USC authentication, `read_only`, and the existing shared browser lock apply.

```python
from fichaxebot.usc_api import UscWebSession

session = UscWebSession()
try:
    catalog = session.fetch_absence_selection_data()
    # catalog: years, today, and types with id, name, requiresHours.
    # Choose an offered year/type; the following values are examples.
    data = {
        "year": 2026,
        "absenceTypeId": "3",
        "periods": [
            {"date": "2026-09-24", "startTime": "09:00", "endTime": "11:00"},
        ],
        "observations": "Asistencia a formación",  # Optional.
        "attachments": ["/path/on/this/machine/proof.pdf"],  # Optional.
    }
    result = session.submit_absence_request(data)
    print(result["id"], result["state"])
finally:
    session.close()
```

For full-day types, each period is simply `{"date": "2026-09-24"}`. For hourly
types, start and end are required in `HH:MM`, with start before end on the same
day. Dates can be past or future, must belong to the selected USC year, and
cannot repeat. Up to 100 dates are accepted by the Python API. The Mini App also
checks Telegram's 4096-byte message limit and may require fewer dates.

Types and hourly requirements are read from USC and refreshed before submission.
Vacation balances and vacation-specific date restrictions do not apply. USC
still validates the request and may reject dates or require supporting evidence.

Attachments are readable, nonempty local PDF files, up to ten files of 1 MiB each.
The API checks their extension, size, and PDF header; USC performs its own file
validation. The caller owns these files: the API does not delete them.
The API returns normalized request details, attachment filenames, the request ID,
and USC state only after verifying the resulting summary in the submitted
`Solicitada` state. Other states are reported as uncertain.

## Screenshot confirmation

Without a callback, the Python API submits directly. With a callback, it supplies
a full-page PNG of the verified USC review before the final submission:

```python
def confirm(png_bytes):
    # Display the image through your own interface and return True or False.
    return your_ui.ask_for_approval(png_bytes)

result = session.submit_absence_request(data, confirm=confirm)
```

Only literal `True` permits submission. Declining or raising prevents the final
action. The callback owns its waiting deadline and must eventually return or
raise. It must not use the shared browser: the entire call holds its lock.
Run the whole call on one worker in asynchronous applications. The existing
`fichaxebot.confirmation.Confirmation` can bridge an async UI, as described in
[the congress API documentation](congress_api.md#shared-asynchronous-confirmation).

- `AbsenceRequestError`: preparation or validation failed before final submission.
- `AbsenceRequestCancelled`: confirmation did not authorize submission.
- `AbsenceRequestUncertain`: final submission was attempted, but acceptance could
  not be verified. Check USC's own requests page before retrying.

Advancing to the review may leave a USC draft after cancellation or errors.
Cancellation prevents the final **Solicitar** action; it does not delete drafts.
Unknown review structures prevent final submission. No automatic retry is made.

## Telegram Mini App and documents from your phone

Configure and host `ausencias.html` and `ausencias.js` together, alongside the
existing static Mini Apps:

```json
{
  "absences_webapp_url": "https://your-host/ausencias.html",
  "absence_confirmation_enabled": true,
  "absence_confirmation_timeout_seconds": 60
}
```

Set `read_only: false` when you intend to submit requests. Restart the bot after
configuration changes. The new settings default to an empty URL, confirmation
enabled, and a 60-second confirmation window. Vacation settings are unchanged.

Open `/ausencias`, choose the year/type/dates, supply hours if required, and add
optional observations. **Quiero adjuntar documentos** starts unchecked:

- Leave it unchecked to prepare the request immediately.
- Check it to send PDFs as Telegram documents after the Mini App closes. Attach
  files from your phone, then press **Continuar**. The bot downloads them to
  temporary files and passes those local paths to the Python API. You never
  enter a filesystem path in Telegram.
- **Continuar sin documentos** explicitly proceeds without attachments;
  **Cancelar** abandons the request. Collection expires after 15 minutes.

The bot then sends the USC review PNG with **Confirmar y enviar** and **Cancelar**.
The confirmation deadline starts after delivery. If confirmation is disabled,
preparation proceeds directly to submission instead.

Only the initiating chat/user may attach documents or confirm. Requests and
buttons are single-use and held in memory, so restarting cancels pending work.
The bot cleans its temporary downloads after the browser worker finishes, or on
cancellation, expiry, or orderly shutdown. Caller-owned API files are untouched.
An abrupt process termination may leave temporary files in the system temp
folder. Removing a local download does not remove its message from Telegram.

The bot handles one interactive USC request at a time. Attachment collection does
not hold the browser lock, so scheduled browser operations can continue. During
preparation and screenshot confirmation, they wait for the shared browser lock.

## Offline verification and live-page limitation

The supplied absence resource contains the initial form, not an absence review
or receipt. Review parsing follows the existing USC summary conventions and
rejects unrecognized layouts. The browser fixture's reviews/receipts are
synthetic; passing offline checks does not establish compatibility with an
unseen live review page. No live request is submitted by the test suite.

```bash
python -m unittest discover -s tests -p 'test_absence*.py'
CHROMEDRIVER=/path/to/chromedriver python -m unittest discover -s tests -p browser_absence_flow.py -v
```

Browser tests block external HTTP(S) URLs and use sanitized local fixtures.
The Mini App tests stub FullCalendar; the wizard fixture simplifies USC
jQuery/DateTimePicker widgets. These checks cover our wiring and validation,
not those third-party widgets themselves.
