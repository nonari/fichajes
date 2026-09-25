import base64
from copy import deepcopy
from datetime import date
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock, get_ident
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import WebDriverException

from fichaxebot.scrap_functions import congress_request as congress
from fichaxebot.usc_api import UscWebSession


DATA = {
    'data_processing_authorized': True,
    'contact': {'email': 'person@example.test', 'phone': '123456789'},
    'address': {'country': 'España', 'province': 'Coruña, A', 'municipality': 'Santiago de Compostela',
                'postal_code': '15782', 'line1': 'Example street 1'},
    'reason': 'Attendance at a research congress', 'organization': 'Example University',
    'employment_category': 'PREDOUTORAIS', 'teaching_assigned': False,
    'start_date': '2026-10-10', 'end_date': '2026-10-12', 'supervisor_query': 'Example Supervisor',
}


class ValidationTests(unittest.TestCase):
    def validate(self, data):
        return congress.validate_request(data, date(2026, 9, 25))

    def test_valid_request_and_conditional_teaching_fields(self):
        result = self.validate(DATA)
        self.assertEqual(result['teaching_cover'], '-')
        self.assertEqual(result['attachments'], [])
        with self.assertRaises(congress.CongressRequestError):
            self.validate({**DATA, 'teaching_assigned': True})
        self.assertEqual(self.validate({**DATA, 'teaching_assigned': True,
                                       'teaching_cover': 'Covered by a colleague'})['teaching_cover'],
                         'Covered by a colleague')

    def test_invalid_required_fields_and_dates(self):
        for change in ({'data_processing_authorized': False}, {'reason': ''}, {'organization': ''},
                       {'employment_category': 'unknown'}, {'teaching_assigned': 'false'},
                       {'start_date': '2026-09-29'}, {'end_date': '2026-10-09'},
                       {'start_date': 'bad'}, {'supervisor_query': 'ab'}, {'address': {}},
                       {'contact': {'email': 123}}):
            with self.subTest(change=change), self.assertRaises(congress.CongressRequestError):
                self.validate({**DATA, **change})

    def test_single_line_fields_reject_keys_that_could_submit_or_change_focus(self):
        for change in ({'organization': 'Example\nUniversity'}, {'supervisor_query': 'Name\tSurname'},
                       {'contact': {'email': 'a@example.test\nb@example.test'}}):
            with self.subTest(change=change), self.assertRaises(congress.CongressRequestError):
                self.validate({**DATA, **change})
        self.assertEqual(self.validate({**DATA, 'reason': 'First paragraph\nSecond paragraph'})['reason'],
                         'First paragraph\nSecond paragraph')

    def test_attachments_require_real_nonempty_files_and_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'program.pdf'
            file.write_bytes(b'%PDF-1.4\nexample')
            attachment = {'title': 'Programme', 'path': str(file)}
            result = self.validate({**DATA, 'attachments': [attachment]})
            self.assertEqual(result['attachments'][0]['path'], str(file.resolve()))
            for attachments in ([attachment] * 11, [{'title': '', 'path': str(file)}],
                                [{'title': 'Missing', 'path': str(file) + '.missing'}]):
                with self.assertRaises(congress.CongressRequestError):
                    self.validate({**DATA, 'attachments': attachments})
            file.write_bytes(b'')
            with self.assertRaises(congress.CongressRequestError):
                self.validate({**DATA, 'attachments': [attachment]})


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.session = SimpleNamespace(driver=Mock(), wait=Mock(), config=SimpleNamespace(read_only=False))
        self.review = {'url': congress.REQUEST_URL + '?execution=current', 'execution': 'current',
                       'action': congress.REQUEST_URL + '?execution=current',
                       'pdf_url': congress.REQUEST_URL + '?execution=current&_eventId=obterSolicitudePdf'}
        self.button = Mock()

    def submit(self, confirm=None, after=None):
        with patch.object(congress, '_review_state', side_effect=[self.review, after or self.review]), \
             patch.object(congress, '_download_pdf', return_value=b'%PDF-test'), \
             patch.object(congress, '_next_button', return_value=self.button):
            return congress.submit_review(self.session, confirm)

    def test_direct_and_confirmed_submission_are_explicitly_unverified(self):
        for confirm in (None, Mock(return_value=True)):
            self.button.reset_mock()
            result = self.submit(confirm)
            self.assertEqual(result['status'], 'unverified')
            self.assertTrue(result['submission_attempted'])
            self.assertNotIn('id', result)
            self.button.click.assert_called_once()
            if confirm:
                confirm.assert_called_once_with(b'%PDF-test')

    def test_rejected_failed_or_changed_confirmation_never_submits(self):
        for confirm in (lambda pdf: False, Mock(side_effect=TimeoutError()), Mock(side_effect=RuntimeError())):
            with self.subTest(confirm=confirm), self.assertRaises(congress.CongressRequestCancelled):
                self.submit(confirm)
        with self.assertRaises(congress.CongressRequestError):
            self.submit(lambda pdf: True, {**self.review, 'execution': 'another'})
        self.button.click.assert_not_called()

    def test_failed_click_still_reports_unverified_and_never_retries(self):
        self.button.click.side_effect = WebDriverException('lost connection')
        result = self.submit()
        self.assertEqual(result['status'], 'unverified')
        self.assertTrue(result['submission_attempted'])
        self.button.click.assert_called_once()

    def test_pdf_errors_prevent_submission(self):
        with patch.object(congress, '_review_state', return_value=self.review), \
             patch.object(congress, '_download_pdf', side_effect=congress.CongressRequestError('bad PDF')):
            with self.assertRaises(congress.CongressRequestError):
                congress.submit_review(self.session, Mock(return_value=True))
        self.button.click.assert_not_called()

    def test_download_rejects_html_errors_and_other_origins(self):
        for response in ({'error': 'HTTP 500'}, {'data': base64.b64encode(b'<html>Login</html>').decode()},
                         {'data': ''}):
            self.session.driver.execute_async_script.return_value = response
            with self.assertRaises(congress.CongressRequestError):
                congress._download_pdf(self.session, self.review['pdf_url'])
        with self.assertRaises(congress.CongressRequestError):
            congress._download_pdf(self.session, 'https://example.test/document.pdf')


