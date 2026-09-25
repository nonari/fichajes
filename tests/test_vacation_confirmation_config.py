import json
import tempfile
import unittest
from pathlib import Path

from fichaxebot.config import load_config


class ConfirmationConfigTests(unittest.TestCase):
    def load(self, **values):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(dict(
                telegram_token='test', telegram_chat_id='123', usc_user='test', usc_pass='test',
                **values,
            )))
            return load_config(path)

    def test_defaults_preserve_direct_submission(self):
        config = self.load()
        self.assertFalse(config.vacation_confirmation_enabled)
        self.assertEqual(config.vacation_confirmation_timeout_seconds, 60)

    def test_custom_settings(self):
        config = self.load(vacation_confirmation_enabled=True, vacation_confirmation_timeout_seconds=120)
        self.assertTrue(config.vacation_confirmation_enabled)
        self.assertEqual(config.vacation_confirmation_timeout_seconds, 120)

    def test_rejects_invalid_settings(self):
        for value in ('true', 1, None):
            with self.subTest(enabled=value), self.assertRaises(ValueError):
                self.load(vacation_confirmation_enabled=value)
        for value in (0, -1, True, 1.5, '60', None):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                self.load(vacation_confirmation_timeout_seconds=value)
