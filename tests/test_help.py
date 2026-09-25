import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import BotCommand
from telegram.ext import CommandHandler

from fichaxebot.commands.help import publish_command_menu, registered_commands, show_help
from fichaxebot.commands.start import start
from fichaxebot.plugins import COMMAND_DESCRIPTIONS_KEY


async def noop(update, context):
    pass


def application(*commands, plugin_descriptions=None):
    handlers = {0: [CommandHandler(command, noop) for command in commands]}
    bot_data = {COMMAND_DESCRIPTIONS_KEY: plugin_descriptions} if plugin_descriptions else {}
    return SimpleNamespace(handlers=handlers, bot_data=bot_data, bot=SimpleNamespace(set_my_commands=AsyncMock()))


class HelpTests(unittest.IsolatedAsyncioTestCase):
    def test_lists_registered_commands_in_order_with_core_and_plugin_descriptions(self):
        app = application("start", "ayuda", "marcar", "congreso_dieta", "sin_texto",
                          plugin_descriptions={"congreso_dieta": "Congreso y dieta"})
        commands = registered_commands(app)
        self.assertEqual([name for name, _ in commands], ["start", "ayuda", "marcar", "congreso_dieta", "sin_texto"])
        described = dict(commands)
        self.assertIn("entrada", described["marcar"])
        self.assertEqual(described["congreso_dieta"], "Congreso y dieta")
        self.assertEqual(described["sin_texto"], "")

    async def test_ayuda_replies_with_every_command(self):
        app = application("marcar", "congreso_dieta", "sin_texto", plugin_descriptions={"congreso_dieta": "Congreso y dieta"})
        reply = AsyncMock()
        await show_help(SimpleNamespace(message=SimpleNamespace(reply_text=reply)), SimpleNamespace(application=app))
        text = reply.await_args.args[0]
        self.assertTrue(text.startswith("Comandos disponibles:"))
        self.assertIn("/congreso_dieta — Congreso y dieta", text)
        self.assertIn("\n/sin_texto", text)

    async def test_menu_is_published_with_a_description_for_every_command(self):
        app = application("marcar", "sin_texto")
        await publish_command_menu(app)
        menu = app.bot.set_my_commands.await_args.args[0]
        self.assertEqual([command.command for command in menu], ["marcar", "sin_texto"])
        self.assertTrue(all(isinstance(command, BotCommand) and command.description for command in menu))

    async def test_start_points_to_ayuda(self):
        reply = AsyncMock()
        await start(SimpleNamespace(message=SimpleNamespace(reply_text=reply)), None)
        self.assertIn("/ayuda", reply.await_args.args[0])
