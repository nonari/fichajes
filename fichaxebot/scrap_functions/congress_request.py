"""USC congress permission wizard. Receipt verification is deliberately pending."""
import base64
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.commit import commit_click
from fichaxebot.utils import get_madrid_now

logger = get_logger(__name__)
REQUEST_URL = 'https://aplicacions.usc.es/intranet/solicitudes/RRHH_InvAsistenciaCongresos.htm'
EMPLOYMENT_CATEGORIES = frozenset({
    'Proxectos', 'JIN', 'MARIECURIE', 'PREDOUTORAIS', 'POSDOUTORAIS',
    'BeatrizGalindo', 'DISTINGUIEOD', 'JUANDELACIERVA', 'RAMONYCAJAL', 'TECNICOAPOIO',
})


class CongressRequestError(ValueError):
    """The request could not reach submission; the final action was not attempted."""


class CongressRequestCancelled(RuntimeError):
    """The caller declined confirmation or could not complete it; not submitted."""


def _text(data, key, *, required=True, limit=None, multiline=False):
    value = data.get(key, '')
    if not isinstance(value, str) or (required and not value.strip()):
        raise CongressRequestError(f'El campo {key} debe contener texto válido.')
    value = value.strip()
    if any('\ue000' <= char <= '\ue05d' or ord(char) < 32 and (char != '\n' or not multiline)
           for char in value):
        raise CongressRequestError(f'El campo {key} contiene teclas de control no admitidas.')
    if limit is not None and len(value) > limit:
        raise CongressRequestError(f'El campo {key} admite como máximo {limit} caracteres.')
    return value


def validate_request(data: dict, today: date) -> dict:
    """Copy and validate caller data before opening or modifying the USC wizard."""
    if not isinstance(data, dict) or data.get('data_processing_authorized') is not True:
        raise CongressRequestError('Es necesario autorizar el tratamiento de los datos.')
    address = data.get('address')
    contact = data.get('contact', {})
    if not isinstance(address, dict) or not isinstance(contact, dict):
        raise CongressRequestError('La dirección y los datos de contacto deben ser objetos.')
    result = {'data_processing_authorized': True}
    result['contact'] = {key: _text(contact, key, required=False, limit=150)
                         for key in ('email', 'phone') if key in contact}
    result['address'] = {
        key: _text(address, key, required=required, limit=limit, multiline=key == 'observations')
        for key, required, limit in (
            ('country', True, None), ('province', False, None), ('municipality', False, None),
            ('department', False, 255), ('postal_code', True, 50), ('line1', True, 255),
            ('line2', False, 255), ('observations', False, 512),
        )
    }
    for key in ('reason', 'organization', 'employment_category', 'supervisor_query'):
        result[key] = _text(data, key, multiline=key == 'reason')
    if result['employment_category'] not in EMPLOYMENT_CATEGORIES:
        raise CongressRequestError('Selecciona una categoría de contrato válida.')
    if len(result['supervisor_query']) < 3:
        raise CongressRequestError('Indica al menos tres caracteres para buscar al supervisor.')
    teaching = data.get('teaching_assigned')
    if type(teaching) is not bool:
        raise CongressRequestError('teaching_assigned debe ser true o false.')
    result['teaching_assigned'] = teaching
    result['teaching_cover'] = _text(data, 'teaching_cover', multiline=True) if teaching else '-'
    try:
        start = date.fromisoformat(_text(data, 'start_date'))
        end = date.fromisoformat(_text(data, 'end_date'))
    except ValueError as exc:
        raise CongressRequestError('Las fechas deben tener formato YYYY-MM-DD.') from exc
    if start < today + timedelta(days=5) or end < start:
        raise CongressRequestError('USC requiere al menos cinco días de antelación y una fecha final no anterior a la inicial.')
    result.update(start_date=start.isoformat(), end_date=end.isoformat())
    attachments = data.get('attachments', [])
    if not isinstance(attachments, list) or len(attachments) > 10:
        raise CongressRequestError('Se pueden adjuntar hasta diez documentos.')
    result['attachments'] = []
    for item in attachments:
        if not isinstance(item, dict):
            raise CongressRequestError('Cada anexo necesita título y ruta de archivo.')
        title = _text(item, 'title', limit=50)
        try:
            path = Path(item['path']).expanduser().resolve(strict=True)
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('empty or non-file')
            with path.open('rb') as source:
                source.read(1)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise CongressRequestError('Cada anexo debe ser un archivo legible y no vacío.') from exc
        result['attachments'].append({'title': title, 'path': str(path)})
    return result


def _field(session, selector):
    return session.driver.find_element(By.CSS_SELECTOR, selector)


def _fill(session, selector, value):
    field = _field(session, selector)
    field.clear()
    field.send_keys(value)
    if field.get_attribute('value').strip() != value.strip():
        raise CongressRequestError('USC no conservó el valor de uno de los campos.')


def _next_button(session):
    return _field(session, '#formulario button[name="_eventId_seguinte"]')


