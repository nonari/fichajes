"""USC authorized absences: catalog, validation, uploads and one-shot submission."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from urllib.parse import urlsplit

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from fichaxebot.scrap_functions.vacation_request import REQUEST_URL, _capture_full_page
from fichaxebot.utils import get_madrid_now

MAX_FILE_BYTES = 1048576
MAX_ATTACHMENTS = 10
REQUEST_TYPE = "5"
REQUEST_NAME = "Ausencias autorizadas"


class AbsenceRequestError(ValueError):
    """The final submission has not been attempted."""


class AbsenceRequestCancelled(RuntimeError):
    """Confirmation did not authorize the final submission."""


class AbsenceRequestUncertain(RuntimeError):
    """The final action was attempted; inspect USC before retrying."""


def _open_form(session):
    session._ensure_access_to(REQUEST_URL)
    Select(session.wait.until(EC.element_to_be_clickable((By.ID, "idTipoSolicitude")))).select_by_value(REQUEST_TYPE)
    session.wait.until(lambda driver: driver.execute_script("""
        return document.querySelectorAll('#ano option[value]:not([value=""])').length > 0
            && typeof engadirPeriodo === 'function';
    """))


def fetch_absence_catalog(session) -> dict:
    _open_form(session)
    form = session.driver.execute_script("""
        return {
            years: [...document.querySelectorAll('#ano option')].map(o => o.value).filter(Boolean),
            types: [...document.querySelectorAll('#idTipoAusencia option')]
                .filter(o => o.value).map(o => ({id: o.value, name: o.textContent.trim()}))
        };
    """)
    result = session.driver.execute_async_script("""
        const [types, done] = arguments;
        Promise.all(types.map(async type => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 10000);
            try {
                const response = await fetch('/pas/podeFraccionarPeriodoTipoAusencia?' +
                    new URLSearchParams({idTipoAusencia: type.id}),
                    {credentials: 'same-origin', signal: controller.signal});
                if (!response.ok || response.redirected) throw new Error('catalog');
                let value = await response.json();
                if (typeof value === 'string') value = JSON.parse(value);
                if (typeof value.isFraccionamento !== 'boolean') throw new Error('hours');
                return {...type, requiresHours: value.isFraccionamento};
            } finally { clearTimeout(timer); }
        })).then(types => done({types})).catch(() => done({error: true}));
    """, form["types"])
    if not isinstance(result, dict) or result.get("error") or not result.get("types"):
        raise AbsenceRequestError("No se pudieron consultar los tipos de ausencia y sus horarios en USC.")
    try:
        years = [int(year) for year in form["years"]]
        if not years or any(year < 1 or year > 9998 for year in years):
            raise ValueError
        if any(type(kind.get("requiresHours")) is not bool for kind in result["types"]):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise AbsenceRequestError("USC devolvió un catálogo de ausencias inválido.") from exc
    return {"years": years, "types": result["types"], "today": get_madrid_now().date().isoformat()}


def validate_pdf(path) -> str:
    try:
        file = Path(path).resolve(strict=True)
        if not file.is_file() or file.suffix.lower() != ".pdf" or not 0 < file.stat().st_size <= MAX_FILE_BYTES:
            raise ValueError
        with file.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError
        return str(file)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        raise AbsenceRequestError("Adjunta un PDF legible, no vacío y de como máximo 1 MiB.") from exc


def validate_selection(data: dict, catalog: dict) -> dict:
    if not isinstance(data, dict):
        raise AbsenceRequestError("La solicitud no tiene un formato válido.")
    year = data.get("year")
    if type(year) is not int or year not in catalog["years"]:
        raise AbsenceRequestError("Selecciona un año disponible en USC.")
    kind = next((kind for kind in catalog["types"] if kind["id"] == data.get("absenceTypeId")), None)
    if kind is None:
        raise AbsenceRequestError("El tipo de ausencia ya no está disponible en USC.")
    raw = data.get("periods")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise AbsenceRequestError("Selecciona entre 1 y 100 días.")
    periods = []
    for period in raw:
        try:
            value = period["date"]
            day = date.fromisoformat(value)
            if day.isoformat() != value or day.year != year:
                raise ValueError
        except (TypeError, KeyError, ValueError) as exc:
            raise AbsenceRequestError("Cada fecha debe ser válida y pertenecer al año seleccionado.") from exc
        item = {"date": value}
        if kind["requiresHours"]:
            start, end = period.get("startTime"), period.get("endTime")
            if (not isinstance(start, str) or not isinstance(end, str)
                    or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start)
                    or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end) or start >= end):
                raise AbsenceRequestError("Indica horas HH:MM válidas, con el inicio anterior al fin, para cada día.")
            item.update(startTime=start, endTime=end)
        elif period.get("startTime") or period.get("endTime"):
            raise AbsenceRequestError("Este tipo de ausencia se solicita por días completos.")
        periods.append(item)
    if len({item["date"] for item in periods}) != len(periods):
        raise AbsenceRequestError("La solicitud contiene días repetidos.")
    observations = data.get("observations", "")
    if not isinstance(observations, str):
        raise AbsenceRequestError("Las observaciones deben ser texto.")
    attachments = data.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS:
        raise AbsenceRequestError("Se permiten como máximo diez documentos PDF.")
    return {"year": year, "absenceTypeId": kind["id"], "absenceTypeName": kind["name"],
            "requiresHours": kind["requiresHours"], "periods": sorted(periods, key=lambda item: item["date"]),
            "observations": observations.strip(), "attachments": [validate_pdf(path) for path in attachments]}


def _set_value(session, element, value):
    session.driver.execute_script("""
        const [element, value] = arguments;
        const picker = window.jQuery && jQuery(element).data('DateTimePicker');
        if (picker) picker.date(value); else element.value = value;
        element.dispatchEvent(new Event('change', {bubbles: true}));
    """, element, value)
    if element.get_attribute("value") != value:
        raise AbsenceRequestError("No se pudo rellenar un campo de la solicitud en USC.")


def fill_absence_request(session, selection):
    _open_form(session)
    year = session.driver.find_element(By.ID, "ano")
    if year.is_enabled():
        Select(year).select_by_value(str(selection["year"]))
    elif year.get_attribute("value") != str(selection["year"]):
        raise AbsenceRequestError("USC ya no permite seleccionar ese año.")
    Select(session.driver.find_element(By.ID, "idTipoAusencia")).select_by_value(selection["absenceTypeId"])
    session.wait.until(lambda driver: driver.execute_script("return !window.jQuery || jQuery.active === 0;"))
    actual_hours = session.driver.execute_script("return window.fraccionamento;")
    if actual_hours is not selection["requiresHours"]:
        raise AbsenceRequestError("USC cambió los requisitos horarios. Abre una nueva solicitud.")
    rows = session.driver.find_elements(By.CSS_SELECTOR, "#taboaPeriodos tbody tr.periodo")
    if rows:
        raise AbsenceRequestError("USC abrió un formulario con períodos existentes.")
    for index, period in enumerate(selection["periods"]):
        session.driver.find_element(By.ID, "engadePeriodo").click()
        session.wait.until(lambda driver: len(driver.find_elements(
            By.CSS_SELECTOR, "#taboaPeriodos tbody tr.periodo")) == index + 1)
        row = session.driver.find_elements(By.CSS_SELECTOR, "#taboaPeriodos tbody tr.periodo")[index]
        formatted = date.fromisoformat(period["date"]).strftime("%d/%m/%Y")
        for field in ("dataInicio", "dataFin"):
            _set_value(session, row.find_element(By.CSS_SELECTOR, f'input[name$=".{field}"]'), formatted)
        checkbox = row.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
        if checkbox.is_selected() != selection["requiresHours"]:
            checkbox.click()
        if selection["requiresHours"]:
            for field, key in (("horaInicio", "startTime"), ("horaFin", "endTime")):
                _set_value(session, row.find_element(By.CSS_SELECTOR, f'input[name$=".{field}"]'), period[key])
    _set_value(session, session.driver.find_element(By.ID, "observacions"), selection["observations"])
    if session.driver.find_elements(By.CSS_SELECTOR, '#taboaXustificantes input[type="file"]'):
        raise AbsenceRequestError("USC abrió un formulario con documentos existentes.")
    for index, path in enumerate(selection["attachments"]):
        session.driver.find_element(By.ID, "engadeXustificante").click()
        files = session.wait.until(lambda driver: (items if len(items := driver.find_elements(
            By.CSS_SELECTOR, '#taboaXustificantes input[type="file"]')) == index + 1 else False))
        files[index].send_keys(path)
        if not session.driver.execute_script(
            "return arguments[0].files.length === 1 && arguments[0].files[0].name === arguments[1]",
            files[index], Path(path).name,
        ):
            raise AbsenceRequestError("No se pudo adjuntar un documento a la solicitud.")


def _read_review(session):
    table = session.wait.until(EC.presence_of_element_located((By.XPATH,
        "//table[thead/tr/th[normalize-space(.)='Dende'] and thead/tr/th[normalize-space(.)='Ata']]")))
    return session.driver.execute_script("""
        const field = label => {
            const node = [...document.querySelectorAll('p.h5')].find(p => p.textContent.trim() === label);
            return node?.nextElementSibling?.textContent.trim() || '';
        };
        const legend = [...document.querySelectorAll('legend')]
            .find(el => el.textContent.trim() === 'Documentos xustificativos');
        const files = legend?.parentElement.querySelector('table');
        return {
            year: field('Ano'), state: field('Estado'), requestType: field('Tipo de solicitude'),
            absenceTypeName: field('Tipo de ausencia'), observations: field('Observacións'),
            headers: [...arguments[0].querySelectorAll('thead th')].map(el => el.textContent.trim()),
            periods: [...arguments[0].querySelectorAll('tbody tr')].map(row =>
                [...row.querySelectorAll('td')].map(cell => cell.textContent.trim())),
            attachments: files ? [...files.querySelectorAll('tbody tr')].map(row =>
                row.querySelector('a')?.textContent.trim() || row.querySelector('td')?.textContent.trim() || '') : [],
            canSubmit: !!document.querySelector('a[href$="/resumo/solicitar"]')
        };
    """, table)


def verify_review(review, selection):
    normalize = lambda text: " ".join(text.split())
    expected_files = sorted(Path(path).name for path in selection["attachments"])
    valid = (review.get("year") == str(selection["year"])
             and review.get("requestType") == REQUEST_NAME
             and review.get("absenceTypeName") == selection["absenceTypeName"]
             and normalize(review.get("observations", "")) == normalize(selection["observations"])
             and sorted(review.get("attachments", [])) == expected_files)
    try:
        headers = review["headers"]
        start_index, end_index = headers.index("Dende"), headers.index("Ata")
        actual = []
        for row in review["periods"]:
            start, end = row[start_index], row[end_index]
            if start != end:
                raise ValueError
            record = [start]
            if selection["requiresHours"]:
                record += [row[headers.index("Hora de inicio")], row[headers.index("Hora de fin")]]
            else:
                for heading in ("Hora de inicio", "Hora de fin"):
                    if heading in headers and row[headers.index(heading)] not in ("", "-", "—"):
                        raise ValueError
            actual.append(record)
        expected = [[date.fromisoformat(period["date"]).strftime("%d/%m/%Y")]
                    + ([period["startTime"], period["endTime"]] if selection["requiresHours"] else [])
                    for period in selection["periods"]]
        valid = valid and sorted(actual) == sorted(expected)
    except (KeyError, ValueError, IndexError, TypeError):
        valid = False
    if not valid:
        raise AbsenceRequestError("El resumen de USC no coincide con el tipo, fechas, horas, observaciones o documentos.")


def submit_absence_request(session, selection, confirm=None):
    form = session.driver.find_element(By.ID, "formularioSolicitude")
    session.driver.find_element(By.ID, "seguinte").click()
    try:
        session.wait.until(EC.staleness_of(form))
        session.wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete")
        review = _read_review(session)
    except TimeoutException as exc:
        errors = [element.text.strip() for element in session.driver.find_elements(
            By.CSS_SELECTOR, '.fielderrloc, .alert-danger') if element.is_displayed() and element.text.strip()]
        raise AbsenceRequestError(" · ".join(errors) or "No se pudo verificar el resumen. No se envió la solicitud.") from exc
    verify_review(review, selection)
    if review["state"].casefold() != "borrador" or not review["canSubmit"]:
        raise AbsenceRequestError("USC no permite enviar la solicitud desde este paso.")
    link = session.driver.find_element(By.CSS_SELECTOR, 'a[href$="/resumo/solicitar"]')
    action_url, review_url = link.get_attribute("href"), session.driver.current_url
    match = re.fullmatch(r"/pas/solicitude/([1-9]\d*)/resumo/solicitar", urlsplit(action_url).path)
    if not match or urlsplit(action_url).netloc != urlsplit(REQUEST_URL).netloc:
        raise AbsenceRequestError("No se pudo identificar la acción de envío de USC.")
    if confirm is not None:
        try:
            screenshot = _capture_full_page(session)
        except Exception as exc:
            raise AbsenceRequestError("No se pudo capturar el resumen. No se envió la solicitud.") from exc
        try:
            accepted = confirm(screenshot)
        except Exception as exc:
            raise AbsenceRequestCancelled("La confirmación falló. No se envió la solicitud.") from exc
        if accepted is not True:
            raise AbsenceRequestCancelled("Solicitud cancelada. No se envió a USC.")
        review = _read_review(session)
        verify_review(review, selection)
        link = session.driver.find_element(By.CSS_SELECTOR, 'a[href$="/resumo/solicitar"]')
        if (session.driver.current_url != review_url or link.get_attribute("href") != action_url
                or review["state"].casefold() != "borrador" or not review["canSubmit"]):
            raise AbsenceRequestError("El resumen cambió durante la confirmación. No se envió la solicitud.")
    try:
        link.click()
        try:
            session.wait.until(EC.staleness_of(link))
        except TimeoutException:
            pass
        session.wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete")
        updated = _read_review(session)
        verify_review(updated, selection)
        receipt_id = re.match(r"/pas/solicitude/([1-9]\d*)(?:/|$)", urlsplit(session.driver.current_url).path)
        if (updated["state"].casefold() != "solicitada" or updated["canSubmit"]
                or (receipt_id and receipt_id.group(1) != match.group(1))):
            raise AbsenceRequestUncertain("USC no confirmó el envío. Comprueba tus solicitudes antes de repetirlo.")
        # Do not leak local filesystem paths into user-facing receipts.
        return {**{key: value for key, value in selection.items() if key != "attachments"},
                "attachments": [Path(path).name for path in selection["attachments"]],
                "id": match.group(1), "state": updated["state"]}
    except (WebDriverException, AbsenceRequestError) as exc:
        raise AbsenceRequestUncertain("No se pudo confirmar el envío. Comprueba USC antes de repetirlo.") from exc
