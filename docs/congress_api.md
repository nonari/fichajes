# Congress permission API

`UscWebSession.submit_congress_request(data, confirm=None)` completes the USC congress permission wizard. It uses the same authenticated browser and lock as the other USC operations and respects `read_only`. There is no congress command, Mini App, Telegram handler, or new configuration setting.

The API currently attempts the final **Seguinte** action once and returns:

```python
{"status": "unverified", "submission_attempted": True}
```

**Pending:** receipt parsing and verified-success detection require a saved post-submission page. This result means the final action was attempted; it does not prove USC accepted the request. A browser error during that action also produces this result. Check USC before retrying. The hidden form ID is not a receipt/reference number.

## Request data

```python
from datetime import timedelta
from fichaxebot.utils import get_madrid_now

start = get_madrid_now().date() + timedelta(days=10)
data = {
    "data_processing_authorized": True,
    "contact": {  # Optional overrides; omitted values retain the USC profile values.
        "email": "person@example.test",
        "phone": "123456789",
    },
    "address": {
        "country": "España",
        "province": "Coruña, A",
        "municipality": "Santiago de Compostela",
        "postal_code": "15782",
        "line1": "Example street 1",
        "line2": "",           # Optional.
        "observations": "",    # Optional.
    },
    "reason": "Attendance at a research congress",
    "organization": "Example University",  # USC's “Razón social” field.
    "employment_category": "PREDOUTORAIS",
    "teaching_assigned": False,
    # "teaching_cover": "Covered by ...",  # Required when teaching_assigned is True.
    "start_date": start.isoformat(),
    "end_date": (start + timedelta(days=2)).isoformat(),
    "supervisor_query": "Supervisor's full name or identity document",
    "attachments": [  # Optional; paths must exist on the bot's machine.
        {"title": "Programme", "path": "/path/to/programme.pdf"},
    ],
}
```

- The authenticated applicant's identity comes from USC and is not editable through this API.
- Country, province and municipality use the labels offered by USC, matched ignoring case and repeated whitespace. Spanish addresses require province and municipality. Foreign addresses require `address.department` instead.
- Dates use `YYYY-MM-DD`. The saved USC form requires at least five days' notice; the API also checks the live datepicker limits. The end cannot precede the start.
- Supervisor search requires at least three characters. An exact unique match is preferred; otherwise a single suggestion is required. Ambiguous searches fail instead of choosing a person arbitrarily.
- Attachments are optional, at most ten, with titles of at most 50 characters and readable, nonempty local files. USC may apply additional validation.
- USC employment codes are `Proxectos`, `JIN`, `MARIECURIE`, `PREDOUTORAIS`, `POSDOUTORAIS`, `BeatrizGalindo`, `DISTINGUIEOD`, `JUANDELACIERVA`, `RAMONYCAJAL`, and `TECNICOAPOIO`. The spelling of each code follows USC.

## Optional PDF confirmation

Without a callback the API submits directly and does not download the preview PDF. With a callback, it downloads USC's original PDF using the authenticated browser session and calls `confirm(pdf_bytes)` before the final action. Only the literal result `True` authorizes submission. `False`, timeout, or callback failure prevents submission.

```python
def confirm(pdf_bytes: bytes) -> bool:
    # Display/send the original PDF, wait for a decision, and return True or False.
    return your_confirmation_ui(pdf_bytes)

result = session.submit_congress_request(data, confirm=confirm)
```

This is a synchronous API. The entire call, including confirmation, stays on one thread holding the browser lock. Scheduled marks wait for that lock. Do not use the same browser from the confirmation callback. A plain callback owns its wait time; it must eventually return or raise.

## Shared asynchronous confirmation

`fichaxebot.confirmation.Confirmation` provides the reusable lifecycle already used by vacation confirmation: document delivery, a deadline starting after delivery, first-decision handling, worker bridging, cancellation, and shutdown joining. It has no Telegram dependency and treats document bytes as opaque.

```python
import asyncio
from fichaxebot.confirmation import Confirmation

async def deliver_pdf(pdf_bytes):
    # Return after successful delivery. The caller supplies filename and MIME type:
    # solicitud-congreso.pdf / application/pdf.
    await your_ui.send_document(pdf_bytes)

confirmation = Confirmation(deliver_pdf, timeout=60)
task = asyncio.create_task(confirmation.run(session.submit_congress_request, data))

# In authenticated UI decision handlers, on the same event loop:
# confirmation.resolve(True)   # Confirm, only after delivery and before expiry.
# confirmation.resolve(False)  # Cancel.
# resolve returns False for premature, expired or duplicate decisions.

result = await task

# During shutdown, BEFORE closing the shared browser:
# await confirmation.stop(task)
```

The caller owns UI authorization, routing, busy handling, and removal of its confirmation controls. Congress is not registered with the bot. Vacation retains its existing Telegram adapter, settings and wording. Shutdown aborts an undecided confirmation; an accepted transaction is allowed to finish. Cancelling the asynchronous task waits for the underlying worker rather than abandoning it.

The asynchronous delivery callback must honor cancellation and must not block the event loop. Aborting while delivery is in progress cancels that delivery before releasing the waiting worker.

`CongressRequestError` means the final action was not attempted. `CongressRequestCancelled` means confirmation was declined or could not complete, also before submission. Both release the browser lock. The shared component's `reason` distinguishes cancellation, expiry, delivery failure and shutdown for the caller's messages.

## Offline verification

Run `python -m unittest discover -s tests`. To exercise the real browser against a local sanitized wizard, set `CHROMEDRIVER` and run `python -m unittest discover -s tests -p browser_congress_flow.py -v`. Those browser tests block USC destinations and serve all test pages and PDFs on localhost.
