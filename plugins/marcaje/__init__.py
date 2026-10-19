"""marcaje plugin: clock in/out now or at a set time, daily question and automatic checkout."""
from telegram.ext import MessageHandler, filters

from fichaxebot.config import get_config
from plugins.marcaje import marks
from plugins.marcaje.commands import cancel, mark, show_pending
from plugins.marcaje.config import NAME, parse_config
from plugins.marcaje.question import DAILY_JOB, ask_for_check_in, process_response

COMMANDS = {"marcar": mark, "pendientes": show_pending, "cancelar": cancel}
COMMAND_DESCRIPTIONS = {
    "marcar": "Fichar entrada o salida, ahora o a una hora: /marcar entrada|salida [HH:MM]",
    "pendientes": "Marcajes programados",
    "cancelar": "Cancelar los marcajes programados",
}


def setup(application) -> None:
    config = parse_config(get_config().plugin_config.get(NAME, {}))
    application.bot_data[NAME] = config
    marks.register(application.scheduler)
    application.scheduler.register_daily(DAILY_JOB, ask_for_check_in, at=config.daily_question_time,
                                         weekdays=range(5), catch_up=True)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_response))
