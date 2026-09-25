import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from telegram.ext import ApplicationBuilder, CommandHandler

import plugins.congreso_dieta as plugin_module
from fichaxebot.plugins import register_plugins
from fichaxebot.scheduler import TaskScheduler
from fichaxebot.utils import MADRID_TZ
from fichaxebot.webapp_controller import router
from plugins.congreso_dieta import flow
from plugins.congreso_dieta.cases import CaseStore
from tests.scheduler_fakes import Clock, fake_app
from tests.test_congreso_config import raw_config


class PluginWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.app = ApplicationBuilder().token("123456:TEST_TOKEN").build()
        self.app.scheduler = TaskScheduler("123", path=self.root / "schedule.json",
                                           clock=Clock(datetime(2026, 10, 1, 8, 0, tzinfo=MADRID_TZ)))
        store = patch.object(plugin_module, "CaseStore",
                             lambda: CaseStore(self.root / "cases.json", self.root / "files"))
        store.start()
        self.addCleanup(store.stop)

    def configure(self, section):
        patcher = patch.object(plugin_module, "get_config",
                               return_value=SimpleNamespace(plugin_config={"congreso_dieta": section}))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_setup_registers_command_webapp_types_task_kind_and_daily_job(self):
        self.configure(raw_config(str(self.root)))
        with patch.dict(router.WEBAPP_CONTROLLERS):
            register_plugins(self.app, ["congreso_dieta"])
            self.assertTrue({"congreso_dieta_new", "congreso_dieta_cancel"} <= set(router.WEBAPP_CONTROLLERS))
        commands = {command for handlers in self.app.handlers.values() for handler in handlers
                    if isinstance(handler, CommandHandler) for command in handler.commands}
        self.assertIn("congreso_dieta", commands)
        self.assertIsInstance(self.app.bot_data[plugin_module.PLUGIN_KEY], flow.CongresoDieta)
        started = fake_app()
        self.app.scheduler.start(started)
        self.assertEqual([job.name for job in started.job_queue.of("daily")], [flow.DAILY_JOB])

    async def test_invalid_config_stops_startup(self):
        self.configure({})
        with patch.dict(router.WEBAPP_CONTROLLERS), self.assertRaisesRegex(ValueError, "congreso_dieta"):
            register_plugins(self.app, ["congreso_dieta"])
