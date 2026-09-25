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

    def open_app(self, cases=()):
        html = re.sub(r"<script\b[^>]*src=[^>]+>\s*</script>", "", (ROOT / "docs/congreso.html").read_text())
        data = {"token": "launch", "today": "2026-10-01", "minStart": "2026-10-06", "cases": list(cases)}
        self.browser.get("about:blank")
        self.browser.get("data:text/html;charset=utf-8," + quote(html) + "#data=" + quote(json.dumps(data)))
        self.browser.execute_script("""
            window.sent = [];
            window.Telegram = {WebApp: {ready(){}, expand(){}, sendData(value){window.sent.push(JSON.parse(value));}}};
            window.FullCalendar = {Calendar: class {
                constructor(el, options){this.el=el;this.options=options;window.testCalendar=this;}
                render(){}
            }};
        """)
        self.browser.execute_script((ROOT / "docs/congreso.js").read_text())

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
        card.find_element(By.TAG_NAME, "button").click()
        self.assertEqual(self.sent(), [{"type": "congreso_dieta_cancel", "token": "launch", "case": "c1"}])
