import time
from asyncio import InvalidStateError
from dataclasses import dataclass
from typing import Final

from selenium import webdriver
from selenium.common import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.view_calendar import CalendarFetchError, _read_calendar_array, _iter_relevant_entries

logger = get_logger(__name__)


CAS_LOGIN_URL: Final[str] = "https://login.usc.es/cas/login"
MAIN_PAGE_URL: Final[str] = "https://fichaxe.usc.gal/pas/marcaxesDiarias"
CALENDAR_URL = "https://fichaxe.usc.gal/pas/calendarioAnual"

@dataclass
class CheckInResult:
    success: bool
    action: str
    message: str


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
        action = action.lower().strip()
        if action not in {"entrada", "salida"}:
            raise ValueError("La acción de fichaje debe ser 'entrada' o 'salida'.")

        # Ensure we are authenticated and landing on MAIN_PAGE_URL
        self._ensure_access_to(MAIN_PAGE_URL)

        self.wait.until(EC.presence_of_element_located((By.ID, "taboaMarcaxesPropios")))
        table = self.driver.find_element(By.ID, "taboaMarcaxesPropios")
        rows = table.find_elements(By.TAG_NAME, "tr")

        last_row = rows[-1] if rows else None
        cells = last_row.find_elements(By.TAG_NAME, "td") if last_row else []
        entry_before = cells[0].text.strip() if len(cells) > 0 else "-"
        exit_before = cells[1].text.strip() if len(cells) > 1 else "-"

        # Logic for allowed action
        if entry_before == "-":
            if exit_before == "-":
                allowed = "entrada"
            else:
                raise InvalidStateError()
        else:
            allowed = "salida" if exit_before == "-" else "entrada"

        if action != allowed:
            msg = (
                "⚠️ Ya existe una entrada pendiente de cerrar."
                if allowed == "salida"
                else "⚠️ No hay una entrada pendiente para cerrar."
            )
            return CheckInResult(False, action, msg)

        # Perform the action
        btn = self.driver.find_element(By.ID, "novaMarcaxe")
        btn.click()
        time.sleep(3)

        # Refresh and verify the new record
        self.driver.refresh()
        table = self.wait.until(
            EC.presence_of_element_located((By.ID, "taboaMarcaxesPropios"))
        )

        rows_after = table.find_elements(By.TAG_NAME, "tr")
        last_after = rows_after[-1]
        cells_after = last_after.find_elements(By.TAG_NAME, "td")

        if action == "entrada":
            entry_after = cells_after[0].text.strip()
            if entry_after != entry_before:
                return CheckInResult(
                    True, action, f"✅ Fichaje de entrada registrado a las {entry_after}"
                )
        else:
            exit_after = cells_after[1].text.strip()
            if exit_after != exit_before:
                return CheckInResult(
                    True, action, f"✅ Fichaje de salida registrada a las {exit_after}"
                )

        return CheckInResult(False, action, "⚠️ No se confirmó el fichaje.")

    # -------------------------- DAILY RECORDS ------------------------------ #

    def get_today_records(self) -> list[dict[str, str]]:
        self._ensure_access_to(MAIN_PAGE_URL)

        table = self.wait.until(
            EC.presence_of_element_located((By.ID, "taboaMarcaxesPropios"))
        )

        rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
        result = []

        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 2:
                continue

            entry_value = cells[0].text.strip() or "-"
            exit_value = cells[1].text.strip() or "-"

            result.append({
                "entrada": entry_value,
                "salida": exit_value
            })

        return result

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
        """
        Return compact calendar entries relevant for the vacation viewer,
        using the same session-based authentication.
        """

        # Ensure we are authenticated and can access the calendar URL
        self._ensure_access_to(CALENDAR_URL)

        wait = self.wait

        # Wait for JS to populate window.calendario
        try:
            wait.until(
                lambda d: d.execute_script(
                    "return Array.isArray(window.calendario) && window.calendario.length >= 0;"
                ),
            )
        except TimeoutException as exc:
            raise CalendarFetchError(
                "No se pudo cargar el calendario en la página"
            ) from exc

        # Extract raw entries
        raw_entries = _read_calendar_array(self.driver)

        # Convert / filter entries
        simplified = list(_iter_relevant_entries(raw_entries))

        simplified.sort(key=lambda item: item.start)
        logger.info("Recovered %s calendar entries for the viewer", len(simplified))

        return [entry.as_payload() for entry in simplified]


    # ------------------------------ CLOSE ---------------------------------- #

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Backwards compatibility procedural API
# --------------------------------------------------------------------------- #

def perform_check_in(action: str) -> CheckInResult:
    session = UscWebSession()
    try:
        return session.perform_check_in(action)
    finally:
        session.close()


def get_today_records() -> list[dict[str, str]]:
    session = UscWebSession()
    try:
        return session.get_today_records()
    finally:
        session.close()

def fetch_calendar_summary() -> list[str]:
    session = UscWebSession()
    try:
        return session.fetch_calendar_summary()
    finally:
        session.close()
