"""Offline browser checks of the congress Mini App; set CHROMEDRIVER to a local driver."""
import json
import os
import re
import unittest
from pathlib import Path
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("CHROMEDRIVER"), "Set CHROMEDRIVER for local browser tests")
class CongresoBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = webdriver.ChromeOptions()
        for flag in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage"):
            options.add_argument(flag)
        cls.browser = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=options)
        cls.addClassCleanup(cls.browser.quit)
        cls.browser.execute_cdp_cmd("Network.enable", {})
        cls.browser.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["http://*", "https://*"]})

    # Telegram appends its own parameters to the Mini App URL fragment.
    TELEGRAM_PARAMS = "&tgWebAppData=query_id%3DAAA%26user%3D%257B%257D&tgWebAppVersion=7.10&tgWebAppPlatform=android"

    def open_app(self, cases=()):
        html = re.sub(r"<script\b[^>]*src=[^>]+>\s*</script>", "", (ROOT / "plugins/congreso_dieta/web/congreso.html").read_text())
        data = {"token": "launch", "today": "2026-10-01", "minStart": "2026-10-06", "minStartNoAuth": "2026-01-01",
                "cases": [{"steps": [
                    {"key": "authorization", "label": "Autorización", "state": "completed", "detail": "Recibida", "action": None},
                    {"key": "absence", "label": "Ausencia", "state": "scheduled", "detail": "Programada", "action": "absence"},
                    {"key": "sign", "label": "Firma", "state": "error", "detail": "Error al firmar", "action": "sign"},
                ], **case} for case in cases]}
        self.browser.get("about:blank")
        self.browser.get("data:text/html;charset=utf-8," + quote(html) + "#data=" + quote(json.dumps(data)) + self.TELEGRAM_PARAMS)
        self.browser.execute_script("""
            window.sent = [];
            window.Telegram = {WebApp: {ready(){}, expand(){}, sendData(value){window.sent.push(JSON.parse(value));}}};
            window.FullCalendar = {Calendar: class {
                constructor(el, options){this.el=el;this.options=options;window.testCalendar=this;}
                render(){}
            }};
        """)
        self.browser.execute_script((ROOT / "plugins/congreso_dieta/web/congreso.js").read_text())

    def click_day(self, day):
        self.browser.execute_script(f"window.testCalendar.options.dateClick({{dateStr:'{day}'}});")

    def sent(self):
        return self.browser.execute_script("return window.sent;")

    def test_range_selection_sends_a_new_request(self):
        self.open_app()
        self.click_day("2026-10-20")
        self.click_day("2026-10-22")
        send = self.browser.find_element(By.ID, "send")
        self.assertTrue(send.is_enabled())
        send.click()
        self.assertEqual(self.sent(), [{"type": "congreso_dieta_new", "token": "launch",
                                        "start": "2026-10-20", "end": "2026-10-22"}])

    def test_blocked_days_and_overlaps_disable_sending(self):
        self.open_app([{"id": "c1", "start": "2026-10-20", "end": "2026-10-22", "status": "Esperando",
                        "problem": None}])
        self.click_day("2026-10-02")   # before minStart: ignored
        self.click_day("2026-10-21")   # inside an open case: ignored
        self.click_day("2026-10-19")
        self.click_day("2026-10-23")   # spans the open case
        self.assertFalse(self.browser.find_element(By.ID, "send").is_enabled())
        self.assertIn("solapan", self.browser.find_element(By.ID, "status").text)

    def test_open_cases_show_problems_and_can_be_cancelled(self):
        self.open_app([{"id": "c1", "start": "2026-10-20", "end": "2026-10-22", "status": "Esperando",
                        "problem": "Error al firmar"}])
        card = self.browser.find_element(By.CSS_SELECTOR, ".case")
        self.assertIn("Error al firmar", card.text)
        card.find_element(By.CSS_SELECTOR, "button.secondary").click()
        self.assertEqual(self.sent(), [{"type": "congreso_dieta_cancel", "token": "launch", "case": "c1"}])

    def test_no_auth_tick_allows_earlier_dates(self):
        self.open_app()
        self.click_day("2026-09-10")   # before minStart: ignored without the tick
        self.assertEqual(self.browser.find_element(By.ID, "selection").text, "Sin fechas seleccionadas")
        tick = self.browser.find_element(By.ID, "no-auth")
        tick.click()
        self.click_day("2026-09-10")
        self.click_day("2026-09-11")
        tick.click()                   # the range is no longer allowed: cleared
        self.assertEqual(self.browser.find_element(By.ID, "selection").text, "Sin fechas seleccionadas")
        tick.click()
        self.click_day("2026-09-10")
        self.click_day("2026-09-11")
        self.browser.find_element(By.ID, "send").click()
        self.assertEqual(self.sent(), [{"type": "congreso_dieta_new", "token": "launch", "start": "2026-09-10",
                                        "end": "2026-09-11", "noAuth": True}])

    def test_status_lines_and_clickable_actions_send_only_confirmation_requests(self):
        for action in ('absence', 'sign'):
            with self.subTest(action=action):
                self.open_app([{'id': 'c1', 'start': '2026-09-28', 'end': '2026-09-30', 'problem': 'No module named uno'}])
                card = self.browser.find_element(By.CSS_SELECTOR, '.case')
                self.assertTrue(card.text.startswith('Del '))
                rows = card.find_elements(By.CSS_SELECTOR, '.step')
                self.assertEqual(len(rows), 3)
                for row, expected in zip(rows, ('✅ Autorización', '⏱️ Ausencia', '❌ Firma')):
                    self.assertTrue(row.text.startswith(expected), row.text)
                self.assertEqual(rows[0].tag_name, 'p')
                card.find_element(By.CSS_SELECTOR, f'button[data-step="{action}"]').click()
                self.assertEqual(self.sent(), [{'type': 'congreso_dieta_action', 'token': 'launch',
                                                'case': 'c1', 'action': action}])
                self.assertFalse(any(button.is_enabled() for button in card.find_elements(By.TAG_NAME, 'button')))

    def test_special_procedure_omits_authorization_and_completed_signature_is_not_clickable(self):
        self.open_app([{'id': 'c1', 'start': '2026-09-28', 'end': '2026-09-30', 'problem': '<script>bad</script>',
                        'steps': [
                            {'key': 'absence', 'label': 'Ausencia', 'state': 'error', 'detail': 'No solicitada', 'action': 'absence'},
                            {'key': 'sign', 'label': 'Firma', 'state': 'completed', 'detail': 'Firmada', 'action': None}]}])
        card = self.browser.find_element(By.CSS_SELECTOR, '.case')
        self.assertNotIn('Autorización', card.text)
        self.assertEqual(card.find_element(By.CSS_SELECTOR, '[data-step="sign"]').tag_name, 'p')
        self.assertEqual(card.find_element(By.CSS_SELECTOR, '.problem').text, '<script>bad</script>')

    def test_old_keyboard_payload_shows_reopen_notice_and_keeps_calendar_usable(self):
        self.open_app([{'id': 'c1', 'start': '2026-09-28', 'end': '2026-09-30', 'steps': None}])
        self.assertIn('Abre /congreso_dieta de nuevo', self.browser.find_element(By.CSS_SELECTOR, '.case').text)
        self.click_day('2026-10-20')
        self.click_day('2026-10-21')
        self.assertTrue(self.browser.find_element(By.ID, 'send').is_enabled())
