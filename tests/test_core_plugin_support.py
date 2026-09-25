import re
import unittest
from threading import RLock

from fichaxebot.usc_api import UscWebSession
from fichaxebot.webapp_controller.vacation_confirmation import CALLBACK_PATTERN


class SessionRunTests(unittest.TestCase):
    def test_run_passes_the_session_and_holds_the_browser_lock(self):
        session = UscWebSession.__new__(UscWebSession)
        session._lock = RLock()

        def operation(received, value):
            self.assertIs(received, session)
            self.assertTrue(session._lock._is_owned())
            return value * 2

        self.assertEqual(session.run(operation, 21), 42)


class ConfirmationPatternTests(unittest.TestCase):
    def test_congress_confirmations_share_the_confirmation_callback(self):
        self.assertTrue(re.fullmatch(CALLBACK_PATTERN, "congreso_confirm:" + "a" * 32))
        self.assertFalse(re.fullmatch(CALLBACK_PATTERN, "cdieta_absence_yes:" + "a" * 32))
