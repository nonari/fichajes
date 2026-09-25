import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

from fichaxebot.scrap_functions import absence_request as absence

SELECTION = {'year':2026,'absenceTypeId':'6','absenceTypeName':'Traslado de domicilio',
             'requiresHours':False,'periods':[{'date':'2026-01-12'}], 'observations':'','attachments':[]}
REVIEW = {'year':'2026','absenceTypeName':'Traslado de domicilio','requestType':'Ausencias autorizadas',
          'observations':'','attachments':[],'headers':['Dende','Ata'],
          'periods':[['12/01/2026','12/01/2026']], 'state':'Borrador','canSubmit':True}


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.form, self.next, self.link = Mock(), Mock(), Mock()
        self.session = SimpleNamespace(driver=Mock(), wait=Mock(), config=SimpleNamespace(read_only=False))
        self.session.driver.current_url='https://fichaxe.usc.gal/pas/solicitude/123/resumo'
        self.link.get_attribute.return_value='https://fichaxe.usc.gal/pas/solicitude/123/resumo/solicitar'
        self.session.driver.find_element.side_effect=lambda by, selector: {
            (By.ID,'formularioSolicitude'):self.form, (By.ID,'seguinte'):self.next,
            (By.CSS_SELECTOR,'a[href$="/resumo/solicitar"]'):self.link}[(by,selector)]

    def submit(self, after, **kwargs):
        with patch.object(absence, '_read_review', side_effect=[REVIEW,after]):
            return absence.submit_absence_request(self.session,SELECTION,**kwargs)

    def test_unknown_or_rejected_status_is_uncertain_after_one_click(self):
        for state in ('Anulada','Erro','Descoñecido','Borrador',''):
            self.link.reset_mock()
            with self.subTest(state=state), self.assertRaises(absence.AbsenceRequestUncertain):
                self.submit({**REVIEW,'state':state,'canSubmit':False})
            self.link.click.assert_called_once()

    def test_different_receipt_id_is_uncertain(self):
        self.link.click.side_effect=lambda: setattr(self.session.driver,'current_url',
            'https://fichaxe.usc.gal/pas/solicitude/456/resumo')
        with self.assertRaises(absence.AbsenceRequestUncertain):
            self.submit({**REVIEW,'state':'Solicitada','canSubmit':False})
        self.link.click.assert_called_once()

    def test_direct_submission_does_not_capture_and_reports_verified_state(self):
        with patch.object(absence,'_capture_full_page') as capture:
            result=self.submit({**REVIEW,'state':'Solicitada','canSubmit':False})
        self.assertEqual((result['id'], result['state']), ('123','Solicitada'))
        capture.assert_not_called()
        self.link.click.assert_called_once()

    def test_capture_and_callback_failure_prevent_submission(self):
        with patch.object(absence,'_read_review',return_value=REVIEW):
            with patch.object(absence,'_capture_full_page',side_effect=WebDriverException()), self.assertRaises(absence.AbsenceRequestError):
                absence.submit_absence_request(self.session,SELECTION,confirm=lambda _:True)
            with patch.object(absence,'_capture_full_page',return_value=b'png'), self.assertRaises(absence.AbsenceRequestCancelled):
                absence.submit_absence_request(self.session,SELECTION,confirm=Mock(side_effect=RuntimeError('offline')))
        self.link.click.assert_not_called()
