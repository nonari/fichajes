"""Read USC vacation allowances and prepare one full-day period per date."""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from fichaxebot.utils import get_madrid_now

REQUEST_URL = "https://fichaxe.usc.gal/pas/solicitude/0"
REQUEST_TYPE = "4"  # Vacacións, permisos e licenzas, from the USC form.


class VacationRequestError(ValueError):
    """A request cannot be prepared with the current USC data."""


def _number(value: Any) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(str(value).replace(",", "."))
        if not math.isfinite(number):
            raise ValueError
        return number
    except (TypeError, ValueError) as exc:
        raise VacationRequestError("USC devolvió un saldo de días inválido.") from exc


def build_catalog(form: dict, balances: list[dict], today: date) -> dict:
    """Use only years offered by USC; prior-year credit must be positive."""
    indexed = {(str(item["year"]), str(item["typeId"])): item for item in balances}
    years = []
    for year in (today.year, today.year - 1):
        if today >= date(year + 1, 3, 1):
            continue
        if str(year) not in {str(value) for value in form["years"]}:
            continue
        types = []
        for kind in form["types"]:
            item = indexed.get((str(year), str(kind["id"])))
            if item is None:
                raise VacationRequestError("No se pudieron consultar todos los saldos de USC.")
            types.append({
                "id": str(kind["id"]), "name": kind["name"],
                "remainingDays": _number(item.get("numeroDiasDisponhibles")),
                "remainingHours": _number(item.get("numeroHorasDisponhibles") or 0),
            })
        if year == today.year or any(kind["remainingDays"] > 0 for kind in types):
            years.append({"year": year, "types": types})
    return {"currentYear": today.year, "today": today.isoformat(), "years": years}


def _open_request_form(session) -> None:
    session._ensure_access_to(REQUEST_URL)
    element = session.wait.until(EC.element_to_be_clickable((By.ID, "idTipoSolicitude")))
    Select(element).select_by_value(REQUEST_TYPE)
    session.wait.until(lambda driver: driver.execute_script(
        "return document.querySelectorAll('#ano option[value]:not([value=\"\"])').length > 0"
        " && typeof engadirPeriodo === 'function';"
    ))


def fetch_vacation_catalog(session) -> dict:
    _open_request_form(session)
    form = session.driver.execute_script("""
        return {
            years: [...document.querySelectorAll('#ano option')].map(o => o.value).filter(Boolean),
            types: [...document.querySelectorAll('#idTipoVacacions option')]
                .filter(o => o.value).map(o => ({id: o.value, name: o.textContent.trim()})),
            applicant: document.getElementById('idSolicitante').value
        };
    """)
    today = get_madrid_now().date()
    queries = [
        {"year": str(year), "typeId": kind["id"]}
        for year in (today.year, today.year - 1)
        if str(year) in form["years"]
        for kind in form["types"]
    ]
    # Same read-only endpoint and parameters used by actualizarDias() in USC.
    result = session.driver.execute_async_script("""
        const [queries, applicant, done] = arguments;
        Promise.all(queries.map(async query => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 10000);
            try {
                const params = new URLSearchParams({
                    idTipoVacacions: query.typeId, idSolicitante: applicant, ano: query.year
                });
                const response = await fetch('/pas/obterNumeroDias?' + params, {
                    credentials: 'same-origin', signal: controller.signal
                });
                if (!response.ok || response.redirected) throw new Error('No se pudo consultar el saldo.');
                let balance = await response.json();
                if (typeof balance === 'string') balance = JSON.parse(balance);
                return {...balance, ...query};
            } finally { clearTimeout(timer); }
        })).then(balances => done({balances})).catch(() => done({error: true}));
    """, queries, form["applicant"])
    if not isinstance(result, dict) or result.get("error") or "balances" not in result:
        raise VacationRequestError("No se pudieron consultar los días disponibles en USC.")
    return build_catalog(form, result["balances"], today)


