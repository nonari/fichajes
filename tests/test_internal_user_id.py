import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from fichaxebot.usc_api import UscWebSession
from fichaxebot.scrap_functions.vacation_request import REQUEST_URL


class InternalUserIdTests(unittest.TestCase):
    def make_session(self, value):
        session = UscWebSession.__new__(UscWebSession)
        field = Mock()
        field.get_attribute.return_value = value
        session.driver = Mock()
        session.driver.find_elements.return_value = [field]
        session.wait = WebDriverWait(session.driver, 0)
        session._ensure_access_to = Mock()
        return session, field

    def test_reads_applicant_without_editing_or_submitting_form(self):
        session, field = self.make_session(' 12345 ')
        self.assertEqual(session._discover_internal_user_id(), '12345')
        session._ensure_access_to.assert_called_once_with(REQUEST_URL)
        field.get_attribute.assert_called_once_with('value')
        field.click.assert_not_called()
        field.send_keys.assert_not_called()
        session.driver.execute_script.assert_not_called()

    def test_missing_or_invalid_ids_fail(self):
        for value in (None, '', 'name', '0', '-1', '123/other'):
            with self.subTest(value=value):
                session, _ = self.make_session(value)
                with self.assertRaises(TimeoutException):
                    session._discover_internal_user_id()

    def test_startup_caches_discovered_id(self):
        with patch.object(UscWebSession, '_create_driver'), \
             patch.object(UscWebSession, '_discover_internal_user_id', return_value='12345') as discover, \
             patch('fichaxebot.usc_api.get_config', return_value=SimpleNamespace()):
            session = UscWebSession()
            self.assertEqual(session.internal_user_id, '12345')
            discover.assert_called_once_with()

    def test_startup_closes_browser_if_discovery_fails(self):
        with patch.object(UscWebSession, '_create_driver') as create, \
             patch.object(UscWebSession, '_discover_internal_user_id', side_effect=TimeoutException), \
             patch('fichaxebot.usc_api.get_config', return_value=SimpleNamespace()):
            with self.assertRaises(TimeoutException):
                UscWebSession()
            create.return_value.quit.assert_called_once_with()
