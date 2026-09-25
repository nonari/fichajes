import json
from pathlib import Path
import tempfile
import unittest

from fichaxebot.config import load_config


class CongressConfirmationConfigTests(unittest.TestCase):
    def config(self, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'telegram_token': 'test', 'telegram_chat_id': '123',
                                        'usc_user': 'test', 'usc_pass': 'test', **kwargs}))
            return load_config(path)

    def test_congress_confirmation_defaults_on(self):
        config = self.config()
        self.assertTrue(config.congress_confirmation_enabled)
        self.assertEqual(config.congress_confirmation_timeout_seconds, 60)

    def test_explicit_direct_mode_and_custom_timeout(self):
        config = self.config(congress_confirmation_enabled=False, congress_confirmation_timeout_seconds=120)
        self.assertFalse(config.congress_confirmation_enabled)
        self.assertEqual(config.congress_confirmation_timeout_seconds, 120)

    def test_invalid_flags_and_deadlines_rejected(self):
        for value in (None, 0, 'true'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'congress_confirmation_enabled'):
                self.config(congress_confirmation_enabled=value)
        for value in (None, True, 0, -1, '60', 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'congress_confirmation_timeout_seconds'):
                self.config(congress_confirmation_timeout_seconds=value)
