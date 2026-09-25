# congreso_dieta plugin

`/congreso_dieta` opens a Mini App to request the USC congress authorization for a date range, manage cases in
progress and cancel them. N days before the start the bot asks whether to request the matching absence. When USC
marks the authorization `Tramitada`, the bot fills the per-diem workbook through its own `CreaPDF` macro, joins the
spreadsheet PDF with the authorization, signs the result with AutoFirma, sends it in Telegram and saves it.

## Requirements (host installation only)

- LibreOffice with `python3-uno`; the virtualenv must be created with `--system-site-packages` (`install.sh` does it).
- `pdfunite` (poppler-utils) and the AutoFirma command line (`autofirma`).
- A certificate in the Firefox store AutoFirma reads (`autofirma listaliases -store mozilla`) or a `.p12` file.
  Write `signing.alias` with the certificate name as Firefox or `certutil` show it (e.g. with `Ñ`). AutoFirma
  lists accented names mis-encoded (`Ñ` → `Ã` + an invisible character); the plugin matches and uses that form
  itself, and a wrong name fails with the list of available certificates.

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

The global `congress_confirmation_enabled` (default `true`) and `congress_confirmation_timeout_seconds` decide
whether USC's preview PDF is confirmed in Telegram before the congress request is submitted.

`congress` accepts the fields of [the congress API](congress_api.md) except the dates. The absence type is matched
by name against USC. Invalid settings stop the bot at startup with a message naming the setting.

## Known gap

The request id of a submitted congress request is not read yet, so cases wait at "Esperando la autorización
firmada" until that step is added. Everything else can be exercised in read-only mode.

## Optional local tests

- `CONGRESO_TEMPLATE=/path/workbook.xlsm` runs the real LibreOffice macro test.
- `CONGRESO_SIGN_ALIAS="<alias>"` (optionally `CONGRESO_SIGN_STORE`, `CONGRESO_SIGN_PASSWORD`) signs a dummy PDF.
- `CHROMEDRIVER=/path/chromedriver` runs the Mini App browser test.
