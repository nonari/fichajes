# Calendar Data Structure

## 🧩 1. What `calendario` actually is

`calendario` is a **JavaScript array** (`[]`) that holds a sequence of **objects** (`{}`), each representing **a time interval** (usually one or more days) assigned to a specific person in a work-schedule system.

Each object is a **record of one calendar entry** — such as a work shift, a vacation, or a non-working day.

---

## 🧱 2. Structure of each element

```js
{
  idPersoa: number,
  startDate: string,        // format: "YYYY-MM-DD"
  endDate: string,          // format: "YYYY-MM-DD"
  name: string,             // human label (in Galician)
  tipo: string,             // categorical code describing the type of day
  color: string,            // hex color used in the UI
  codigoQuenda: string|null,// optional code identifying a specific shift
  tipoAbsentismo: string|null // optional code identifying absence type
}
```

### Explanation of fields

| Field | Description | Example                                                  | Notes |
|--------|-------------|----------------------------------------------------------|-------|
| `idPersoa` | **Person ID** (integer, internal system identifier) | `12345`                                                  | This is *personal* data; it identifies the individual. |
| `startDate` | Start date of the event | `"2025-04-16"`                                           | ISO date string (UTC-insensitive). |
| `endDate` | End date of the event | `"2025-04-16"`                                           | Often the same as `startDate`, but can span multiple days. |
| `name` | Human-readable description | `"Día laborable (quenda primaria"`                       | In Galician: “Working day (primary shift)”. |
| `tipo` | Symbolic category for the event | `"QUENDA_PRIMARIA"`, `"DIA_VACACIONS_COMPLETA_APROBADA"` | Encoded day type (used by HR/calendar logic). |
| `color` | Display color for UI | `"#085CE3"`, `"#0AB91A"`, `"#ABAAAA"`                    | Tied to the `tipo`. |
| `codigoQuenda` | Shift code (if applicable) | `"INV-2022 Q0"`                                          | May be `null` for non-working days or vacations. |
| `tipoAbsentismo` | Absence type code | `null`                                                   | Non-null only for specific absences (e.g. sick leave). |

---

## 🗂️ 3. Typical values in your data

| `tipo` | Meaning (translated) | Color | Work status |
|:--|:--|:--|:--|
| `QUENDA_PRIMARIA` | Primary shift | `#085CE3` | Working |
| `QUENDA_ALTERNATIVA` | Alternative shift | `#5C90E4` | Working |
| `DIA_NON_LABORABLE` | Non-working day | `#ABAAAA` | Rest |
| `DIA_VACACIONS_COMPLETA_APROBADA` | Approved vacation day | `#0AB91A` | Absent |
| (possibly others) | Sick leave, training, etc. | varies | Absent |

---

## 🕵️ 4. Example

Here’s an example `calendario` array with the same logical structure and variety of day types.

```js
// ✅ Safe anonymized sample calendar data
var calendario = [
  {
    idPersoa: 0,
    startDate: "2025-04-16",
    endDate: "2025-04-16",
    name: "Día de descanso",
    tipo: "DIA_NON_LABORABLE",
    color: "#CCCCCC",
    codigoQuenda: null,
    tipoAbsentismo: null
  },
  {
    idPersoa: 0,
    startDate: "2025-04-17",
    endDate: "2025-04-17",
    name: "Día laborable (quenda A)",
    tipo: "QUENDA_A",
    color: "#007BFF",
    codigoQuenda: "GENERIC-QA",
    tipoAbsentismo: null
  },
  {
    idPersoa: 0,
    startDate: "2025-04-18",
    endDate: "2025-04-18",
    name: "Día de vacacións",
    tipo: "DIA_VACACIONS",
    color: "#33CC33",
    codigoQuenda: null,
    tipoAbsentismo: null
  },
  {
    idPersoa: 0,
    startDate: "2025-04-19",
    endDate: "2025-04-19",
    name: "Día laborable (quenda B)",
    tipo: "QUENDA_B",
    color: "#3399FF",
    codigoQuenda: "GENERIC-QB",
    tipoAbsentismo: null
  }
];
```


## Vacation selection payload

`/vacaciones` requests `fetch_calendar_summary(for_vacation_selection=True)`.
The compact entries use inclusive dates: `P2026-09-11` for one day or
`V2026-09-14:2026-09-16` for a range.

| USC `tipo` | Payload | Selection display |
|---|---|---|
| `DIA_NON_LABORABLE` | `N` | Purple, including weekend entries |
| Vacation types containing `VACACION`, approved or requested | `V` | Green: registered vacations |
| `QUENDA_ALTERNATIVA` | `P` | Light blue with an underline: partial working day |
| `QUENDA_PRIMARIA` | Omitted | Ordinary working day |

The partial-day mapping follows the user’s identification of the light-blue
underline and the shift/color mapping above. The saved USC renderer in
`resources/calendar.html` underlines `QUENDA_PRIMARIA` and
`QUENDA_ALTERNATIVA` using each entry’s `color`. This change was not verified
against a live authenticated Chrome session.

Newly selected dates have an amber outline independent of the three day types.
Selecting, deselecting, and changing months preserve the USC markings.
The ordinary `/calendario` payload retains its existing category filtering.
The request workflow, including draft creation and explicit submission, is described
in [vacation_request_structure.md](vacation_request_structure.md).
