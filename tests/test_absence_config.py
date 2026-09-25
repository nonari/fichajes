import json
from pathlib import Path
import tempfile
import unittest

from fichaxebot.config import load_config


class AbsenceConfigTests(unittest.TestCase):
    def config(self, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'telegram_token':'test','telegram_chat_id':'123',
                'usc_user':'test','usc_pass':'test',**kwargs}))
            return load_config(path)

    def test_absence_confirmation_defaults_on_without_changing_vacations(self):
        config = self.config()
        self.assertTrue(config.absence_confirmation_enabled)
        self.assertFalse(config.vacation_confirmation_enabled)
        self.assertEqual(config.absences_webapp_url, '')
        self.assertEqual(config.absence_confirmation_timeout_seconds, 60)

    def test_explicit_direct_mode_and_custom_url_timeout(self):
        config = self.config(absence_confirmation_enabled=False, absence_confirmation_timeout_seconds=120,
                             absences_webapp_url='https://example.test/ausencias.html')
        self.assertFalse(config.absence_confirmation_enabled)
        self.assertEqual(config.absence_confirmation_timeout_seconds, 120)
        self.assertEqual(config.absences_webapp_url,'https://example.test/ausencias.html')

    def test_invalid_flags_and_deadlines_rejected(self):
        for value in (None, 0, 'true'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config(absence_confirmation_enabled=value)
        for value in (None, True, 0, -1, '60', 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config(absence_confirmation_timeout_seconds=value)