class SessionTests(unittest.TestCase):
    def test_scheduled_mark_waits_until_congress_confirmation_releases_browser(self):
        session = UscWebSession.__new__(UscWebSession)
        session.config = SimpleNamespace(read_only=False)
        session._lock = RLock()
        waiting, release, marked, mark_started = Event(), Event(), Event(), Event()
        thread_ids = []
        def confirm(pdf):
            thread_ids.append(get_ident())
            waiting.set()
            if not release.wait(2):
                raise AssertionError('not released')
            return False
        def submit(current, data, callback):
            thread_ids.append(get_ident())
            self.assertTrue(session._lock._is_owned())
            self.assertFalse(callback(b'%PDF-1.4'))
        def mark():
            mark_started.set()
            session.perform_check_in('salida')
        with patch('fichaxebot.usc_api._submit_congress_request', side_effect=submit), \
             patch('fichaxebot.usc_api._perform_check_in', side_effect=lambda *args: marked.set()), \
             ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(session.submit_congress_request, DATA, confirm)
            try:
                self.assertTrue(waiting.wait(1))
                scheduled = pool.submit(mark)
                self.assertTrue(mark_started.wait(1))
                self.assertFalse(marked.wait(0.05))
            finally:
                release.set()
            request.result(timeout=1)
            scheduled.result(timeout=1)
        self.assertTrue(marked.is_set())
        self.assertEqual(thread_ids[0], thread_ids[1])

    def test_one_lock_covers_preparation_pdf_and_decision(self):
        session = UscWebSession.__new__(UscWebSession)
        session.config = SimpleNamespace(read_only=False)
        session._lock = RLock()
        confirm = Mock()
        def submit(current, data, callback):
            self.assertIs(current, session)
            self.assertTrue(session._lock._is_owned())
            self.assertIs(callback, confirm)
            return {'status': 'unverified', 'submission_attempted': True}
        with patch('fichaxebot.usc_api._submit_congress_request', side_effect=submit):
            result = session.submit_congress_request(deepcopy(DATA), confirm)
        self.assertEqual(result['status'], 'unverified')
        self.assertFalse(session._lock._is_owned())
