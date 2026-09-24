import unittest
from datetime import date
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By

from fichaxebot.scrap_functions import vacation_request as requests
from fichaxebot.usc_api import UscWebSession


SELECTION = {
    "year": 2026, "vacationTypeId": "16", "vacationTypeName": "Vacacións",
    "days": ["2026-10-01"], "remainingDays": 10,
}
SUMMARY = {
    "year": "2026", "vacationTypeName": "Vacacións",
    "requestType": "Vacacións, permisos e licenzas",
    "periods": [["01/10/2026", "01/10/2026", "1"]],
    "state": "Borrador", "canSubmit": True,
}


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.form, self.next_button, self.submit_link = Mock(), Mock(), Mock()
        self.submit_link.get_attribute.return_value = (
            "https://fichaxe.usc.gal/pas/solicitude/123/resumo/solicitar"
        )
        self.session = SimpleNamespace(driver=Mock(), wait=Mock())
        self.session.driver.find_element.side_effect = lambda by, value: {
            (By.ID, "formularioSolicitude"): self.form,
            (By.ID, "seguinte"): self.next_button,
            (By.CSS_SELECTOR, 'a[href$="/resumo/solicitar"]'): self.submit_link,
        }[(by, value)]
        self.session.driver.find_elements.return_value = []
        # A transient summary cannot be reopened: any navigation is a bug.
        self.session.driver.get.side_effect = AssertionError("Do not reload the request")
        self.session._ensure_access_to = Mock(side_effect=AssertionError("Do not reopen the request"))

    def submit(self, summaries):
        with patch.object(requests, "_read_review", side_effect=summaries):
            return requests.submit_vacation_request(self.session, SELECTION)

    def test_submits_from_current_summary_without_reopening(self):
        result = self.submit([SUMMARY, {**SUMMARY, "state": "Solicitada", "canSubmit": False}])
        self.assertEqual(result, {**SELECTION, "id": "123", "state": "Solicitada"})
        self.next_button.click.assert_called_once()
        self.submit_link.click.assert_called_once()

    def test_mismatched_summary_never_submits(self):
        with self.assertRaises(requests.VacationRequestError):
            self.submit([{**SUMMARY, "year": "2025"}])
        self.submit_link.click.assert_not_called()

    def test_missing_submit_action_never_submits(self):
        with self.assertRaises(requests.VacationRequestError):
            self.submit([{**SUMMARY, "canSubmit": False}])
        self.submit_link.click.assert_not_called()

    def test_unknown_outcome_is_not_retried_or_reported_as_success(self):
        for updated in (SUMMARY, {**SUMMARY, "state": ""}, TimeoutException()):
            self.submit_link.reset_mock()
            with self.subTest(updated=updated), self.assertRaises(requests.VacationRequestUncertain):
                self.submit([SUMMARY, updated])
            self.submit_link.click.assert_called_once()

    def test_submit_click_failure_is_uncertain_and_is_not_retried(self):
        self.submit_link.click.side_effect = WebDriverException("connection lost")
        with self.assertRaises(requests.VacationRequestUncertain):
            self.submit([SUMMARY])
        self.submit_link.click.assert_called_once()

    def test_next_timeout_reports_form_error_without_submitting(self):
        self.session.wait.until.side_effect = TimeoutException()
        error = Mock(text="Fechas no válidas")
        self.session.driver.find_elements.return_value = [error]
        with self.assertRaisesRegex(requests.VacationRequestError, "Fechas no válidas"):
            self.submit([])
        self.submit_link.click.assert_not_called()


class SessionSubmissionTests(unittest.TestCase):
    def test_revalidation_through_final_submit_holds_one_lock(self):
        session = UscWebSession.__new__(UscWebSession)
        session.config = SimpleNamespace(read_only=False)
        session._lock = Lock()
        calls = []
        catalog = {"years": [{"year": 2026, "types": [
            {"id": "16", "name": "Vacacións", "remainingDays": 10},
        ]}]}
        result = {**SELECTION, "id": "123", "state": "Solicitada"}

        def step(name, value):
            def run(current, *args, **kwargs):
                self.assertIs(current, session)
                self.assertTrue(session._lock.locked(), name)
                calls.append(name)
                return value
            return run

        with patch('fichaxebot.usc_api.fetch_vacation_catalog', side_effect=step('balances', catalog)), \
             patch('fichaxebot.usc_api._fetch_calendar_summary', side_effect=step('calendar', [])), \
             patch('fichaxebot.usc_api.fill_vacation_request', side_effect=step('fill', None)), \
             patch('fichaxebot.usc_api._submit_vacation_request', side_effect=step('submit', result)), \
             patch('fichaxebot.usc_api.get_madrid_now') as now:
            now.return_value.date.return_value = date(2026, 9, 24)
            self.assertEqual(session.submit_vacation_request(SELECTION), result)
        self.assertEqual(calls, ['balances', 'calendar', 'fill', 'submit'])
        self.assertFalse(session._lock.locked())
