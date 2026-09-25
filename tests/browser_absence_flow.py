"""Offline browser checks; set CHROMEDRIVER to a locally installed driver."""
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from types import SimpleNamespace
import unittest
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from fichaxebot.usc_api import UscWebSession
from fichaxebot.scrap_functions.absence_request import AbsenceRequestCancelled, AbsenceRequestError, AbsenceRequestUncertain

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('CHROMEDRIVER'), 'Set CHROMEDRIVER for local browser tests')
class AbsenceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = webdriver.ChromeOptions()
        for flag in ('--headless=new', '--no-sandbox', '--disable-dev-shm-usage'):
            options.add_argument(flag)
        cls.browser = webdriver.Chrome(service=Service(os.environ['CHROMEDRIVER']), options=options)
        cls.addClassCleanup(cls.browser.quit)
        cls.browser.execute_cdp_cmd('Network.enable', {})
        cls.browser.execute_cdp_cmd('Network.setBlockedURLs', {'urls': ['http://*', 'https://*']})

    def open_html(self, html, fragment=''):
        self.browser.get('about:blank')
        self.browser.get('data:text/html;charset=utf-8,' + quote(html) + fragment)

    def open_app(self, hourly=False):
        html = (ROOT / 'docs/ausencias.html').read_text()
        html = re.sub(r'<script\b[^>]*src=[^>]+>\s*</script>', '', html)
        data = {'today': '2026-09-25', 'requestId': 'launch', 'years': [2026], 'types': [
            {'id': '3' if hourly else '6', 'name': 'Curso' if hourly else 'Traslado', 'requiresHours': hourly}],
            'confirmationRequired': True}
        self.open_html(html, '#data=' + quote(json.dumps(data)))
        self.browser.execute_script("""
            window.sent = [];
            window.Telegram = {WebApp: {ready(){}, expand(){}, sendData(value){window.sent.push(JSON.parse(value));}}};
            window.FullCalendar = {Calendar: class {
                constructor(el, options){this.el=el;this.options=options;window.testCalendar=this;}
                render(){} updateSize(){} setOption(){} gotoDate(){}
            }};
        """)
        self.browser.execute_script((ROOT / 'docs/ausencias.js').read_text())
        self.browser.find_element(By.ID, 'year-next').click()
        self.browser.find_element(By.CSS_SELECTOR, 'input[name="absenceType"]').click()
        self.browser.find_element(By.ID, 'type-next').click()
        self.browser.execute_script("window.testCalendar.options.dateClick({dateStr:'2026-01-12'});")

    def test_checkbox_defaults_off_and_sends_single_past_day_request(self):
        self.open_app()
        self.assertFalse(self.browser.find_element(By.ID, 'attach-documents').is_selected())
        self.browser.find_element(By.ID, 'send').click()
        self.browser.execute_script("document.getElementById('send').onclick();")
        sent = self.browser.execute_script('return window.sent;')
        self.assertEqual(sent, [{'type': 'absence_request_submit', 'requestId': 'launch', 'year': 2026,
            'absenceTypeId': '6', 'periods': [{'date': '2026-01-12'}], 'observations': '', 'attachDocuments': False}])

    def test_checked_box_sends_intent_and_hourly_inputs_required(self):
        self.open_app(hourly=True)
        self.assertFalse(self.browser.find_element(By.ID, 'send').is_enabled())
        self.browser.execute_script("""
            const fields=document.querySelectorAll('input[type=time]');
            fields[0].value='09:00'; fields[0].dispatchEvent(new Event('input'));
            fields[1].value='11:00'; fields[1].dispatchEvent(new Event('input'));
        """)
        self.browser.find_element(By.ID, 'attach-documents').click()
        self.browser.find_element(By.ID, 'send').click()
        sent = self.browser.execute_script('return window.sent[0];')
        self.assertTrue(sent['attachDocuments'])
        self.assertEqual(sent['periods'], [{'date':'2026-01-12','startTime':'09:00','endTime':'11:00'}])

    def test_oversized_payload_stays_open_and_reports_problem(self):
        self.open_app()
        self.browser.execute_script("document.getElementById('observations').value='é'.repeat(3000);")
        self.browser.find_element(By.ID, 'send').click()
        self.assertEqual(self.browser.execute_script('return window.sent;'), [])
        self.assertTrue(self.browser.find_element(By.ID, 'status').text)

    def session(self):
        session = UscWebSession.__new__(UscWebSession)
        session.driver = self.browser
        session.wait = WebDriverWait(self.browser, 1)
        session._lock = RLock()
        session.config = SimpleNamespace(read_only=False)
        html = (ROOT / 'tests/fixtures/absence_wizard.html').read_text()
        session._ensure_access_to = lambda url: self.open_html(html)
        return session

    def test_real_catalog_fill_upload_review_screenshot_and_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'proof.pdf'
            path.write_bytes(b'%PDF-1.4\nproof')
            captures = []
            def confirm(png):
                captures.append(png)
                return True
            result = self.session().submit_absence_request({'year':2026,'absenceTypeId':'3',
                'periods':[{'date':'2026-01-12','startTime':'09:00','endTime':'11:00'}],
                'observations':'Curso de formación','attachments':[str(path)]}, confirm=confirm)
            self.assertEqual(result['id'], '123')
            self.assertEqual(result['state'], 'Solicitada')
            self.assertEqual(result['attachments'], ['proof.pdf'])
            self.assertTrue(path.exists())
            self.assertTrue(captures[0].startswith(b'\x89PNG\r\n'))
            self.assertEqual(self.browser.execute_script('return window.submissions'), 1)

    def test_declined_or_nonboolean_confirmation_never_submits(self):
        for decision in (False, None, 1, 'yes'):
            with self.subTest(decision=decision), self.assertRaises(AbsenceRequestCancelled):
                self.session().submit_absence_request({'year':2026,'absenceTypeId':'6',
                    'periods':[{'date':'2026-01-12'}]}, confirm=lambda png: decision)
            self.assertEqual(self.browser.execute_script('return window.submissions'), 0)

    def test_review_changed_during_confirmation_prevents_submission(self):
        def confirm(png):
            self.browser.execute_script("document.querySelector('#review-periods td').textContent='13/01/2026';")
            return True
        with self.assertRaises(AbsenceRequestError):
            self.session().submit_absence_request({'year':2026,'absenceTypeId':'6',
                'periods':[{'date':'2026-01-12'}]}, confirm=confirm)
        self.assertEqual(self.browser.execute_script('return window.submissions'), 0)

    def test_unknown_receipt_is_not_retried(self):
        def confirm(png):
            self.browser.execute_script('window.unknownReceipt=true;')
            return True
        with self.assertRaises(AbsenceRequestUncertain):
            self.session().submit_absence_request({'year':2026,'absenceTypeId':'6',
                'periods':[{'date':'2026-01-12'}]}, confirm=confirm)
        self.assertEqual(self.browser.execute_script('return window.submissions'), 1)
