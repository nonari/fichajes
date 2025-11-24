import time
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
from fichaxebot.scrap_functions.view_calendar import CalendarFetchError, fetch_calendar_summary as _fetch_calendar_summary

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
        self.driver = self._create_driver(headless=headless)
        self.wait = WebDriverWait(self.driver, 20)
        self.config = get_config()

    # -------------------------- DRIVER CREATION ----------------------------- #

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

    # ---------------------- ENSURE ACCESS TO PROTECTED URL ------------------ #

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

    # ---------------------------- LOGIN ------------------------------------ #

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
                (By.XPATH, "//h1[contains(text(), 'Acceso correcto')]")
            )
        )

        logger.info("Login successful")

    # ---------------------------- CHECK-IN --------------------------------- #

    def perform_check_in(self, action: str) -> CheckInResult:
        return _perform_check_in(self, action)

    # -------------------------- DAILY RECORDS ------------------------------ #

    def get_today_records(self) -> list[dict[str, str]]:
        return _get_today_records(self)

    # --------------------------- MOCK VACATIONS ----------------------------- #

    def retrieve_vacations_info(self) -> dict:
        """
        Mock method for future real scraping.
        """
        # When implemented → call:
        # self._ensure_access_to(PROTECTED_VACATIONS_URL)
        return {
            "vacations_used": 8,
            "vacations_remaining": 14,
            "source": "mock"
        }

    # -------------------------- CALENDAR SUMMARY --------------------------- #

    def fetch_calendar_summary(self) -> list[str]:
        return _fetch_calendar_summary(self)


    # ------------------------------ CLOSE ---------------------------------- #

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass
