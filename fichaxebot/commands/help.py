"""/ayuda and the Telegram command menu, built from the handlers actually registered."""
from telegram import BotCommand, Update
from telegram.ext import CommandHandler, ContextTypes

from fichaxebot.plugins import COMMAND_DESCRIPTIONS_KEY

CORE_DESCRIPTIONS = {
    "start": "Presentación del bot",
    "ayuda": "Lista de comandos disponibles",
    "marcar": "Fichar entrada o salida, ahora o a una hora: /marcar entrada|salida [HH:MM]",
    "marcajes": "Marcajes de hoy",
    "pendientes": "Marcajes programados",
    "cancelar": "Cancelar los marcajes programados",
    "calendario": "Abrir el calendario",
    "vacaciones": "Solicitar vacaciones",
    "vacaciones_info": "Consultar los saldos de vacaciones",
    "ausencias": "Solicitar ausencias autorizadas",
}


def registered_commands(application) -> list[tuple[str, str]]:
    """Every registered command in registration order, with its description ('' if none)."""
    descriptions = {**CORE_DESCRIPTIONS, **application.bot_data.get(COMMAND_DESCRIPTIONS_KEY, {})}
    commands = []
    for handlers in application.handlers.values():
        for handler in handlers:
            if isinstance(handler, CommandHandler):
                commands.extend(command for command in sorted(handler.commands) if command not in commands)
    return [(command, descriptions.get(command, "")) for command in commands]


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    lines = [f"/{command} — {description}" if description else f"/{command}"
             for command, description in registered_commands(context.application)]
    await update.message.reply_text("Comandos disponibles:\n" + "\n".join(lines))


async def publish_command_menu(application) -> None:
    """Make Telegram's '/' menu match the registered commands (descriptions are mandatory there)."""
    await application.bot.set_my_commands(
        [BotCommand(command, description or f"/{command}") for command, description in registered_commands(application)]
    )
