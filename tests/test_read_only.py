import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fichaxebot.usc_api import UscWebSession


class ReadOnlyTests(unittest.TestCase):
    def test_all_write_entry_points_reject_before_browser_access(self):
        session = UscWebSession.__new__(UscWebSession)
        session.config = SimpleNamespace(read_only=True)
        session.driver = Mock()
        for method, argument in [
            (session.perform_check_in, "entrada"),
            (session.perform_check_in, "salida"),
            (session.submit_vacation_request, {}),
            (session.submit_congress_request, {}),
        ]:
            with self.subTest(method=method.__name__, argument=argument):
                with self.assertRaises(PermissionError):
                    method(argument)
        self.assertEqual(session.driver.mock_calls, [])
