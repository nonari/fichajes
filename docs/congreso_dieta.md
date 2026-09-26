# congreso_dieta plugin

`/congreso_dieta` opens a Mini App to request the USC congress authorization for a date range, manage cases in
progress and cancel them. N days before the start the bot asks whether to request the matching absence. When USC
marks the authorization `Tramitada`, the bot fills the per-diem workbook through its own `CreaPDF` macro, joins the
spreadsheet PDF with the authorization, signs the result with AutoFirma, sends it in Telegram and saves it.

## Without the congress authorization

For a special procedure when there is no time to formalise the authorization, tick **Sin autorización de congreso
(procedimiento especial)** in the Mini App. The five days' notice then does not apply: any day of the current year can
be chosen, including past ones. Nothing is sent to USC for the congress. The bot asks about the absence straight away
and keeps reminding until you answer, even once the congress has started. After the congress ends and the absence
question is answered, the per-diem workbook alone is signed, sent and saved.

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
    "webapp_url": "https://nonari.github.io/fichajes/plugins/congreso_dieta/congreso.html",
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

The Mini App lives in `plugins/congreso_dieta/web/` and is published with the other pages (see the README's plugin
section); `webapp_url` may point at any HTTPS copy of it.

The global `congress_confirmation_enabled` (default `true`) and `congress_confirmation_timeout_seconds` decide
whether USC's preview PDF is confirmed in Telegram before the congress request is submitted.

`congress` accepts the fields of [the congress API](congress_api.md) except the dates. The absence type is matched
by name against USC. Invalid settings disable the plugin (the rest of the bot keeps working); a Telegram message at
startup names the setting to fix.

## Known gap

The request id of a submitted congress request is not read yet, so cases wait at "Esperando la autorización
firmada" until that step is added. Everything else can be exercised in read-only mode.

## Optional local tests

- `CONGRESO_TEMPLATE=/path/workbook.xlsm` runs the real LibreOffice macro test.
- Signing is checked outside the test suite: `.venv/bin/python devtools/sign_dummy_pdf.py` signs a dummy PDF
  with the plugin's code and the `signing` settings from `config.json`, then verifies it with `pdfsig`.
- `CHROMEDRIVER=/path/chromedriver` runs the Mini App browser test.
