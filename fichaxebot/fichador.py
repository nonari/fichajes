import time
from asyncio import InvalidStateError
from dataclasses import dataclass
from typing import Final

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger


@dataclass
class CheckInResult:
    """Represents the outcome of a check-in attempt."""

    success: bool
    action: str
    message: str


LOGIN_URL: Final[str] = "https://fichaxe.usc.gal/pas/marcaxesDiarias"

logger = get_logger(__name__)


def _create_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


def _login(driver: webdriver.Chrome, wait: WebDriverWait, user: str, password: str) -> None:
    driver.get(LOGIN_URL)
    logger.info("Login page loaded")

    user_input = wait.until(EC.presence_of_element_located((By.ID, "username-input")))
    pass_input = driver.find_element(By.ID, "password")
    user_input.send_keys(user)
    pass_input.send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    logger.info("Credentials submitted")

    wait.until(EC.presence_of_element_located((By.ID, "novaMarcaxe")))


def perform_check_in(action: str) -> CheckInResult:
    """Execute the requested check-in action if valid and return the outcome."""

    action = action.lower().strip()
    if action not in {"entrada", "salida"}:
        raise ValueError("La acción de fichaje debe ser 'entrada' o 'salida'.")

    logger.info("Starting check-in process for %s", action)

    config = get_config()
    user = config.usc_user
    password = config.usc_pass

    if not user or not password:
        raise ValueError("Las credenciales de USC no están configuradas correctamente")

    driver = _create_driver()
    wait = WebDriverWait(driver, 20)

    try:
        _login(driver, wait, user, password)

        table = driver.find_element(By.ID, "taboaMarcaxesPropios")
        rows = table.find_elements(By.TAG_NAME, "tr")

        last_row = rows[-1] if rows else None
        cells_before = last_row.find_elements(By.TAG_NAME, "td") if last_row else []
        entry_before = cells_before[0].text.strip() if len(cells_before) > 0 else "-"
        exit_before = cells_before[1].text.strip() if len(cells_before) > 1 else "-"

        if entry_before == "-":
            if exit_before == "-":
                allowed_action = "entrada"
            else:
                raise InvalidStateError()
        else:
            if exit_before == "-":
                allowed_action = "salida"
            else:
                allowed_action = "entrada"

        logger.info("Allowed action on the website: %s", allowed_action)

        if action != allowed_action:
            if allowed_action == "salida":
                message = (
                    "⚠️ Ya existe una entrada pendiente de cerrar. Marca la salida antes de "
                    "registrar una nueva entrada."
                )
            else:
                message = "⚠️ No hay una entrada pendiente para cerrar."
            logger.warning("Action '%s' not permitted at this time", action)
            return CheckInResult(False, action, message)

        # --- CLICK EN NOVA MARCAXE ---
        nova_btn = driver.find_element(By.ID, "novaMarcaxe")
        driver.execute_script("arguments[0].click();", nova_btn)
        logger.info("Click on 'novaMarcaxe' executed")
        time.sleep(5)

        # --- REFRESH AND VERIFY CHANGE ---
        driver.refresh()
        wait.until(EC.presence_of_element_located((By.ID, "taboaMarcaxesPropios")))
        table = driver.find_element(By.ID, "taboaMarcaxesPropios")
        rows_after = table.find_elements(By.TAG_NAME, "tr")
        last_row_after = rows_after[-1] if rows_after else None
        cells_after = (
            last_row_after.find_elements(By.TAG_NAME, "td") if last_row_after else []
        )

        if action == "entrada":
            entry_after = cells_after[0].text.strip() if len(cells_after) > 0 else "-"
            if entry_after and entry_after != entry_before:
                logger.info("Entry registered at %s", entry_after)
                return CheckInResult(
                    True, action, f"✅ Fichaje de entrada registrado a las {entry_after}"
                )

            if len(rows_after) > len(rows):
                logger.info("Entry detected in new row after performing the check-in")
                return CheckInResult(
                    True,
                    action,
                    f"✅ Fichaje de entrada registrado a las {entry_after or 'hora desconocida'}",
                )

            logger.warning("No entry time detected after attempting the check-in.")
            return CheckInResult(
                False,
                action,
                "⚠️ No se confirmó el fichaje de entrada (puede que ya estuviese registrado).",
            )

        exit_after = cells_after[1].text.strip() if len(cells_after) > 1 else "-"
        if exit_after != "-" and exit_after != exit_before:
            logger.info("Exit registered at %s", exit_after)
            return CheckInResult(
                True, action, f"✅ Fichaje de salida registrado a las {exit_after}"
            )

        logger.warning("No exit time detected after attempting the check-in.")
        return CheckInResult(
            False,
            action,
            "⚠️ No se confirmó el fichaje de salida (puede que ya estuviese registrado).",
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("Error during the check-in process")
        return CheckInResult(False, action, f"❌ Error en fichaje: {exc}")

    finally:
        driver.quit()


def get_today_records() -> list[dict[str, str]]:
    """Return the list of check-ins registered today (entry/exit)."""

    config = get_config()
    user = config.usc_user
    password = config.usc_pass

    if not user or not password:
        raise ValueError("Las credenciales de USC no están configuradas correctamente")

    driver = _create_driver()
    wait = WebDriverWait(driver, 20)

    try:
        _login(driver, wait, user, password)
        wait.until(EC.presence_of_element_located((By.ID, "taboaMarcaxesPropios")))
        table = driver.find_element(By.ID, "taboaMarcaxesPropios")
        rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")

        records: list[dict[str, str]] = []
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 2:
                continue
            entry_value = cells[0].text.strip()
            exit_value = cells[1].text.strip()
            if not entry_value and not exit_value:
                continue
            records.append(
                {
                    "entrada": entry_value or "-",
                    "salida": exit_value or "-",
                }
            )

        return records
    except Exception:  # noqa: BLE001
        logger.exception("Error while retrieving today's check-ins")
        raise
    finally:
        driver.quit()
