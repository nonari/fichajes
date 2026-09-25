# FichaxeBot

Telegram bot for USC work records, calendars, and vacation requests. Run it on a computer or server that stays online, using **either systemd or Docker**.

## 1. Download and configure

```bash
git clone https://github.com/nonari/fichajes.git
cd fichajes
cp config.example.json config.json
```

Edit `config.json` and replace these placeholders:

| Setting | Value |
| --- | --- |
| `telegram_token` | Bot token from BotFather. |
| `telegram_chat_id` | ID of your private chat with the bot. |
| `usc_user` | Your USC login username. |
| `usc_pass` | Your USC login password. |

The internal USC user ID is discovered automatically at startup. Keep `config.json` private; Git ignores it.

Only updates from `telegram_chat_id` are handled. Messages, commands, and button callbacks from other chats are silently ignored. Use your private chat ID; a group chat ID would allow members of that group to interact with the bot.

### Create your Telegram bot and get the chat ID

1. Open [@BotFather](https://t.me/BotFather), send `/newbot`, and follow the instructions. Copy its token into `telegram_token`.
2. Open your new bot in Telegram and send `/start`.
3. Before running the app, open this URL with your token substituted:

   ```text
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```

4. Find `result` → an update → `message` → `chat` → `id`. Put that number in `telegram_chat_id`, for example `"123456789"`.

If `result` is empty, send another message and refresh. Stop any running instance first so it does not consume the update. See Telegram's [bot setup guide](https://core.telegram.org/bots/tutorial) and [getUpdates documentation](https://core.telegram.org/bots/api#getupdates).

### Behaviour and Web Apps

| Setting | Example/default behaviour |
| --- | --- |
| `read_only` | The example sets `true`: clock-ins and requests run up to the final USC action, which is skipped. Set `false` only when you want those actions sent. |
| `vacation_confirmation_enabled` | `false`: submit directly. Set `true` to receive a full-page PNG of USC's final summary in Telegram and confirm before submission. |
| `vacation_confirmation_timeout_seconds` | `60`: positive integer seconds to decide, starting after the screenshot is delivered. |
| `daily_question_time` | `09:00`, in Europe/Madrid time. |
| `auto_checkout_delay_minutes` | `420` minutes after clock-in; `0` disables automatic checkout. |
| `auto_checkout_random_offset_minutes` | Random offset of up to `3` minutes either way; `0` disables it. |
| `max_reminders` | Up to `3` reminders; `0` disables reminders. |
| `reminder_interval_minutes` | `5` minutes between reminders. |

Read-only mode runs each procedure up to the final USC action and then stops: clock-ins check the current state, and vacation, absence, and congress requests are filled, reviewed, and confirmed, but not submitted. Vacation and absence requests may leave a draft (*borrador*) in USC, because USC creates it when the form moves to the summary. Daily questions and scheduled-job handling still run.

Scheduled marks survive restarts. Marks whose time passed while the bot was off are not executed; the startup message lists them, together with any mark that was interrupted mid-run (check USC before repeating it). The daily question is asked at most once per day, also after a restart. **Upgrading from a version before the task scheduler:** pending marks are not carried over — the old `.schedule.data` is moved to `.schedule.data.corrupt`; note and re-create them after deploying.

The [congress permission API](docs/congress_api.md) supports optional PDF confirmation through a caller callback. It is not connected to chat; receipt verification remains pending.

The optional [`congreso_dieta` plugin](docs/congreso_dieta.md) automates congress authorization, absence and the signed per-diem document (host installation only).

With vacation confirmation enabled, the Mini App shows **Revisar solicitud**. Telegram sends the screenshot as a document with **Confirmar y enviar** and **Cancelar** buttons. Only the person who started the request can decide. Cancellation, timeout, or failure to capture/deliver the image leaves the request unsubmitted; USC does not save a reusable draft. Open `/vacaciones` again to start over.

The same browser session stays locked while confirmation is pending. Scheduled marks wait and run after the transaction releases the browser; other chat actions receive a busy response. On graceful shutdown, pending confirmation is cancelled and the worker finishes before the browser closes. An already confirmed submission is allowed to finish. Restart the bot after changing these settings and deploy the updated `docs/vacaciones.html` and `docs/vacaciones.js` together with the bot.

The three `*_webapp_url` settings in the example point to this project's GitHub Pages viewers. Keep them to use the supplied pages, or replace them with your own HTTPS URLs. Open the viewers using the bot's buttons so they receive the calendar data.

## 2A. Run on Linux

Install **Python 3.10+**, Python's `venv` support, and **Google Chrome**. Internet access is needed for Telegram, USC, and automatic ChromeDriver setup.

After configuring `config.json`, run from the repository directory:

```bash
bash install.sh "$(pwd)"
```

The installer creates the Python environment, installs dependencies, and uses sudo to install and start `fichaxe.service` automatically at boot. The service runs as root.

```bash
sudo systemctl status fichaxe.service
sudo systemctl restart fichaxe.service
sudo systemctl stop fichaxe.service
tail -f fichaje.log
```

Remove the service with `bash uninstall.sh`. Application logs also go to `/tmp/fichaxe_app/app.log`; set `FICHAXE_LOG_DIR` to change that directory.

## 2B. Run with Docker

Install Docker Engine with the Compose plugin. The supplied image installs Python and Chrome and targets Linux x86-64. Create and fill in `config.json` first, then run:

```bash
docker compose up -d --build
docker compose logs -f fichaxebot
```

Useful commands:

```bash
docker compose restart fichaxebot  # after editing config.json
docker compose down               # stop and remove the container
```

Compose mounts `config.json` read-only and stores application logs in `./logs/app.log`. Scheduled marks are stored inside the container and are lost when it is recreated with the supplied Compose configuration.

## 3. Use the bot

Send commands in your private chat:

| Command | Action |
| --- | --- |
| `/start` | Show basic help. |
| `/marcajes` | Show today's work records. |
| `/calendario` | Open the calendar. |
| `/ausencias` | Request authorized absences, optionally attach PDFs in chat, and confirm the USC screenshot. See [API and setup](docs/absence_api.md). |
| `/vacaciones_info` | Open vacation balances. |
| `/vacaciones` | Select a balance year, vacation type, and dates, then press **Solicitar en USC** to submit. With `read_only: true` it stops before the final submission. |
| `/marcar entrada` or `/marcar salida` | Clock in/out; with `read_only: true` it only checks that the mark is allowed. |
| `/marcar entrada 09:00` | Schedule a mark for a future time today. |
| `/pendientes` | List scheduled marks. |
| `/cancelar` | Cancel scheduled marks. |

Restart the app after changing configuration. Run only one instance per bot token. If commands do not respond, check the process logs, token, and chat ID. If Telegram reports an existing webhook, remove it with its [deleteWebhook method](https://core.telegram.org/bots/api#deletewebhook); this app uses polling.

## 4. Add command plugins

Keep each plugin in a Python package under `plugins/<name>/`. Its `__init__.py`
exports a `COMMANDS` mapping from command names (without `/`) to async callbacks:

```python
# plugins/my_plugin/__init__.py
async def hello(update, context):
    await update.effective_message.reply_text("Hello!")


async def echo(update, context):
    await update.effective_message.reply_text(" ".join(context.args) or "Usage: /echo <text>")


COMMANDS = {"hello": hello, "echo": echo}
```

Add `"plugins": ["my_plugin"]` to `config.json` and restart the bot. Multiple
plugins load in the order listed. Omit the setting or use `"plugins": []` to
disable all plugins; unlisted plugins are not imported. Names must be unique
Python package names, without paths or dots. No changes to the bot's command
registration are needed when adding another plugin.

The included `plugins/plugin1/` example is disabled by default. Enable it with
`"plugins": ["plugin1"]`, restart, then send `/hello` or `/echo some text` in your
configured chat.

Callbacks use the usual Telegram `(update, context)` signature. Command arguments
are available in `context.args`; existing services are available as
`context.application.web_session` and `context.application.scheduler_manager`.
Use `asyncio.to_thread(...)` for blocking browser calls, as the built-in commands
do. Keep supporting code and resources in the plugin folder and use relative
imports such as `from .commands import COMMANDS`. Access app services inside
callbacks, since plugins are imported before the USC browser session starts.

Commands contain 1–32 ASCII letters, digits, or underscores and are
case-insensitive. Invalid exports, import failures, or names colliding with
built-in commands or another enabled plugin stop startup with an error naming
the plugin. Plugin commands inherit the existing restriction to
`telegram_chat_id`.

Plugins run as trusted local Python code in the bot process; the folder is an
organizational boundary, not a sandbox. Install any extra plugin dependencies
in the bot's environment yourself. Restart after code or configuration changes.
With Docker, rebuild the image for code changes using
`docker compose up -d --build`. There is no runtime installation or hot reload.
