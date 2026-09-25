import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from selenium.webdriver.common.by import By

from fichaxebot.scrap_functions import absence_request as absence
from fichaxebot.scrap_functions import congress_request as congress
from fichaxebot.scrap_functions import mark
from fichaxebot.scrap_functions import vacation_request as vacation
from fichaxebot.scrap_functions.commit import ReadOnlyStop, commit_click
from fichaxebot.usc_api import UscWebSession
from fichaxebot.webapp_controller import absences as absence_chat
from fichaxebot.webapp_controller import calendar_vacations

SUBMIT_URL = 'https://fichaxe.usc.gal/pas/solicitude/123/resumo/solicitar'
VACATION = {"year": 2026, "vacationTypeId": "16", "vacationTypeName": "Vacacións",
            "days": ["2026-10-01"], "remainingDays": 10}
VACATION_REVIEW = {"year": "2026", "vacationTypeName": "Vacacións",
                   "requestType": "Vacacións, permisos e licenzas",
                   "periods": [["01/10/2026", "01/10/2026", "1"]], "state": "Borrador", "canSubmit": True}
ABSENCE = {'year': 2026, 'absenceTypeId': '6', 'absenceTypeName': 'Traslado de domicilio',
           'requiresHours': False, 'periods': [{'date': '2026-01-12'}], 'observations': '', 'attachments': []}
ABSENCE_REVIEW = {'year': '2026', 'absenceTypeName': 'Traslado de domicilio',
                  'requestType': 'Ausencias autorizadas', 'observations': '', 'attachments': [],
                  'headers': ['Dende', 'Ata'], 'periods': [['12/01/2026', '12/01/2026']],
                  'state': 'Borrador', 'canSubmit': True}


def wizard_session(read_only):
    """A session whose request wizard is on the filled form, one click away from the review."""
    link = Mock()
    link.get_attribute.return_value = SUBMIT_URL
    session = SimpleNamespace(driver=Mock(), wait=Mock(), config=SimpleNamespace(read_only=read_only))
    session.driver.current_url = 'https://fichaxe.usc.gal/pas/solicitude/123/resumo'
    session.driver.find_elements.return_value = []
    elements = {(By.ID, 'formularioSolicitude'): Mock(), (By.ID, 'seguinte'): Mock(),
                (By.CSS_SELECTOR, 'a[href$="/resumo/solicitar"]'): link}
    session.driver.find_element.side_effect = lambda by, value: elements[(by, value)]
    return session, elements[(By.ID, 'seguinte')], link


class CommitGateTests(unittest.TestCase):
    def test_read_only_raises_instead_of_clicking(self):
        element = Mock()
        with self.assertRaises(ReadOnlyStop):
            commit_click(SimpleNamespace(config=SimpleNamespace(read_only=True)), element)
        element.click.assert_not_called()

    def test_writes_enabled_clicks_once(self):
        element = Mock()
        commit_click(SimpleNamespace(config=SimpleNamespace(read_only=False)), element)
        element.click.assert_called_once_with()

    def test_stop_is_a_permission_error_for_generic_callers(self):
        self.assertTrue(issubclass(ReadOnlyStop, PermissionError))


