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

## Procedure status and manual actions

Each open procedure shows its date range first, followed by these rows:

- **Autorización**, when required: ✅ once received, ⏱️ while pending, or ❌ if its check fails or USC rejects it.
- **Ausencia**: ✅ submitted (or unnecessary because there are no working days), ⏱️ scheduled/in progress,
  or ❌ declined, failed, simulated, or unconfirmed.
- **Firma**: ✅ signed, ⏱️ pending/in progress, or ❌ if document generation or signing failed.

Click an available **Ausencia** row to request it now, including a previously declined request. Telegram asks
for confirmation before starting. If the previous submission was unconfirmed, it warns you to check USC first
to avoid creating a duplicate. The configured final USC screenshot confirmation still applies.

Click an available **Firma** row to generate and sign immediately after the congress's final day, without waiting
for the daily job. Telegram asks for confirmation. A procedure requiring authorization still needs that document;
an already signed document is never signed again. A signing failure retries the existing generated document.

Error details appear beneath the status rows and remain associated with the failed step. Successful retries clear
that step's error. Manual and daily document operations cannot run on the same procedure simultaneously.

After delivery, once absence handling is settled, the procedure is marked as finished and stays in the list for
24 hours before it is removed. While it is listed, click its **Firma** row to repeat the signature: after
confirmation the already generated document is signed again (it is not regenerated), the saved PDF is replaced and
sent again, and the 24 hours start over. If the repeated signature fails, the procedure stays until it succeeds.
**Quitar de la lista** removes a finished procedure straight away.

If you manually sign while the absence question is still open, the procedure remains visible with ✅ Firma and the
absence action available. The PDF is not delivered again on subsequent daily checks.

## Requirements (host installation only)

- Enable the plugin in `config.json` before running `install.sh`. Its local
  [`install.py`](../plugins/congreso_dieta/install.py) installs missing Debian/Ubuntu
  packages (`libreoffice-calc`, `python3-uno`, `poppler-utils`) using apt and sudo when needed.
- The hook verifies UNO in the bot's Python. If an existing virtualenv hides the installed
  system package, it enables `include-system-site-packages` and verifies the import in a new
  process. Use a virtualenv based on `/usr/bin/python3`; incompatible Python versions fail
  setup with a diagnostic instead of failing during document generation.
- Install AutoFirma separately so its `autofirma` command is available. The hook checks
  `soffice`, `pdfunite`, and `autofirma` and stops if any is missing.
- A certificate in the Firefox store AutoFirma reads (`autofirma listaliases -store mozilla`) or a `.p12` file.
  Write `signing.alias` with the certificate name as Firefox or `certutil` show it (e.g. with `Ñ`). AutoFirma
  lists accented names mis-encoded (`Ñ` → `Ã` + an invisible character); the plugin matches and uses that form
  itself, and a wrong name fails with the list of available certificates.
  If the bot runs as another user (including older installations running as root), set
  `signing.mozilla_profiles_ini` to the absolute path of your Firefox `profiles.ini`, for example
  `/home/user/snap/firefox/common/.mozilla/firefox/profiles.ini`. The plugin passes that file to AutoFirma
  for both alias lookup and signing. Without it, AutoFirma uses the service user's Firefox store.
  The configured file must be readable at startup; the service also needs access to the profile it references.

You can rerun enabled plugin dependency hooks without modifying or starting the service:

```bash
.venv/bin/python devtools/install_plugins.py
```

Dependency setup does not generate documents, sign PDFs, or access certificates. Restart
the bot after setup so its running Python process sees any environment changes.

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
    "absence": {"type": "DESPRAZAMENTOS_AUTORIZADOS", "start_time": "08:00", "end_time": "15:00"},
    "congress": {
      "data_processing_authorized": true,
      "address": {"country": "España", "province": "Coruña, A", "municipality": "Santiago de Compostela",
                  "postal_code": "15782", "line1": "…"},
      "reason": "…", "organization": "…", "employment_category": "CONTRATADOS_PREDOUTORAIS",
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

Set `absence.type` to the normalized `AbsenceType` member name, such as `DESPRAZAMENTOS_AUTORIZADOS`.
Both settings require exact uppercase names with underscores; display names and web codes are not config values.
See the [supported absence types](absence_api.md#supported-types); `Asistencia a congresos` is not a valid type.
Set `congress.employment_category` to the normalized `EmploymentCategory` member name from the [employment categories](congress_api.md#employment-categories).

Both catalogs and their validators belong to the app in
[`fichaxebot/usc_types.py`](../fichaxebot/usc_types.py), and the USC API validates them independently of plugins.
Plugin setup reuses these validators before registering jobs. Invalid values disable the enabled plugin and
produce a startup error naming `absence.type` or `congress.employment_category` under
`plugin_config.congreso_dieta`, including the rejected value and valid choices.
Browser selectors use the official USC display names stored in the enums. The web codes are retained separately.
Absence availability is also checked against the live USC catalog when requested.
The example above is a configuration example; select the values that apply to you.

## Known gap

The request id of a submitted congress request is not read yet, so cases wait at "Esperando la autorización
firmada" until that step is added. Everything else can be exercised in read-only mode.

## Optional local tests

- `CONGRESO_TEMPLATE=/path/workbook.xlsm` runs the real LibreOffice macro test.
- Signing is checked outside the test suite: `.venv/bin/python devtools/sign_dummy_pdf.py` signs a dummy PDF
  with the plugin's code and the `signing` settings from `config.json`, then verifies it with `pdfsig`.
- `CHROMEDRIVER=/path/chromedriver` runs the Mini App browser test.