def _advance(session, next_selector):
    form = _field(session, '#formulario')
    _next_button(session).click()
    try:
        session.wait.until(EC.staleness_of(form))
        session.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, next_selector)))
    except TimeoutException as exc:
        errors = [node.text.strip() for node in session.driver.find_elements(
            By.CSS_SELECTOR, '.alert-danger, #listaErros li, #ulErros li, #dialogoMostrarAvisos.show .modal-body',
        ) if node.is_displayed() and node.text.strip()]
        raise CongressRequestError(' · '.join(errors) or 'USC no avanzó al siguiente paso. No se envió la solicitud.') from exc


def _select_text(session, selector, text):
    select = Select(_field(session, selector))
    matches = [option for option in select.options
               if ' '.join(option.text.split()).casefold() == ' '.join(text.split()).casefold()
               and option.get_attribute('value') not in ('', '0')]
    if len(matches) != 1:
        raise CongressRequestError(f'USC no ofrece una opción única para {text!r}.')
    changed = not matches[0].is_selected()
    select.select_by_value(matches[0].get_attribute('value'))
    return changed


def _fill_address(session, address):
    old_province = _field(session, '#idProvincia')
    old_town = _field(session, '#idConcello')
    changed = _select_text(session, '#idPais', address['country'])
    country = Select(_field(session, '#idPais')).first_selected_option
    if changed:
        # USC replaces both dependent lists, including an automatic town lookup.
        session.wait.until(EC.staleness_of(old_province))
        session.wait.until(EC.staleness_of(old_town))
    if country.get_attribute('data-codigo') == '108':
        if not address['province'] or not address['municipality']:
            raise CongressRequestError('La dirección española requiere provincia y municipio.')
        old_town = _field(session, '#idConcello')
        if _select_text(session, '#idProvincia', address['province']):
            session.wait.until(EC.staleness_of(old_town))
        _select_text(session, '#idConcello', address['municipality'])
    else:
        if not address['department']:
            raise CongressRequestError('La dirección extranjera requiere departamento.')
        _fill(session, 'input[name="departamento"]', address['department'])
    for name, key in (('codigoPostal', 'postal_code'), ('enderezoLina1', 'line1'),
                      ('enderezoLina2', 'line2'), ('observacions', 'observations')):
        _fill(session, f'[name="{name}"]', address[key])


def _detail_selector(index, kind='nome'):
    # Generic field IDs are duplicated in USC HTML. Match the unique name suffix.
    return f'[name$=".datosXenericos0.{index}].{kind}"]'


def _set_date(session, index, iso_date):
    value = date.fromisoformat(iso_date).strftime('%d/%m/%Y')
    error = session.driver.execute_script("""
        const input = arguments[0], value = arguments[1], picker = jQuery(input);
        const parse = v => v instanceof Date ? v : jQuery.datepicker.parseDate('dd/mm/yy', v);
        const chosen = parse(value), min = picker.datepicker('option', 'minDate'),
              max = picker.datepicker('option', 'maxDate');
        if ((min && chosen < parse(min)) || (max && chosen > parse(max))) return 'date outside USC limits';
        picker.datepicker('setDate', value);
        const onClose = picker.datepicker('option', 'onClose');
        if (onClose) onClose.call(input, value);
        picker.trigger('change');
        return input.value === value ? '' : 'date not accepted';
    """, _field(session, _detail_selector(index)), value)
    if error:
        raise CongressRequestError('Las fechas no cumplen las restricciones actuales de USC.')


def _fill_details(session, data):
    _fill(session, _detail_selector(0), data['reason'])
    _fill(session, _detail_selector(1), data['organization'])
    _field(session, _detail_selector(3, 'codigo') + f'[value="{data["employment_category"]}"]').click()
    _field(session, _detail_selector(5, 'codigo') + f'[value="{int(data["teaching_assigned"])}"]').click()
    if data['teaching_assigned']:
        _fill(session, _detail_selector(6), data['teaching_cover'])
    _set_date(session, 8, data['start_date'])
    _set_date(session, 9, data['end_date'])


def _fill_supervisor(session, query):
    _fill(session, 'input[name="asinantes[0].nome"]', query)
    def suggestions(driver):
        field = driver.find_element(By.ID, 'autoCompletarNome0')
        if 'ui-autocomplete-loading' in (field.get_attribute('class') or ''):
            return False
        return [node for node in driver.find_elements(By.CSS_SELECTOR, '.ui-autocomplete .ui-menu-item')
                if node.is_displayed()]
    options = session.wait.until(suggestions)
    exact = [node for node in options if ' '.join(node.text.split()).casefold() == ' '.join(query.split()).casefold()]
    choices = exact or options
    if len(choices) != 1:
        raise CongressRequestError('La búsqueda del supervisor es ambigua. Usa su nombre completo o documento.')
    choices[0].click()
    session.wait.until(lambda driver: driver.find_element(By.ID, 'autoCompletarNid0').get_attribute('value'))


