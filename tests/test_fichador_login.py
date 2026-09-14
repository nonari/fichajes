import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from fichaxebot.fichador import FichaxeAccessError, _login, get_today_records


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.driver = Mock()
        self.driver.find_element.return_value.is_displayed.return_value = True
        self.driver.find_elements.return_value = []
        self.wait = WebDriverWait(self.driver, 0.01, poll_frequency=0.001)

    def test_rejected_credentials_report_actionable_error_immediately(self):
        alert = Mock()
        alert.text = "Username or password are not correct."
        alert.is_displayed.return_value = True
        self.driver.find_elements.side_effect = lambda by, value: (
            [alert] if value == "#alert-container.is-error" else []
        )

        with self.assertRaisesRegex(FichaxeAccessError, "USC ha rechazado.*usc_pass"):
            _login(self.driver, self.wait, "user", "secret")

        self.driver.find_elements.assert_called_once_with(
            By.CSS_SELECTOR, "#alert-container.is-error"
        )

    def test_records_are_accessible_without_mark_button(self):
        table = Mock()
        self.driver.find_elements.side_effect = lambda by, value: (
            [table] if value == "taboaMarcaxesPropios" else []
        )

        _login(self.driver, self.wait, "user", "secret")

        self.assertFalse(any(
            "novaMarcaxe" in call.args
            for call in self.driver.find_elements.call_args_list
        ))

    def test_hidden_or_empty_alert_does_not_reject_login(self):
        for displayed, text in ((False, "Old error"), (True, " ")):
            with self.subTest(displayed=displayed, text=text):
                alert = Mock()
                alert.is_displayed.return_value = displayed
                alert.text = text
                self.driver.find_elements.side_effect = lambda by, value: (
                    [alert] if value == "#alert-container.is-error" else [Mock()]
                )
                _login(self.driver, self.wait, "user", "secret")

    def test_missing_login_form_has_clear_error_and_preserves_cause(self):
        self.driver.find_element.side_effect = NoSuchElementException()

        with self.assertRaisesRegex(FichaxeAccessError, "página de acceso") as caught:
            _login(self.driver, self.wait, "user", "secret")

        self.assertIsInstance(caught.exception.__cause__, TimeoutException)
        self.assertNotIn("Stacktrace", str(caught.exception))

    def test_login_without_records_has_clear_error(self):
        with self.assertRaisesRegex(FichaxeAccessError, "tras el inicio de sesión"):
            _login(self.driver, self.wait, "user", "secret")

    @patch("fichaxebot.fichador.get_config")
    @patch("fichaxebot.fichador._create_driver")
    @patch("fichaxebot.fichador._login", side_effect=FichaxeAccessError("Acceso rechazado"))
    def test_records_query_closes_browser_on_login_failure(self, login, create_driver, config):
        config.return_value = SimpleNamespace(usc_user="user", usc_pass="secret")
        create_driver.return_value = self.driver

        with self.assertRaisesRegex(FichaxeAccessError, "Acceso rechazado"):
            get_today_records()

        self.driver.quit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
