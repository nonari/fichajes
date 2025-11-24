from __future__ import annotations

import time
from asyncio import InvalidStateError
from dataclasses import dataclass
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)

MAIN_PAGE_URL = "https://fichaxe.usc.gal/pas/marcaxesDiarias"


@dataclass
class CheckInResult:
    success: bool
    action: str
    message: str


def _get_last_row_cells(driver) -> list[Any]:
    table = driver.find_element(By.ID, "taboaMarcaxesPropios")
    rows = table.find_elements(By.TAG_NAME, "tr")
    last_row = rows[-1] if rows else None
    return last_row.find_elements(By.TAG_NAME, "td") if last_row else []


def perform_check_in(session, action: str) -> CheckInResult:
    action = action.lower().strip()
    if action not in {"entrada", "salida"}:
        raise ValueError("La acción de fichaje debe ser 'entrada' o 'salida'.")

    session._ensure_access_to(MAIN_PAGE_URL)

    session.wait.until(EC.presence_of_element_located((By.ID, "taboaMarcaxesPropios")))

    cells = _get_last_row_cells(session.driver)
    entry_before = cells[0].text.strip() if len(cells) > 0 else "-"
    exit_before = cells[1].text.strip() if len(cells) > 1 else "-"

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

    btn = session.driver.find_element(By.ID, "novaMarcaxe")
    btn.click()
    time.sleep(3)

    session.driver.refresh()
    table = session.wait.until(
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


def get_today_records(session) -> list[dict[str, str]]:
    session._ensure_access_to(MAIN_PAGE_URL)

    table = session.wait.until(
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