def _fill_attachments(session, attachments):
    for index, attachment in enumerate(attachments):
        _field(session, '#divEngadirAnexos a').click()
        session.wait.until(EC.presence_of_element_located((By.ID, f'anexos_arquivo{index}')))
        _fill(session, f'[name="anexos[{index}].asunto"]', attachment['title'])
        _field(session, f'input[name="anexos[{index}].arquivo"]').send_keys(attachment['path'])


def _is_procedure_url(url):
    parsed, expected = urlsplit(url), urlsplit(REQUEST_URL)
    return (parsed.scheme, parsed.netloc, parsed.path) == (expected.scheme, expected.netloc, expected.path)


def _review_state(session):
    url = session.driver.current_url
    action = _field(session, '#formulario').get_attribute('action')
    execution = _field(session, '#formulario input[name="execution"]').get_attribute('value')
    flow_key = _field(session, '#formulario input[name="_flowExecutionKey"]').get_attribute('value')
    sources = session.driver.find_elements(By.CSS_SELECTOR, '#pdf a[href], #pdf object[data], #pdf embed[src], #pdf iframe[src]')
    pdf_url = next((node.get_attribute('href') or node.get_attribute('data') or node.get_attribute('src')
                    for node in sources), '')
    if (not _is_procedure_url(url) or not _is_procedure_url(action) or not execution
            or flow_key != execution or parse_qs(urlsplit(action).query).get('execution') != [execution]
            or not _is_procedure_url(pdf_url)
            or parse_qs(urlsplit(pdf_url).query).get('execution') != [execution]
            or parse_qs(urlsplit(pdf_url).query).get('_eventId') != ['obterSolicitudePdf']):
        raise CongressRequestError('No se pudo identificar la revisión PDF de esta solicitud.')
    _next_button(session)
    return {'url': url, 'action': action, 'execution': execution, 'pdf_url': pdf_url}


def _download_pdf(session, url):
    if not _is_procedure_url(url) or parse_qs(urlsplit(url).query).get('_eventId') != ['obterSolicitudePdf']:
        raise CongressRequestError('La URL de la preimpresión no corresponde a USC.')
    # Fetch in the page: retains the authenticated cookie jar and the live wizard.
    response = session.driver.execute_async_script("""
        const url = arguments[0], done = arguments[arguments.length - 1];
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
        fetch(url, {credentials: 'same-origin', redirect: 'error', signal: controller.signal})
          .then(async response => {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const bytes = new Uint8Array(await response.arrayBuffer());
            let binary = '';
            for (let i = 0; i < bytes.length; i += 8192)
                binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
            done({data: btoa(binary)});
          }).catch(error => done({error: String(error)})).finally(() => clearTimeout(timer));
    """, url)
    try:
        pdf = base64.b64decode(response['data'], validate=True)
        if not pdf.startswith(b'%PDF-'):
            raise ValueError('not a PDF')
    except (KeyError, TypeError, ValueError) as exc:
        raise CongressRequestError('No se pudo obtener la preimpresión PDF. No se envió la solicitud.') from exc
    return pdf


def submit_review(session, confirm=None):
    state = _review_state(session)
    if confirm is not None:
        pdf = _download_pdf(session, state['pdf_url'])
        try:
            accepted = confirm(pdf)
        except Exception as exc:
            raise CongressRequestCancelled('No se pudo completar la confirmación. No se envió la solicitud.') from exc
        if accepted is not True:
            raise CongressRequestCancelled('Solicitud cancelada. No se envió a USC.')
        if _review_state(session) != state:
            raise CongressRequestError('La revisión de USC cambió durante la confirmación. No se envió la solicitud.')
    button = _next_button(session)
    try:
        commit_click(session, button)
    except WebDriverException:
        logger.warning('Congress submission action encountered a browser error; outcome unverified')
    # Pending: inspect a real receipt page before implementing success detection.
    # The hidden form id identifies the procedure, not a submission receipt.
    return {'status': 'unverified', 'submission_attempted': True}


def submit_congress_request(session, data, confirm=None):
    data = validate_request(data, get_madrid_now().date())
    if confirm is not None and not callable(confirm):
        raise CongressRequestError('confirm debe ser una función o None.')
    try:
        session._ensure_access_to(REQUEST_URL)
        session.wait.until(EC.presence_of_element_located((By.ID, 'autorizacionDatos')))
        consent = _field(session, '#autorizacionDatos')
        if not consent.is_selected():
            consent.click()
        _advance(session, 'input[name="email"]')
        for key, name in (('email', 'email'), ('phone', 'telefono')):
            if key in data['contact']:
                _fill(session, f'input[name="{name}"]', data['contact'][key])
        _advance(session, '#idPais')
        _fill_address(session, data['address'])
        _advance(session, _detail_selector(0))
        _fill_details(session, data)
        _advance(session, '#autoCompletarNome0')
        _fill_supervisor(session, data['supervisor_query'])
        _advance(session, '#divEngadirAnexos')
        _fill_attachments(session, data['attachments'])
        _advance(session, '#pdf')
        return submit_review(session, confirm)
    except WebDriverException as exc:
        raise CongressRequestError('No se pudo completar el formulario de USC. No se envió la solicitud.') from exc