def validate_selection(data: dict, catalog: dict, entries: list[str], today: date) -> dict:
    """Validate untrusted Mini App input against server-side allowances/calendar."""
    year = data.get("year")
    if type(year) is not int or year not in (today.year, today.year - 1):
        raise VacationRequestError("Selecciona un año de saldo válido.")
    year_info = next((item for item in catalog["years"] if item["year"] == year), None)
    kind = next((item for item in (year_info or {}).get("types", [])
                 if item["id"] == data.get("vacationTypeId")), None)
    if kind is None:
        raise VacationRequestError("El año o tipo de vacaciones ya no está disponible en USC.")
    raw_days = data.get("days")
    if not isinstance(raw_days, list) or not raw_days or len(raw_days) > 100:
        raise VacationRequestError("Selecciona entre 1 y 100 días.")
    days = []
    for value in raw_days:
        try:
            day = date.fromisoformat(value)
            if day.isoformat() != value:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise VacationRequestError("Hay una fecha inválida en la selección.") from exc
        if day < today or day >= date(year + 1, 3, 1):
            raise VacationRequestError("Selecciona fechas desde hoy hasta finales de febrero del año siguiente al saldo.")
        days.append(value)
    if len(set(days)) != len(days):
        raise VacationRequestError("La selección contiene días repetidos.")
    if len(days) > kind["remainingDays"]:
        raise VacationRequestError(
            f"Solo quedan {kind['remainingDays']:g} días disponibles para este tipo y año."
        )
    for entry in entries:
        if entry[0] not in {"N", "V"}:
            continue
        bounds = entry[1:].split(":", 1)
        first, last = bounds[0], bounds[-1]
        if any(first <= day <= last for day in days):
            raise VacationRequestError("Hay días no laborables o vacaciones ya registradas en la selección.")
    return {"year": year, "vacationTypeId": kind["id"], "vacationTypeName": kind["name"],
            "days": sorted(days), "remainingDays": kind["remainingDays"]}


def fill_vacation_request(session, selection: dict) -> None:
    """Fill the first USC form; this function never clicks Seguinte or submits."""
    _open_request_form(session)
    year = session.driver.find_element(By.ID, "ano")
    year_value = str(selection["year"])
    if year.is_enabled():
        Select(year).select_by_value(year_value)
    elif year.get_attribute("value") != year_value:
        raise VacationRequestError("USC ya no permite seleccionar ese año.")
    Select(session.driver.find_element(By.ID, "idTipoVacacions")).select_by_value(
        selection["vacationTypeId"]
    )
    session.wait.until(lambda driver: driver.execute_script(
        "return !window.jQuery || jQuery.active === 0;"
    ))
    rows = session.driver.find_elements(By.CSS_SELECTOR, "#taboaPeriodos tbody tr.periodo")
    if len(rows) > 1:
        raise VacationRequestError("USC abrió un formulario con períodos existentes.")
    for index, value in enumerate(selection["days"]):
        if index >= len(rows):
            session.driver.find_element(By.ID, "engadePeriodo").click()
            session.wait.until(lambda driver: len(driver.find_elements(
                By.CSS_SELECTOR, "#taboaPeriodos tbody tr.periodo")) == index + 1)
            rows = session.driver.find_elements(By.CSS_SELECTOR, "#taboaPeriodos tbody tr.periodo")
        formatted = date.fromisoformat(value).strftime("%d/%m/%Y")
        for field in ("dataInicio", "dataFin"):
            element = rows[index].find_element(By.CSS_SELECTOR, f'input[name$=".{field}"]')
            session.driver.execute_script("""
                const [element, value] = arguments;
                const picker = window.jQuery && jQuery(element).data('DateTimePicker');
                if (picker) picker.date(value);
                else element.value = value;
                element.dispatchEvent(new Event('change', {bubbles: true}));
            """, element, formatted)
            if element.get_attribute("value") != formatted:
                raise VacationRequestError("No se pudo rellenar una fecha en USC.")
        for checkbox in rows[index].find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]'):
            if checkbox.is_selected():
                checkbox.click()


class VacationRequestUncertain(RuntimeError):
    """USC may have saved a change; check its state instead of repeating it."""


