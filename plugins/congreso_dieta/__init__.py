"""congreso_dieta plugin: congress authorization, absence and signed per-diem document."""
from telegram.ext import CallbackQueryHandler

from fichaxebot.config import get_config
from fichaxebot.scheduler import Interrupted, Misfire
from fichaxebot.utils import get_madrid_now
from plugins.congreso_dieta.cases import CaseStore
from plugins.congreso_dieta.config import NAME, parse_config
from plugins.congreso_dieta.flow import CALLBACK_PATTERN, DAILY_JOB, PROMPT_KIND, CongresoDieta

PLUGIN_KEY = "congreso_dieta"


def _plugin(context) -> CongresoDieta:
    return context.application.bot_data[PLUGIN_KEY]


async def congreso_dieta(update, context):
    await _plugin(context).open_app(update, context)


async def _new_request(update, context, data):
    await _plugin(context).handle_new(update, context, data)


async def _cancel_request(update, context, data):
    await _plugin(context).handle_cancel(update, context, data)


COMMANDS = {"congreso_dieta": congreso_dieta}
COMMAND_DESCRIPTIONS = {"congreso_dieta": "Congreso: autorización, ausencia y documento de dieta firmado"}
WEBAPP_CONTROLLERS = {"congreso_dieta_new": _new_request, "congreso_dieta_cancel": _cancel_request}


def setup(application) -> None:
    raw = get_config().plugin_config.get(NAME)
    config = parse_config(raw, today=get_madrid_now().date())
    store = CaseStore()
    store.load()
    plugin = CongresoDieta(application, config, store)
    application.bot_data[PLUGIN_KEY] = plugin
    # Sending a question twice is harmless, so interrupted prompts are retried.
    application.scheduler.register_kind(PROMPT_KIND, plugin.run_absence_prompt, misfire=Misfire.RUN_LATE,
                                        interrupted=Interrupted.RETRY, notify_errors=True)
    application.scheduler.register_daily(DAILY_JOB, plugin.run_daily, at=config.auth_check_time,
                                         catch_up=True, notify_errors=True)
    application.add_handler(CallbackQueryHandler(plugin.handle_callback, pattern=CALLBACK_PATTERN))
