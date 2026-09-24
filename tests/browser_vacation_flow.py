"""Offline browser checks: CHROMEDRIVER=/path/to/chromedriver python -m unittest discover -s tests -p browser_vacation_flow.py."""
import json
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from fichaxebot.scrap_functions.vacation_request import submit_vacation_request


ROOT = Path(__file__).resolve().parents[1]
# Sanitized structure of USC's transient summary: no table ID, with day counts.
SUMMARY = """
<p class="h5">Estado</p><p>Borrador</p>
<p class="h5">Ano</p><p><span>2026</span></p>
<p class="h5">Tipo de solicitude</p><p>Vacacións, permisos e licenzas</p>
<p class="h5">Tipo de vacacións, permisos e licenzas</p><p>Vacacións</p>
<fieldset><div><legend>Períodos</legend><table class="table table-condensed">
<thead><tr><th>Dende</th><th>Ata</th><th>Número de días</th></tr></thead>
<tbody><tr><td>01/10/2026</td><td>01/10/2026</td><td>1</td></tr></tbody>
</table></div></fieldset>
"""


@unittest.skipUnless(os.environ.get("CHROMEDRIVER"), "Set CHROMEDRIVER to run offline Chrome tests")
class VacationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = webdriver.ChromeOptions()
        for flag in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage"):
            options.add_argument(flag)
        cls.browser = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=options)
        cls.addClassCleanup(cls.browser.quit)
        cls.browser.execute_cdp_cmd("Network.enable", {})
        cls.browser.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["http://*", "https://*"]})

    def open_html(self, html, fragment=""):
        self.browser.get("data:text/html;charset=utf-8," + quote(html) + fragment)

    def test_transient_summary_is_submitted_in_place_and_verified(self):
        summary = SUMMARY + '<a href="https://fichaxe.usc.gal/pas/solicitude/123/resumo/solicitar">Solicitar</a>'
        submitted = SUMMARY.replace("Borrador", "Solicitada")
        self.open_html('<form id="formularioSolicitude"><button type="button" id="seguinte">Seguinte</button></form>')
        self.browser.execute_script("""
            const [summary, submitted] = arguments;
            window.submissions = 0;
            document.getElementById('seguinte').onclick = () => {
                document.body.innerHTML = summary;
                document.querySelector('a').onclick = event => {
                    event.preventDefault(); window.submissions++;
                    document.body.innerHTML = submitted;
                };
            };
        """, summary, submitted)
        session = SimpleNamespace(driver=self.browser, wait=WebDriverWait(self.browser, 2))
        selection = {"year": 2026, "vacationTypeName": "Vacacións", "days": ["2026-10-01"]}
        result = submit_vacation_request(session, selection)
        self.assertEqual(result, {**selection, "id": "123", "state": "Solicitada"})
        self.assertEqual(self.browser.execute_script("return window.submissions"), 1)

    def open_mini_app(self):
        html = (ROOT / "docs/vacaciones.html").read_text(encoding="utf-8")
        html = re.sub(r'<script\b[^>]*src=[^>]+>\s*</script>', '', html)
        data = {
            "today": "2026-09-24", "currentYear": 2026, "entries": [], "requestId": "test",
            "years": [{"year": 2026, "types": [
                {"id": "16", "name": "Vacacións", "remainingDays": 10, "remainingHours": 0},
            ]}],
        }
        self.open_html(html, "#data=" + quote(json.dumps(data)))
        self.browser.execute_script("""
            window.sent = [];
            window.Telegram = {WebApp: {ready() {}, expand() {}, sendData(data) {window.sent.push(JSON.parse(data));}}};
            window.FullCalendar = {Calendar: class {
                constructor(el, options) {this.el = el; this.options = options; window.testCalendar = this;}
                render() {} updateSize() {}
            }};
        """)
        self.browser.execute_script((ROOT / "docs/vacaciones.js").read_text(encoding="utf-8"))

    def test_date_selection_has_one_submit_action_and_sends_explicit_intent_once(self):
        self.open_mini_app()
        self.browser.find_element(By.ID, "year-next").click()
        self.browser.find_element(By.CSS_SELECTOR, 'input[name="vacationType"]').click()
        self.browser.find_element(By.ID, "type-next").click()
        send = self.browser.find_element(By.ID, "send")
        self.assertTrue(send.is_displayed())
        self.assertFalse(send.is_enabled())
        self.browser.execute_script("window.testCalendar.options.dateClick({dateStr: '2026-10-01'});")
        self.assertEqual(send.text, "Solicitar en USC")
        self.assertTrue(send.is_enabled())
        send.click()
        self.browser.execute_script("document.getElementById('send').onclick();")
        self.assertEqual(self.browser.execute_script("return window.sent"), [{
            "type": "vacation_request_submit", "requestId": "test", "year": 2026,
            "vacationTypeId": "16", "days": ["2026-10-01"],
        }])
