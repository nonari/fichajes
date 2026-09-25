"""Read USC vacation allowances and prepare one full-day period per date."""
from __future__ import annotations

import base64
import math
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from selenium.common.exceptions import TimeoutException, WebDriverException
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
    """Submission may have reached USC; check its state instead of repeating it."""


def _read_review(session) -> dict:
    # The summary uses an unlabelled table, unlike the editable first form.
    table = session.wait.until(EC.presence_of_element_located((By.XPATH,
        "//table[thead/tr/th[normalize-space(.)='Dende']"
        " and thead/tr/th[normalize-space(.)='Ata']"
        " and thead/tr/th[normalize-space(.)='Número de días']]"
    )))
    return session.driver.execute_script("""
        const field = label => {
            const node = [...document.querySelectorAll('p.h5')].find(p => p.textContent.trim() === label);
            return node?.nextElementSibling?.textContent.trim() || '';
        };
        return {
            state: field('Estado'), year: field('Ano'), requestType: field('Tipo de solicitude'),
            vacationTypeName: field('Tipo de vacacións, permisos e licenzas'),
            periods: [...arguments[0].querySelectorAll('tbody tr')].map(row =>
                [...row.querySelectorAll('td')].map(cell => cell.textContent.trim())),
            canSubmit: !!document.querySelector('a[href$="/resumo/solicitar"]')
        };
    """, table)


def _verify_review(review: dict, selection: dict) -> None:
    expected = sorted(date.fromisoformat(day).strftime("%d/%m/%Y") for day in selection["days"])
    periods = review.get("periods", [])
    if (review.get("year") != str(selection["year"])
            or review.get("requestType") != "Vacacións, permisos e licenzas"
            or review.get("vacationTypeName") != selection["vacationTypeName"]
            or sorted(row[0] for row in periods if len(row) == 3) != expected
            or any(len(row) != 3 or row[0] != row[1] or _number(row[2]) != 1 for row in periods)):
        raise VacationRequestError("El resumen de USC no coincide con el año, tipo o fechas seleccionados.")


class VacationRequestCancelled(RuntimeError):
    """The transient request was abandoned before the final USC action."""


def _capture_full_page(session) -> bytes:
    size = session.driver.execute_cdp_cmd("Page.getLayoutMetrics", {})["cssContentSize"]
    screenshot = session.driver.execute_cdp_cmd("Page.captureScreenshot", {
        "format": "png", "captureBeyondViewport": True,
        "clip": {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1},
    })
    return base64.b64decode(screenshot["data"], validate=True)


def submit_vacation_request(session, selection: dict, confirm=None) -> dict:
    """Advance and submit the filled wizard in place, without reopening its pages."""
    form = session.driver.find_element(By.ID, "formularioSolicitude")
    session.driver.find_element(By.ID, "seguinte").click()
    try:
        session.wait.until(EC.staleness_of(form))
        session.wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete")
        review = _read_review(session)
    except TimeoutException as exc:
        errors = [element.text.strip() for element in session.driver.find_elements(
            By.CSS_SELECTOR, '.fielderrloc, .alert-danger') if element.is_displayed() and element.text.strip()]
        detail = " · ".join(errors) or "No se pudo verificar el resumen de USC. No se ha enviado la solicitud."
        raise VacationRequestError(detail) from exc
    _verify_review(review, selection)
    if review["state"].casefold() != "borrador" or not review["canSubmit"]:
        raise VacationRequestError("USC no permite enviar la solicitud desde este paso.")
    link = session.driver.find_element(By.CSS_SELECTOR, 'a[href$="/resumo/solicitar"]')
    match = re.fullmatch(r"/pas/solicitude/([1-9]\d*)/resumo/solicitar", urlsplit(link.get_attribute("href")).path)
    if not match:
        raise VacationRequestError("No se pudo identificar la acción de envío de USC.")
    if confirm is not None:
        review_url = session.driver.current_url
        action_url = link.get_attribute("href")
        try:
            screenshot = _capture_full_page(session)
        except Exception as exc:
            raise VacationRequestError("No se pudo capturar el resumen. No se envió la solicitud.") from exc
        if not confirm(screenshot):
            raise VacationRequestCancelled("Solicitud cancelada. No se envió a USC.")
        # Inspect the same live wizard, without navigating or retrying the request.
        try:
            review = _read_review(session)
            _verify_review(review, selection)
            link = session.driver.find_element(By.CSS_SELECTOR, 'a[href$="/resumo/solicitar"]')
            if (session.driver.current_url != review_url
                    or link.get_attribute("href") != action_url
                    or review["state"].casefold() != "borrador" or not review["canSubmit"]):
                raise VacationRequestError("El resumen de USC cambió durante la confirmación. No se envió la solicitud.")
        except WebDriverException as exc:
            raise VacationRequestError("El resumen de USC ya no está disponible. No se envió la solicitud.") from exc
    try:
        # From this point a browser error may occur after USC accepted the action.
        link.click()
        try:
            session.wait.until(EC.staleness_of(link))
        except TimeoutException:
            pass  # Inspect the current page, but never repeat the click or reload.
        session.wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete")
        updated = _read_review(session)
        _verify_review(updated, selection)
        if not updated["state"] or updated["state"].casefold() == "borrador" or updated["canSubmit"]:
            raise VacationRequestUncertain("USC no confirmó el envío. Comprueba tus solicitudes antes de repetirlo.")
        return {**selection, "id": match.group(1), "state": updated["state"]}
    except (WebDriverException, VacationRequestError) as exc:
        raise VacationRequestUncertain("No se pudo confirmar el envío. Comprueba tus solicitudes en USC antes de repetirlo.") from exc
