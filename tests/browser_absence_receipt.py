"""Replay USC's observed post-submission redirect locally, including confirmation."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

from fichaxebot.scrap_functions import absence_request as absence
from tests.test_absence_submission import SELECTION


REVIEW_HTML = '''<!doctype html><meta charset="utf-8"><body>
<p class="h5">Ano</p><p>2026</p>
<p class="h5">Estado</p><p>Borrador</p>
<p class="h5">Tipo de solicitude</p><p>Ausencias autorizadas</p>
<p class="h5">Tipo de ausencia</p><p>Traslado de domicilio</p>
<p class="h5">Observacións</p><p></p>
<table><thead><tr><th>Dende</th><th>Ata</th></tr></thead>
<tbody><tr><td>12/01/2026</td><td>12/01/2026</td></tr></tbody></table>
<a href="/pas/solicitude/123/resumo/solicitar">Solicitar</a></body>'''


class Handler(BaseHTTPRequestHandler):
    submissions = 0
    list_html = ''

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.endswith('/resumo/solicitar'):
            type(self).submissions += 1
            self.send_response(302)
            self.send_header('Location', '/pas/solicitudesPropias')
            self.end_headers()
            return
        if path == '/pas/solicitudesPropias':
            body = type(self).list_html
        elif path.endswith('/resumo'):
            body = REVIEW_HTML
        else:
            body = '''<form id="formularioSolicitude" action="/pas/solicitude/123/resumo">
              <button id="seguinte" type="submit">Seguinte</button></form>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


@unittest.skipUnless(os.environ.get('CHROMEDRIVER'), 'Set CHROMEDRIVER for offline browser tests')
class AbsenceReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        cls.url = f'http://127.0.0.1:{server.server_port}/pas/solicitude/0'
        options = webdriver.ChromeOptions()
        for flag in ('--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking',
                     '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'):
            options.add_argument(flag)
        cls.browser = webdriver.Chrome(service=Service(os.environ['CHROMEDRIVER']), options=options)
        cls.addClassCleanup(cls.browser.quit)
        cls.browser.execute_cdp_cmd('Network.enable', {})
        cls.browser.execute_cdp_cmd('Network.setBlockedURLs', {'urls': ['https://*', '*usc.es*', '*usc.gal*']})

    def setUp(self):
        Handler.submissions = 0
        Handler.list_html = (Path(__file__).parent / 'fixtures/absence_submitted_list.html').read_text()
        self.browser.get(self.url)
        self.session = SimpleNamespace(driver=self.browser, wait=WebDriverWait(self.browser, 2),
                                       config=SimpleNamespace(read_only=False))
        patcher = patch.object(absence, 'REQUEST_URL', self.url)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_confirmed_submission_is_verified_in_redirected_list(self):
        captures = []
        def confirm(png):
            captures.append(png)
            return True
        result = absence.submit_absence_request(self.session, SELECTION, confirm=confirm)
        self.assertEqual((result['id'], result['state']), ('123', 'Solicitada'))
        self.assertEqual(Handler.submissions, 1)
        self.assertTrue(captures[0].startswith(b'\x89PNG'))
        self.assertTrue(self.browser.current_url.endswith('/pas/solicitudesPropias'))

    def test_other_submitted_request_cannot_confirm_this_one(self):
        Handler.list_html = Handler.list_html.replace('/123/resumo', '/999/resumo')
        with self.assertRaises(absence.AbsenceRequestUncertain):
            absence.submit_absence_request(self.session, SELECTION)
        self.assertEqual(Handler.submissions, 1)

    def test_draft_cannot_be_reported_as_submitted(self):
        Handler.list_html = Handler.list_html.replace('Solicitada', 'Borrador', 1)
        with self.assertRaises(absence.AbsenceRequestUncertain):
            absence.submit_absence_request(self.session, SELECTION)
        self.assertEqual(Handler.submissions, 1)