class ReadOnlyFlowTests(unittest.TestCase):
    def test_vacation_reaches_confirmation_but_not_final_submit(self):
        session, next_button, link = wizard_session(read_only=True)
        confirm = Mock(return_value=True)
        with patch.object(vacation, '_read_review', return_value=VACATION_REVIEW), \
             patch.object(vacation, '_capture_full_page', return_value=b'png'), \
             self.assertRaises(ReadOnlyStop):
            vacation.submit_vacation_request(session, VACATION, confirm=confirm)
        next_button.click.assert_called_once()
        confirm.assert_called_once_with(b'png')
        link.click.assert_not_called()

    def test_absence_reaches_confirmation_but_not_final_submit(self):
        session, next_button, link = wizard_session(read_only=True)
        confirm = Mock(return_value=True)
        with patch.object(absence, '_read_review', return_value=ABSENCE_REVIEW), \
             patch.object(absence, '_capture_full_page', return_value=b'png'), \
             self.assertRaises(ReadOnlyStop):
            absence.submit_absence_request(session, ABSENCE, confirm=confirm)
        next_button.click.assert_called_once()
        confirm.assert_called_once_with(b'png')
        link.click.assert_not_called()

    def test_congress_reaches_pdf_confirmation_but_not_final_submit(self):
        session = SimpleNamespace(driver=Mock(), wait=Mock(), config=SimpleNamespace(read_only=True))
        review = {'url': 'u', 'action': 'a', 'execution': 'e', 'pdf_url': 'p'}
        button, confirm = Mock(), Mock(return_value=True)
        with patch.object(congress, '_review_state', return_value=review), \
             patch.object(congress, '_download_pdf', return_value=b'%PDF'), \
             patch.object(congress, '_next_button', return_value=button), \
             self.assertRaises(ReadOnlyStop):
            congress.submit_review(session, confirm)
        confirm.assert_called_once_with(b'%PDF')
        button.click.assert_not_called()

    def test_check_in_validates_state_but_does_not_mark(self):
        session = SimpleNamespace(driver=Mock(), wait=Mock(), config=SimpleNamespace(read_only=True),
                                  _ensure_access_to=Mock())
        button = Mock()
        session.driver.find_element.return_value = button
        with patch.object(mark, '_get_last_row_cells', return_value=[]):
            result = mark.perform_check_in(session, 'entrada')
        self.assertFalse(result.success)
        self.assertTrue(result.dry_run)
        self.assertIn('solo lectura', result.message)
        session._ensure_access_to.assert_called_once()
        button.click.assert_not_called()

    def test_check_in_still_reports_invalid_action_in_read_only(self):
        session = SimpleNamespace(driver=Mock(), wait=Mock(), config=SimpleNamespace(read_only=True),
                                  _ensure_access_to=Mock())
        with patch.object(mark, '_get_last_row_cells', return_value=[]):
            result = mark.perform_check_in(session, 'salida')
        self.assertFalse(result.dry_run)
        self.assertIn('No hay una entrada pendiente', result.message)


class SessionEntryPointTests(unittest.TestCase):
    def test_read_only_no_longer_blocks_before_the_flow_starts(self):
        session = UscWebSession.__new__(UscWebSession)
        from threading import RLock
        session._lock = RLock()
        session.config = SimpleNamespace(read_only=True, vacation_confirmation_enabled=False)
        with patch('fichaxebot.usc_api._perform_check_in', return_value='marked') as check_in, \
             patch('fichaxebot.usc_api._submit_congress_request', return_value='congress') as congress_submit:
            self.assertEqual(session.perform_check_in('entrada'), 'marked')
            self.assertEqual(session.submit_congress_request({}), 'congress')
        check_in.assert_called_once()
        congress_submit.assert_called_once()


class ChatReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_vacation_dry_run_is_reported_as_not_sent(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        session = SimpleNamespace(submit_vacation_request=Mock(side_effect=ReadOnlyStop()))
        await calendar_vacations._submit_and_report(status, session, VACATION)
        text = status.edit_text.await_args.args[0]
        self.assertIn('solo lectura', text)
        self.assertIn('no se envió', text)
        self.assertNotIn('❌', text)

    async def test_absence_dry_run_is_reported_as_not_sent(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        pending = SimpleNamespace(
            origin_message=SimpleNamespace(reply_text=AsyncMock(return_value=status)),
            application=SimpleNamespace(web_session=SimpleNamespace(
                submit_absence_request=Mock(side_effect=ReadOnlyStop()))),
            selection=ABSENCE, abort=Mock(), finish=AsyncMock())
        with patch.object(absence_chat, 'get_config',
                          return_value=SimpleNamespace(absence_confirmation_enabled=False)):
            await absence_chat._submit_and_report(pending)
        text = status.edit_text.await_args.args[0]
        self.assertIn('solo lectura', text)
        self.assertIn('no se envió', text)
        self.assertNotIn('volver a intentarlo', text)
