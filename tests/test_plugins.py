import importlib
import json
import sys
import tempfile
import textwrap
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from telegram import Chat, Message, MessageEntity, Update, User
from telegram.ext import ApplicationBuilder, CommandHandler

from fichaxebot.access_control import restrict_to_chat
from fichaxebot.config import load_config
from fichaxebot.plugins import COMMAND_DESCRIPTIONS_KEY, PluginFailure, plugin_warning, register_plugins
from fichaxebot.webapp_controller import router


class PluginConfigTests(unittest.TestCase):
    def load(self, **overrides):
        data = {
            "telegram_token": "123456:TEST_TOKEN",
            "telegram_chat_id": "123",
            "usc_user": "test",
            "usc_pass": "test",
            **overrides,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_config(path)

    def test_plugin_config_defaults_to_empty_and_requires_objects(self):
        self.assertEqual(self.load().plugin_config, {})
        self.assertEqual(self.load(plugin_config={"x": {"a": 1}}).plugin_config, {"x": {"a": 1}})
        for value in ([], "x", {"x": 1}, {"x": []}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "plugin_config"):
                self.load(plugin_config=value)

    def test_plugins_default_to_disabled(self):
        self.assertEqual(self.load().plugins, [])
        self.assertEqual(self.load(plugins=[]).plugins, [])

    def test_enabled_plugins_preserve_configuration_order(self):
        self.assertEqual(self.load(plugins=["second", "first"]).plugins, ["second", "first"])

    def test_plugins_must_be_a_list_of_unique_package_names(self):
        for value in (
            None, "plugin1", {}, 1, [None], [1], [""], ["../outside"],
            ["nested.plugin"], ["two-words"], ["plugin1", "plugin1"], ["class"],
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "plugins"):
                self.load(plugins=value)


class PluginLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.plugin_dir = Path(directory.name)
        package = types.ModuleType("plugins")
        package.__path__ = [directory.name]
        modules = patch.dict(sys.modules, {"plugins": package})
        modules.start()
        self.addCleanup(modules.stop)

        self.app = ApplicationBuilder().token("123456:TEST_TOKEN").build()
        self.app._initialized = True
        self.app.bot._bot_user = User(456, "Test", True, username="test_bot")
        self.app.web_session = object()
        self.app.scheduler = object()
        restrict_to_chat(self.app, "123")
        self.errors = []

        async def capture_error(update, context):
            self.errors.append(context.error)

        self.app.add_error_handler(capture_error)

    async def asyncTearDown(self):
        self.assertEqual(self.errors, [])

    def load_failing(self, names, failing):
        """Register plugins expecting exactly one failure, for `failing`; return it."""
        failures = register_plugins(self.app, names)
        self.assertEqual([failure.name for failure in failures], [failing])
        return failures[0]

    def plugin(self, name, source):
        directory = self.plugin_dir / name
        directory.mkdir()
        (directory / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
        importlib.invalidate_caches()
        return directory

    def command_plugin(self, name, commands):
        self.plugin(name, f"""
            async def run(update, context):
                context.bot_data.setdefault("calls", []).append(({name!r}, context.args))
                context.bot_data["services"] = (
                    context.application.web_session, context.application.scheduler,
                )
            COMMANDS = {{command: run for command in {commands!r}}}
        """)

    async def dispatch(self, text, chat_id=123):
        command = text.split()[0]
        message = Message(
            1, datetime.now(timezone.utc), Chat(chat_id, "private"),
            from_user=User(789, "User", False), text=text,
            entities=[MessageEntity(MessageEntity.BOT_COMMAND, 0, len(command))],
        )
        message.set_bot(self.app.bot)
        await self.app.process_update(Update(1, message=message))

    async def test_setup_hook_runs_after_commands_are_registered(self):
        self.plugin("feature", """
            async def run(update, context):
                pass
            COMMANDS = {"hello": run}
            def setup(application):
                application.bot_data["setup"] = [
                    handler.commands for handlers in application.handlers.values()
                    for handler in handlers if hasattr(handler, "commands")
                ]
        """)
        register_plugins(self.app, ["feature"])
        self.assertEqual(self.app.bot_data["setup"], [frozenset({"hello"})])

    async def test_setup_must_be_callable_and_its_errors_are_reported(self):
        self.plugin("bad_setup", "COMMANDS = {}\nsetup = 1\n")
        self.assertIn("setup", self.load_failing(["bad_setup"], "bad_setup").message)
        self.plugin("failing_setup", 'COMMANDS = {}\ndef setup(application):\n    raise RuntimeError("no config")\n')
        failure = self.load_failing(["failing_setup"], "failing_setup")
        self.assertIn("no config", failure.message)
        self.assertIsInstance(failure.error, RuntimeError)

    async def test_failed_setup_rolls_back_the_plugin_and_others_still_load(self):
        self.plugin("broken", """
            async def run(update, context):
                context.bot_data["broken"] = True
            async def handle(update, context, data):
                pass
            COMMANDS = {"broken": run}
            COMMAND_DESCRIPTIONS = {"broken": "Roto"}
            WEBAPP_CONTROLLERS = {"broken_submit": handle}
            def setup(application):
                from telegram.ext import CallbackQueryHandler
                application.add_handler(CallbackQueryHandler(run, pattern="^broken$"))
                raise ValueError("bad config")
        """)
        self.command_plugin("fine", ["hello"])
        with patch.dict(router.WEBAPP_CONTROLLERS):
            failure = self.load_failing(["broken", "fine"], "broken")
            self.assertNotIn("broken_submit", router.WEBAPP_CONTROLLERS)
        self.assertIn("bad config", failure.message)
        self.assertEqual([type(handler).__name__ for handler in self.app.handlers[0]], ["CommandHandler"])
        self.assertNotIn("broken", self.app.bot_data.get(COMMAND_DESCRIPTIONS_KEY, {}))
        await self.dispatch("/broken")
        await self.dispatch("/hello")
        self.assertNotIn("broken", self.app.bot_data)
        self.assertEqual(self.app.bot_data["calls"], [("fine", [])])

    async def test_command_descriptions_are_stored_for_help(self):
        self.plugin("feature", """
            async def run(update, context):
                pass
            COMMANDS = {"Hello": run}
            COMMAND_DESCRIPTIONS = {"Hello": "Saluda"}
        """)
        register_plugins(self.app, ["feature"])
        self.assertEqual(self.app.bot_data[COMMAND_DESCRIPTIONS_KEY], {"hello": "Saluda"})

    async def test_invalid_command_descriptions_are_rejected(self):
        for index, descriptions in enumerate(({"other": "x"}, {"hello": ""}, {"hello": 1}, [])):
            name = f"bad_descriptions_{index}"
            self.plugin(name, f"async def run(u, c):\n    pass\nCOMMANDS = {{'hello': run}}\n"
                              f"COMMAND_DESCRIPTIONS = {descriptions!r}\n")
            with self.subTest(descriptions=descriptions):
                self.load_failing([name], name)

    async def test_webapp_controllers_are_merged_into_the_router(self):
        self.plugin("feature", """
            async def handle(update, context, data):
                pass
            COMMANDS = {}
            WEBAPP_CONTROLLERS = {"feature_submit": handle}
        """)
        with patch.dict(router.WEBAPP_CONTROLLERS):
            register_plugins(self.app, ["feature"])
            self.assertIn("feature_submit", router.WEBAPP_CONTROLLERS)
        self.assertNotIn("feature_submit", router.WEBAPP_CONTROLLERS)

    async def test_invalid_or_colliding_webapp_controllers_are_rejected(self):
        for index, source in enumerate((
            "COMMANDS = {}\nWEBAPP_CONTROLLERS = []\n",
            "async def h(u, c, d):\n    pass\nCOMMANDS = {}\nWEBAPP_CONTROLLERS = {'Bad Type': h}\n",
            "def h(u, c, d):\n    pass\nCOMMANDS = {}\nWEBAPP_CONTROLLERS = {'feature_submit': h}\n",
            "async def h(u, c, d):\n    pass\nCOMMANDS = {}\nWEBAPP_CONTROLLERS = {'absence_request_submit': h}\n",
        )):
            name = f"bad_controllers_{index}"
            self.plugin(name, source)
            with self.subTest(source=source), patch.dict(router.WEBAPP_CONTROLLERS):
                self.load_failing([name], name)

    async def test_multiple_commands_dispatch_with_arguments_and_services(self):
        self.command_plugin("feature", ["hello", "echo"])
        register_plugins(self.app, ["feature"])

        await self.dispatch("/hello")
        await self.dispatch("/echo@test_bot one two")

        self.assertEqual(self.app.bot_data["calls"], [("feature", []), ("feature", ["one", "two"])])
        self.assertEqual(self.app.bot_data["services"], (self.app.web_session, self.app.scheduler))

    async def test_other_chats_cannot_invoke_plugins(self):
        self.command_plugin("feature", ["hello"])
        register_plugins(self.app, ["feature"])
        await self.dispatch("/hello", chat_id=999)
        self.assertNotIn("calls", self.app.bot_data)

    async def test_disabled_plugins_are_not_imported_or_registered(self):
        self.plugin("disabled", 'raise RuntimeError("must not import")')
        register_plugins(self.app, [])
        self.assertNotIn("plugins.disabled", sys.modules)
        self.assertNotIn(0, self.app.handlers)

        self.command_plugin("enabled", ["hello"])
        register_plugins(self.app, ["enabled"])
        await self.dispatch("/hello")
        self.assertEqual(self.app.bot_data["calls"], [("enabled", [])])
        self.assertNotIn("plugins.disabled", sys.modules)

    async def test_imports_enabled_plugins_in_configuration_order(self):
        self.plugin("first", """
            import plugins
            plugins.load_order = ["first"]
            COMMANDS = {}
        """)
        self.plugin("second", """
            import plugins
            plugins.load_order.append("second")
            COMMANDS = {}
        """)
        register_plugins(self.app, ["first", "second"])
        self.assertEqual(sys.modules["plugins"].load_order, ["first", "second"])

    async def test_plugin_can_import_its_supporting_modules(self):
        directory = self.plugin("feature", "from .commands import COMMANDS")
        (directory / "commands.py").write_text(
            'async def run(update, context):\n    context.bot_data["result"] = "ok"\n'
            'COMMANDS = {"hello": run}\n', encoding="utf-8",
        )
        register_plugins(self.app, ["feature"])
        await self.dispatch("/hello")
        self.assertEqual(self.app.bot_data["result"], "ok")

    async def test_builtin_command_collision_is_rejected_case_insensitively(self):
        async def builtin(update, context):
            context.bot_data["builtin"] = True

        self.app.add_handler(CommandHandler("start", builtin))
        self.command_plugin("feature", ["hello", "START"])
        self.assertIn("start", self.load_failing(["feature"], "feature").message)
        await self.dispatch("/start")
        await self.dispatch("/hello")
        self.assertTrue(self.app.bot_data["builtin"])
        self.assertNotIn("calls", self.app.bot_data)

    async def test_colliding_plugin_is_skipped_and_the_first_one_kept(self):
        self.command_plugin("first", ["hello"])
        self.command_plugin("second", ["HELLO", "other"])
        self.assertIn("hello", self.load_failing(["first", "second"], "second").message)
        await self.dispatch("/hello")
        await self.dispatch("/other")
        self.assertEqual(self.app.bot_data["calls"], [("first", [])])

    async def test_case_insensitive_collision_within_one_plugin(self):
        self.command_plugin("feature", ["hello", "HELLO"])
        self.assertIn("hello", self.load_failing(["feature"], "feature").message)

    async def test_invalid_command_names_are_rejected(self):
        for index, name in enumerate(("", "/hello", "two words", "ñ", "x" * 33, "hello\n", 1, ("a", "b"))):
            plugin_name = f"invalid_{index}"
            self.command_plugin(plugin_name, [name])
            with self.subTest(name=name):
                self.load_failing([plugin_name], plugin_name)

    async def test_malformed_command_exports_are_rejected(self):
        for index, source in enumerate((
            "", "COMMANDS = []", "COMMANDS = None",
            'COMMANDS = {"hello": 1}',
            'def run(update, context): pass\nCOMMANDS = {"hello": run}',
        )):
            name = f"malformed_{index}"
            self.plugin(name, source)
            with self.subTest(source=source):
                self.load_failing([name], name)

    async def test_import_errors_are_reported_with_their_cause(self):
        self.plugin("broken", 'raise RuntimeError("plugin failed to import")')
        for name, cause in (("missing", ModuleNotFoundError), ("broken", RuntimeError)):
            with self.subTest(name=name):
                self.assertIsInstance(self.load_failing([name], name).error, cause)

    async def test_plugin_must_be_a_package(self):
        (self.plugin_dir / "loose.py").write_text("COMMANDS = {}", encoding="utf-8")
        self.assertIn("package", self.load_failing(["loose"], "loose").message)

    def test_warning_names_each_failed_plugin(self):
        self.assertIsNone(plugin_warning([]))
        text = plugin_warning([PluginFailure("congreso_dieta", "falta line1", ValueError())])
        self.assertIn("congreso_dieta: falta line1", text)
        self.assertIn("config.json", text)

    async def test_startup_continues_past_a_broken_plugin(self):
        config = PluginConfigTests().load(plugins=["missing"])
        with patch("fichaxebot.config.get_config", return_value=config):
            bot = importlib.import_module("fichaxebot.bot")

        class ReachedBrowser(Exception):
            pass

        with patch.object(bot, "get_config", return_value=config), \
             patch.object(bot, "UscWebSession", side_effect=ReachedBrowser):
            with self.assertRaises(ReachedBrowser):
                await bot._run_bot()

