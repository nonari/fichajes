"""Offline checks for selecting official USC labels; no network destinations allowed."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select, WebDriverWait

from fichaxebot.scrap_functions import absence_request as absence


@unittest.skipUnless(os.environ.get('CHROMEDRIVER'), 'Set CHROMEDRIVER for local browser tests')
class AbsenceTypeSelectorTests(unittest.TestCase):
    def test_absence_selection_uses_official_name_when_web_code_changes(self):
        options = webdriver.ChromeOptions()
        for flag in ('--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking'):
            options.add_argument(flag)
        browser = webdriver.Chrome(service=Service(os.environ['CHROMEDRIVER']), options=options)
        self.addCleanup(browser.quit)
        browser.execute_cdp_cmd('Network.enable', {})
        browser.execute_cdp_cmd('Network.setBlockedURLs', {'urls': ['http://*', 'https://*']})
        browser.get('data:text/html;charset=utf-8,' + quote('''
            <select id="ano"><option value="2026">2026</option></select>
            <select id="idTipoAusencia"><option value="">Elixir</option>
              <option value="new-code">Traslado de domicilio</option></select>
            <table id="taboaPeriodos"><tbody></tbody></table>
            <button id="engadePeriodo" onclick="document.querySelector('tbody').innerHTML =
              document.getElementById('period').innerHTML">Engadir</button>
            <template id="period"><tr class="periodo"><td>
              <input name="periodo.dataInicio"><input name="periodo.dataFin"><input type="checkbox">
            </td></tr></template>
            <textarea id="observacions"></textarea>
            <script>window.fraccionamento = false;</script>
        '''))
        session = SimpleNamespace(driver=browser, wait=WebDriverWait(browser, 2))
        data = {'year': 2026, 'absenceTypeId': '6', 'periods': [{'date': '2026-01-12'}]}
        catalog = {'years': [2026], 'types': [
            {'id': '6', 'name': 'Traslado de domicilio', 'requiresHours': False}]}
        selection = absence.validate_selection(data, catalog)
        with patch.object(absence, '_open_form'):
            absence.fill_absence_request(session, selection)
        chosen = Select(browser.find_element(By.ID, 'idTipoAusencia')).first_selected_option
        self.assertEqual(chosen.text, 'Traslado de domicilio')
        self.assertEqual(chosen.get_attribute('value'), 'new-code')
