import json
import tempfile
import unittest
from datetime import time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler

import plugins.marcaje as plugin_module
from fichaxebot.config import load_config
from fichaxebot.plugins import COMMAND_DESCRIPTIONS_KEY, register_plugins
from fichaxebot.scheduler import TaskScheduler
from fichaxebot.scrap_functions.mark import CheckInResult
from fichaxebot.utils import MADRID_TZ
from plugins.marcaje import marks, question
from plugins.marcaje.config import MarcajeConfig, parse_config
from tests.scheduler_fakes import Clock, fake_app, fire
from tests.test_marcaje_marks import NOW, started_app


class ConfigTests(unittest.TestCase):
    def test_defaults_match_the_former_core_settings(self):
        self.assertEqual(parse_config({}), MarcajeConfig(time(9, 0), timedelta(minutes=420), 3, 3, timedelta(minutes=5)))

    def test_values_are_read_and_zero_delay_disables_auto_checkout(self):
        config = parse_config({"daily_question_time": "08:15", "auto_checkout_delay_minutes": 0,
                               "auto_checkout_random_offset_minutes": 0, "max_reminders": 0,
                               "reminder_interval_minutes": 10})
        self.assertEqual(config, MarcajeConfig(time(8, 15), None, 0, 0, timedelta(minutes=10)))

    def test_invalid_values_are_rejected(self):
        for raw in ({"daily_question_time": "25:00"}, {"daily_question_time": 9},
                    {"auto_checkout_delay_minutes": -1}, {"auto_checkout_delay_minutes": "420"},
                    {"auto_checkout_random_offset_minutes": -1}, {"max_reminders": True},
                    {"reminder_interval_minutes": 0}):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "plugin_config.marcaje"):
                parse_config(raw)

    def test_core_config_rejects_the_former_top_level_settings(self):
        data = {"telegram_token": "123456:TEST_TOKEN", "telegram_chat_id": "123", "usc_user": "test",
                "usc_pass": "test", "max_reminders": 3}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_reminders.*plugin_config.marcaje"):
                load_config(path)


class PluginWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / ".schedule.data"
        self.clock = Clock(NOW)
        self.app = ApplicationBuilder().token("123456:TEST_TOKEN").build()
        self.app.scheduler = TaskScheduler("123", path=self.path, clock=self.clock)

    def configure(self, plugin_config):
        patcher = patch.object(plugin_module, "get_config",
                               return_value=SimpleNamespace(plugin_config=plugin_config))
        patcher.start()
        self.addCleanup(patcher.stop)

    def handlers(self):
        return [handler for handlers in self.app.handlers.values() for handler in handlers]

    async def test_setup_registers_commands_answers_mark_kind_and_daily_question(self):
        self.configure({"marcaje": {"daily_question_time": "08:30"}})
        self.assertEqual(register_plugins(self.app, ["marcaje"]), [])
        commands = {command for handler in self.handlers() if isinstance(handler, CommandHandler)
                    for command in handler.commands}
        self.assertEqual(commands, {"marcar", "pendientes", "cancelar"})
        self.assertEqual(set(self.app.bot_data[COMMAND_DESCRIPTIONS_KEY]), commands)
        self.assertIn(question.process_response,
                      [handler.callback for handler in self.handlers() if isinstance(handler, MessageHandler)])

        started = fake_app()
        self.app.scheduler.start(started)
        [daily] = started.job_queue.of("daily")
        self.assertEqual((daily.name, daily.timing["time"], daily.timing["days"]),
                         ("daily_question", time(8, 30, tzinfo=MADRID_TZ), (1, 2, 3, 4, 5)))
        task = self.app.scheduler.schedule(marks.KIND, NOW + timedelta(hours=1), {"action": "entrada"})
        self.assertEqual(self.app.scheduler.pending(), [task])

    async def test_section_is_optional(self):
        self.configure({})
        self.assertEqual(register_plugins(self.app, ["marcaje"]), [])
        self.assertEqual(self.app.bot_data["marcaje"], parse_config({}))

    async def test_invalid_config_skips_the_plugin_before_scheduling(self):
        self.configure({"marcaje": {"max_reminders": -1}})
        [failure] = register_plugins(self.app, ["marcaje"])
        self.assertEqual(failure.name, "marcaje")
        self.assertIn("plugin_config.marcaje", failure.message)
        self.assertEqual(self.handlers(), [])
        started = fake_app()
        self.app.scheduler.start(started)
        self.assertEqual(started.job_queue.of("daily"), [])

    async def test_marks_saved_before_the_plugin_existed_are_restored(self):
        self.clock.now = NOW.replace(hour=10)
        when = NOW + timedelta(hours=8)
        self.path.write_text(json.dumps({
            "version": 2,
            "tasks": [{"id": "old", "kind": "mark", "when": when.isoformat(),
                       "payload": {"action": "salida"}, "state": "pending"}],
            "recurring": {"daily_question": "2026-10-01"},
        }), encoding="utf-8")
        self.configure({})
        register_plugins(self.app, ["marcaje"])
        started = fake_app()
        report = self.app.scheduler.start(started)
        self.assertEqual([task.id for task in report.restored], ["old"])
        self.assertEqual([job.name for job in started.job_queue.of("once")], ["task_old"])


class QuestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app, self.scheduler = started_app(self)
        for target, value in (("plugins.marcaje.question.get_config", SimpleNamespace(telegram_chat_id="123")),
                              ("plugins.marcaje.question.get_madrid_now", NOW)):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = SimpleNamespace(application=self.app, bot=self.app.bot, job_queue=self.app.job_queue)
        self.reply = AsyncMock()

    def answer(self, text):
        update = SimpleNamespace(message=SimpleNamespace(text=text, reply_text=self.reply))
        return question.process_response(update, SimpleNamespace(application=self.app))

    async def test_question_is_skipped_while_marks_are_pending(self):
        marks.schedule(self.app, "entrada", NOW + timedelta(hours=1))
        await question.ask_for_check_in(self.context)
        self.app.bot.send_message.assert_not_awaited()

    async def test_reminders_stop_after_the_configured_maximum(self):
        await question.ask_for_check_in(self.context)
        self.assertEqual(self.app.bot.send_message.await_args.kwargs["text"], "📅 Buenos días! ¿Quieres fichar hoy?")
        [reminder] = self.app.job_queue.of("repeating")
        self.assertEqual(reminder.timing["interval"], 300)
        for _ in range(4):
            await fire(self.app, reminder)
        texts = [call.kwargs["text"] for call in self.app.bot.send_message.await_args_list]
        self.assertEqual(sum(text.startswith("⏰ Recordatorio") for text in texts), 3)
        self.assertTrue(reminder.removed)
        self.assertFalse(self.app.bot_data[question.AWAITING_RESPONSE_KEY])

    async def test_yes_clocks_in_and_schedules_checkout(self):
        await question.ask_for_check_in(self.context)
        result = CheckInResult(True, "entrada", "✅ ok")
        with patch("plugins.marcaje.question.execute_check_in_async", AsyncMock(return_value=result)):
            await self.answer("Sí")
        replies = [call.args[0] for call in self.reply.await_args_list]
        self.assertEqual(replies[:2], ["🔄 Intentando fichaje de entrada...", "✅ ok"])
        self.assertIn("Salida programada", replies[2])
        self.assertEqual([task.payload["action"] for task in marks.pending(self.app)], ["salida"])
        self.assertFalse(self.app.bot_data[question.AWAITING_RESPONSE_KEY])
        self.assertEqual(self.app.job_queue.of("repeating"), [])

    async def test_answer_without_a_question_is_ignored(self):
        await self.answer("si")
        self.reply.assert_not_awaited()
