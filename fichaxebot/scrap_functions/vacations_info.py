from __future__ import annotations

import re
from typing import Final

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

VACATIONS_URL_TEMPLATE: Final[str] = (
    "https://fichaxe.usc.gal/pas/persoa/{person_id}/vacacionsPermisosLicenzas"
)


class VacationsInfoError(Exception):
    """Raised when the vacations table cannot be retrieved or parsed."""


def _extract_int(value: str) -> int:
    digits = re.findall(r"-?\d+", value)
    if not digits:
        return 0
    return int(digits[0])


def fetch_vacations_info(session) -> tuple[list[str], list[str], list[list[int]]]:
    """
    Retrieve vacations table as (columns, row_names, rows) tuple.

    Each row in ``rows`` contains numeric values associated with the corresponding
    entry in ``row_names``.
    """

    person_id = getattr(session, "internal_user_id", "")
    if not person_id:
        raise VacationsInfoError(
            "No se pudo descubrir el identificador interno del usuario en USC."
        )

    url = VACATIONS_URL_TEMPLATE.format(person_id=person_id)
    session._ensure_access_to(url)

    try:
        table = session.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.table"))
        )
    except TimeoutException as exc:  # pragma: no cover - requires live session
        raise VacationsInfoError(
            "No se pudo encontrar la tabla de vacaciones en la página."
        ) from exc

    headers = [
        th.text.strip()
        for th in table.find_elements(By.CSS_SELECTOR, "thead th")
        if th.text.strip()
    ]

    row_names: list[str] = []
    rows: list[list[int]] = []

    for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        if not cells:
            continue

        row_names.append(cells[0].text.strip())
        numeric_cells = [_extract_int(cell.text) for cell in cells[1:]]
        rows.append(numeric_cells)

    if not headers or not row_names:
        raise VacationsInfoError("La tabla de vacaciones no contiene datos válidos.")

    return headers, row_names, rows
