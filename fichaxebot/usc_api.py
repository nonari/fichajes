import time
from threading import RLock
from typing import Final

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.mark import (
    CheckInResult,
    get_today_records as _get_today_records,
    perform_check_in as _perform_check_in,
)
from fichaxebot.scrap_functions.vacations_info import (
    fetch_vacations_info as _fetch_vacations_info,
)
from fichaxebot.scrap_functions.view_calendar import fetch_calendar_summary as _fetch_calendar_summary
from fichaxebot.scrap_functions.congress_request import submit_congress_request as _submit_congress_request
from fichaxebot.scrap_functions.vacation_request import (
    REQUEST_URL,
    fetch_vacation_catalog, fill_vacation_request, validate_selection,
    submit_vacation_request as _submit_vacation_request,
)
from fichaxebot.scrap_functions.absence_request import (
    fetch_absence_catalog, fill_absence_request, validate_selection as validate_absence_selection,
    submit_absence_request as _submit_absence_request,
)
from fichaxebot.utils import get_madrid_now

logger = get_logger(__name__)


CAS_LOGIN_URL: Final[str] = "https://login.usc.es/cas/login"


class UscWebSession:
    """
    Manages a persistent Selenium session with automatic session recovery.

    Mechanism:
    Each public method calls `self._ensure_access_to(PROTECTED_URL)`.

    `_ensure_access_to(url)`:
      - Loads the protected URL
      - If redirected to CAS, performs login
      - Finally loads the protected URL again (authenticated)
    """

    def __init__(self, headless: bool = True):
        self._lock = RLock()
        self.driver = self._create_driver(headless=headless)
        self.wait = WebDriverWait(self.driver, 20)
        self.config = get_config()
        try:
            self.internal_user_id = self._discover_internal_user_id()
        except Exception:
            self.close()
            raise

    def _discover_internal_user_id(self) -> str:
        """Read the authenticated applicant ID without modifying the request form."""
        self._ensure_access_to(REQUEST_URL)
        def read_applicant(driver):
            fields = driver.find_elements(By.ID, "idSolicitante")
            value = fields[0].get_attribute("value") if fields else ""
            value = (value or "").strip()
            return value if value.isascii() and value.isdecimal() and int(value) > 0 else False

        person_id = self.wait.until(
            read_applicant,
            message="No se pudo descubrir el identificador interno del usuario en USC.",
        )
        logger.info("Internal USC user ID discovered from the authenticated request form")
        return person_id

    def perform_check_in(self, action: str) -> CheckInResult:
        self._require_writes_enabled()
        with self._lock:
            return _perform_check_in(self, action)

    def get_today_records(self) -> list[dict[str, str]]:
        with self._lock:
            return _get_today_records(self)

    def retrieve_vacations_info(self) -> tuple[list[str], list[str], list[list[int]]]:
        with self._lock:
            return _fetch_vacations_info(self)

    def fetch_calendar_summary(self, *, for_vacation_selection: bool = False) -> list[str]:
        with self._lock:
            return _fetch_calendar_summary(self, for_vacation_selection=for_vacation_selection)

    def fetch_vacation_selection_data(self) -> dict:
        with self._lock:
            catalog = fetch_vacation_catalog(self)
            entries = _fetch_calendar_summary(self, for_vacation_selection=True)
            return {**catalog, "entries": entries}

    def submit_vacation_request(self, data: dict, confirm=None) -> dict:
        """Validate and finish the USC wizard without releasing the browser lock."""
        self._require_writes_enabled()
        if self.config.vacation_confirmation_enabled and confirm is None:
            raise ValueError("La confirmación visual requiere una función de confirmación.")
        with self._lock:
            # Balances and calendar may have changed while the Mini App was open.
            catalog = fetch_vacation_catalog(self)
            entries = _fetch_calendar_summary(self, for_vacation_selection=True)
            selection = validate_selection(data, catalog, entries, get_madrid_now().date())
            fill_vacation_request(self, selection)
            return _submit_vacation_request(
                self, selection, confirm=confirm if self.config.vacation_confirmation_enabled else None,
            )

    def fetch_absence_selection_data(self) -> dict:
        """Read available absence years/types without creating a USC request."""
        with self._lock:
            return fetch_absence_catalog(self)

    def submit_absence_request(self, data: dict, confirm=None) -> dict:
        """Submit an absence; optional confirm receives PNG bytes and must return True.

        Attachments are local PDF paths. Run the whole synchronous call on one
        worker in async applications; the callback must not use this browser.
        """
        self._require_writes_enabled()
        with self._lock:
            catalog = fetch_absence_catalog(self)
            selection = validate_absence_selection(data, catalog)
            fill_absence_request(self, selection)
            return _submit_absence_request(self, selection, confirm=confirm)

    def submit_congress_request(self, data: dict, confirm=None) -> dict:
        """Complete the congress wizard; optional confirm receives the original PDF.

        Run the entire call on one worker when used from an async application.
        The result remains unverified until USC receipt parsing is implemented.
        """
        self._require_writes_enabled()
        with self._lock:
            return _submit_congress_request(self, data, confirm)

    def _require_writes_enabled(self) -> None:
        if self.config.read_only:
            raise PermissionError("Modo de solo lectura: las escrituras en USC están desactivadas.")

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    @staticmethod
    def _create_driver(headless: bool) -> webdriver.Chrome:
        options = Options()
        if headless:
            options.add_argument("--headless")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )

    def _ensure_access_to(self, url: str) -> None:
        """
        Ensures access to the protected URL `url`.

        Minimal-request algorithm:
            1. Try to access `url`
            2. If redirected to CAS → login
            3. Reload `url`
        """

        logger.info(f"Requesting protected resource: {url}")
        self.driver.get(url)
        time.sleep(0.4)

        # If redirected to CAS, session expired
        if CAS_LOGIN_URL in self.driver.current_url:
            logger.info("Redirection to CAS detected → session expired. Logging in...")
            self._perform_login()

            # After login, retry the protected URL
            logger.info(f"Retrying protected resource after login: {url}")
            self.driver.get(url)
            time.sleep(0.3)
        else:
            logger.info("Session active — no login needed.")

    def _perform_login(self):
        user = self.config.usc_user
        password = self.config.usc_pass

        if not user or not password:
            raise ValueError("Las credenciales de USC no están configuradas correctamente.")

        logger.info("Opening CAS login page...")
        self.driver.get(CAS_LOGIN_URL)

        # CAS form fields
        user_input = self.wait.until(EC.presence_of_element_located((By.ID, "username-input")))
        password_input = self.driver.find_element(By.ID, "password")

        user_input.send_keys(user)
        password_input.send_keys(password)

        self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        logger.info("Credentials submitted")

        self.wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//h1[contains(., 'Acceso correcto') or contains(., 'Log In Successful')]")
            )
        )

        logger.info("Login successful")