def _read_review(session) -> dict:
    session.wait.until(EC.presence_of_element_located((By.ID, "taboaPeriodos")))
    return session.driver.execute_script("""
        const field = label => {
            const node = [...document.querySelectorAll('p.h5')].find(p => p.textContent.trim() === label);
            return node?.nextElementSibling?.textContent.trim() || '';
        };
        return {
            state: field('Estado'), year: field('Ano'), requestType: field('Tipo de solicitude'),
            vacationTypeName: field('Tipo de vacacións, permisos e licenzas'),
            periods: [...document.querySelectorAll('#taboaPeriodos tbody tr')].map(row =>
                [...row.querySelectorAll('td')].slice(0, 4).map(cell => cell.textContent.trim())),
            canSubmit: !!document.querySelector('a[href$="/resumo/solicitar"]')
        };
    """)


def _verify_review(review: dict, selection: dict) -> None:
    expected = sorted(date.fromisoformat(day).strftime("%d/%m/%Y") for day in selection["days"])
    periods = review.get("periods", [])
    if (review.get("year") != str(selection["year"])
            or review.get("requestType") != "Vacacións, permisos e licenzas"
            or review.get("vacationTypeName") != selection["vacationTypeName"]
            or sorted(row[0] for row in periods if len(row) >= 2) != expected
            or any(len(row) < 2 or row[0] != row[1]
                   or any(value not in ("", "-") for value in row[2:4]) for row in periods)):
        raise VacationRequestError("El resumen de USC no coincide con el año, tipo o fechas seleccionados.")


def save_vacation_draft(session, selection: dict) -> dict:
    """Seguinte saves a draft and redirects to /solicitude/{id}/resumo."""
    import re
    from urllib.parse import urlsplit
    from selenium.common.exceptions import TimeoutException

    form = session.driver.find_element(By.ID, "formularioSolicitude")
    session.driver.find_element(By.ID, "seguinte").click()
    try:
        session.wait.until(EC.staleness_of(form))
        session.wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete")
        parts = urlsplit(session.driver.current_url)
        match = re.fullmatch(r"/pas/solicitude/([1-9]\d*)/resumo/?", parts.path)
        if not match:
            errors = [element.text.strip() for element in session.driver.find_elements(
                By.CSS_SELECTOR, '.fielderrloc, .alert-danger') if element.is_displayed() and element.text.strip()]
            if errors and session.driver.find_elements(By.ID, "formularioSolicitude"):
                raise VacationRequestError("USC no aceptó las fechas: " + " · ".join(errors))
            raise VacationRequestUncertain("No se pudo confirmar el borrador. Revisa las solicitudes en USC antes de repetirlo.")
        review = _read_review(session)
        _verify_review(review, selection)
        if review["state"].casefold() != "borrador" or not review["canSubmit"]:
            raise VacationRequestUncertain("USC no mostró el borrador esperado. Revisa la solicitud en USC.")
        return {**selection, "id": match.group(1),
                "reviewUrl": f"https://fichaxe.usc.gal/pas/solicitude/{match.group(1)}/resumo"}
    except TimeoutException as exc:
        raise VacationRequestUncertain("USC no confirmó si guardó el borrador. Revisa las solicitudes antes de repetirlo.") from exc


def submit_vacation_draft(session, draft: dict) -> str:
    """Only called after the user presses the bot's final Solicitar button."""
    from selenium.common.exceptions import TimeoutException

    session._ensure_access_to(draft["reviewUrl"])
    review = _read_review(session)
    _verify_review(review, draft)
    if not review["state"]:
        raise VacationRequestError("No se pudo leer el estado de la solicitud en USC.")
    if review["state"].casefold() != "borrador":
        return review["state"]  # Already processed: never click Solicitar again.
    if not review["canSubmit"]:
        raise VacationRequestError("USC no permite solicitar este borrador.")
    link = session.driver.find_element(By.CSS_SELECTOR, 'a[href$="/resumo/solicitar"]')
    link.click()
    try:
        session.wait.until(EC.staleness_of(link))
    except TimeoutException:
        pass  # Read the saved state; do not repeat the action on a timeout.
    try:
        session._ensure_access_to(draft["reviewUrl"])
        updated = _read_review(session)
        _verify_review(updated, draft)
        if not updated["state"] or updated["state"].casefold() == "borrador":
            raise VacationRequestUncertain("USC no confirmó el envío. Revisa el borrador antes de volver a solicitarlo.")
        return updated["state"]
    except TimeoutException as exc:
        raise VacationRequestUncertain("No se pudo comprobar el estado tras solicitar. Revisa la solicitud en USC.") from exc
