"""Offline congress wizard checks; set CHROMEDRIVER to enable. Never contacts USC."""
from copy import deepcopy
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

from fichaxebot.scrap_functions import congress_request as congress
from test_congress_request import DATA

PDF = b'%PDF-1.4\n1 0 obj <</Type /Catalog>> endobj\n%%EOF\n'


class Handler(BaseHTTPRequestHandler):
    pdf_requests = 0

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        if query.get('_eventId') == ['obterSolicitudePdf']:
            type(self).pdf_requests += 1
            if 'testsession=authenticated' not in self.headers.get('Cookie', ''):
                self.send_response(403); self.end_headers(); return
            if query.get('mode') == ['html']:
                body, content_type = b'<html>Login</html>', 'text/html'
            else:
                body, content_type = PDF, 'application/pdf'
        else:
            body = (Path(__file__).parent / 'fixtures/congress_wizard.html').read_bytes()
            content_type = 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Set-Cookie', 'testsession=authenticated; SameSite=Strict')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(os.environ.get('CHROMEDRIVER'), 'Set CHROMEDRIVER to run offline browser checks')
class CongressBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server_thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/RRHH_InvAsistenciaCongresos.htm'
        options = webdriver.ChromeOptions()
        for flag in ('--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking',
                     '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'):
            options.add_argument(flag)
        cls.browser = webdriver.Chrome(service=Service(os.environ['CHROMEDRIVER']), options=options)
        cls.addClassCleanup(cls.browser.quit)
        cls.browser.execute_cdp_cmd('Network.enable', {})
        cls.browser.execute_cdp_cmd('Network.setBlockedURLs', {'urls': ['https://*', '*usc.es*', '*usc.gal*']})

    def setUp(self):
        self.patch_url = patch.object(congress, 'REQUEST_URL', self.url)
        self.patch_url.start(); self.addCleanup(self.patch_url.stop)
        self.now = patch.object(congress, 'get_madrid_now')
        self.now.start().return_value.date.return_value = date(2026, 9, 25)
        self.addCleanup(self.now.stop)
        self.session = SimpleNamespace(driver=self.browser, wait=WebDriverWait(self.browser, 2),
                                       _ensure_access_to=self.browser.get)
        Handler.pdf_requests = 0

    def test_all_steps_pdf_cookie_and_one_confirmed_submission(self):
        data = deepcopy(DATA)
        data['teaching_assigned'] = True
        data['teaching_cover'] = 'Teaching covered by colleague'
        def confirm(pdf):
            self.assertEqual(pdf, PDF)
            self.assertEqual(self.browser.execute_script('return window.submissions'), 0)
            self.assertIn('execution=live7', self.browser.current_url)
            return True
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'program.pdf'; file.write_bytes(PDF)
            data['attachments'] = [{'title': 'Programme', 'path': str(file)}]
            result = congress.submit_congress_request(self.session, data, confirm)
        self.assertEqual(result, {'status': 'unverified', 'submission_attempted': True})
        self.assertEqual(self.browser.execute_script('return window.submissions'), 1)
        self.assertEqual(Handler.pdf_requests, 1)
        saved = self.browser.execute_script('return window.saved')
        self.assertEqual(saved['1']['autorizacion'], 'on')
        self.assertEqual(saved['2']['email'], data['contact']['email'])
        self.assertEqual(saved['3']['idConcello'], '78')
        details = saved['4']
        self.assertIn('10/10/2026', details.values())
        self.assertIn(data['teaching_cover'], details.values())
        self.assertIn('Contratados predoutorais', details.values())
        self.assertEqual(saved['5']['asinantes[0].nid'], 'directory-id')
        self.assertEqual(saved['6']['anexos[0].asunto'], 'Programme')

    def test_direct_foreign_address_preserves_contact_and_skips_pdf(self):
        data = deepcopy(DATA); data.pop('contact')
        data['address'] = {'country': 'Francia', 'department': 'Paris', 'postal_code': '75001', 'line1': 'Example 1'}
        congress.submit_congress_request(self.session, data)
        self.assertEqual(Handler.pdf_requests, 0)
        saved = self.browser.execute_script('return window.saved')
        self.assertEqual(saved['2']['email'], 'original@example.test')
        self.assertEqual(saved['3']['departamento'], 'Paris')
        self.assertIn('-', saved['4'].values())

    def test_cancel_keeps_live_review_without_submitting(self):
        with self.assertRaises(congress.CongressRequestCancelled):
            congress.submit_congress_request(self.session, DATA, lambda pdf: False)
        self.assertEqual(self.browser.execute_script('return window.submissions'), 0)
        self.assertIn('execution=live7', self.browser.current_url)

    def test_ambiguous_supervisor_does_not_advance_to_submission(self):
        with self.assertRaisesRegex(congress.CongressRequestError, 'ambigua'):
            congress.submit_congress_request(self.session, {**DATA, 'supervisor_query': 'ambiguous'})
        self.assertEqual(self.browser.execute_script('return window.submissions'), 0)
        self.assertIn('execution=live5', self.browser.current_url)

    def test_html_instead_of_pdf_never_reaches_confirmation(self):
        def open_fixture(url):
            self.browser.get(url)
            self.browser.execute_script("window.pdfMode = 'html'")
        self.session._ensure_access_to = open_fixture
        confirm = Mock(return_value=True)
        with self.assertRaises(congress.CongressRequestError):
            congress.submit_congress_request(self.session, DATA, confirm)
        confirm.assert_not_called()
        self.assertEqual(self.browser.execute_script('return window.submissions'), 0)

    def test_changed_flow_after_confirmation_does_not_submit(self):
        def confirm(pdf):
            self.browser.execute_script("document.querySelector('input[name=execution]').value = 'expired'")
            return True
        with self.assertRaises(congress.CongressRequestError):
            congress.submit_congress_request(self.session, DATA, confirm)
        self.assertEqual(self.browser.execute_script('return window.submissions'), 0)

    def test_server_validation_and_stricter_live_date_constraints_are_respected(self):
        for setting, value, message in (('formErrorAt', 2, 'USC rejected'), ('minimumDate', '15/10/2026', 'restricciones')):
            with self.subTest(setting=setting):
                def open_fixture(url):
                    self.browser.get(url)
                    self.browser.execute_script('window[arguments[0]] = arguments[1]', setting, value)
                self.session._ensure_access_to = open_fixture
                with self.assertRaisesRegex(congress.CongressRequestError, message):
                    congress.submit_congress_request(self.session, DATA)
                self.assertEqual(self.browser.execute_script('return window.submissions'), 0)
