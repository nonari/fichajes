/* One explicit absence request; files are collected by the bot in Telegram. */
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const el = id => document.getElementById(id);
const selected = new Map();
let data, selectedYear, selectedType, calendar, sent = false;
const dateLabel = value => new Date(`${value}T12:00:00`).toLocaleDateString('es', {day: 'numeric', month: 'long', year: 'numeric'});
const dateString = date => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
const periods = () => [...selected.values()].sort((a,b) => a.date.localeCompare(b.date));

function showStep(name) {
  for (const step of ['year', 'type', 'calendar']) el(`${step}-step`).hidden = step !== name;
  el('status').textContent = '';
}
function updateSelection() {
  calendar?.el.querySelectorAll('.fc-daygrid-day[data-date]').forEach(cell => {
    cell.classList.toggle('dia-seleccionado', selected.has(cell.dataset.date));
  });
  el('selection-count').textContent = `${selected.size} días seleccionados`;
  el('send').disabled = sent || !selected.size || (selectedType?.requiresHours && periods().some(p =>
    !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(p.startTime || '') ||
    !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(p.endTime || '') || p.startTime >= p.endTime));
}
function renderPeriods() {
  el('periods').replaceChildren();
  for (const period of periods()) {
    const row = document.createElement('div'); row.className = 'period';
    const title = document.createElement('strong'); title.textContent = dateLabel(period.date); row.append(title);
    if (selectedType.requiresHours) {
      for (const [key, labelText] of [['startTime','Desde'], ['endTime','Hasta']]) {
        const label = document.createElement('label'); label.textContent = labelText;
        const input = document.createElement('input'); input.type = 'time'; input.required = true;
        input.value = period[key] || ''; input.setAttribute('aria-label', `${labelText} ${dateLabel(period.date)}`);
        input.oninput = () => { period[key] = input.value; updateSelection(); };
        label.append(input); row.append(label);
      }
    }
    const remove = document.createElement('button'); remove.className = 'secondary'; remove.textContent = 'Quitar día';
    remove.onclick = () => { selected.delete(period.date); renderPeriods(); };
    row.append(remove); el('periods').append(row);
  }
  updateSelection();
}
function renderTypes() {
  selectedYear = Number(el('year').value); selectedType = null; selected.clear();
  el('types').replaceChildren(); el('type-next').disabled = true;
  for (const kind of data.types) {
    const label = document.createElement('label'); label.className = 'type-option';
    const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'absenceType'; radio.value = kind.id;
    const text = document.createElement('span'); text.textContent = `${kind.name} · ${kind.requiresHours ? 'Por horas' : 'Días completos'}`;
    radio.onchange = () => { selectedType = kind; selected.clear(); el('type-next').disabled = false; };
    label.append(radio,text); el('types').append(label);
  }
  showStep('type');
}
function openCalendar() {
  const range = {start: `${selectedYear}-01-01`, end: `${selectedYear+1}-01-01`};
  const initial = data.today >= range.start && data.today < range.end ? data.today : range.start;
  el('selection-kind').textContent = `${selectedType.name} · ${selectedType.requiresHours ? 'Indica el horario de cada día.' : 'Días completos.'}`;
  showStep('calendar');
  if (!calendar) {
    calendar = new FullCalendar.Calendar(el('calendar'), {
      locale: 'es', initialView: 'dayGridMonth', initialDate: initial, firstDay: 1,
      height: 'auto', fixedWeekCount: false, validRange: range,
      headerToolbar: {start: 'title', end: 'prev,next,today'}, buttonText: {today:'Hoy'},
      dayCellClassNames: info => selected.has(dateString(info.date)) ? ['dia-seleccionado'] : [],
      dateClick(info) {
        const value = info.dateStr;
        if (value < `${selectedYear}-01-01` || value >= `${selectedYear+1}-01-01`) return;
        if (selected.has(value)) selected.delete(value);
        else if (selected.size < 100) selected.set(value, {date: value});
        else { el('status').textContent = 'Puedes seleccionar como máximo 100 días.'; return; }
        el('status').textContent = ''; renderPeriods();
      }
    });
  } else { calendar.setOption('validRange', range); calendar.gotoDate(initial); }
  calendar.render(); calendar.updateSize(); renderPeriods();
}
function updateHelp() {
  el('submission-help').textContent = el('attach-documents').checked
    ? 'Al continuar, el bot te pedirá los documentos en el chat.'
    : data.confirmationRequired
      ? 'Recibirás una captura del resumen de USC en el chat para confirmar el envío.'
      : 'Al continuar se enviará la solicitud a USC.';
}
function sendSelection() {
  if (sent || el('send').disabled) return;
  if (!tg?.sendData) { el('status').textContent = 'Abre /ausencias desde Telegram para enviar la solicitud.'; return; }
  const payload = JSON.stringify({type:'absence_request_submit', requestId:data.requestId, year:selectedYear,
    absenceTypeId:selectedType.id, periods:periods(), observations:el('observations').value.trim(),
    attachDocuments:el('attach-documents').checked});
  if (new TextEncoder().encode(payload).length > 4096) {
    el('status').textContent = 'La selección es demasiado grande para Telegram. Reduce las fechas o las observaciones.'; return;
  }
  sent = true; updateSelection();
  try { tg.sendData(payload); }
  catch { sent = false; updateSelection(); el('status').textContent = 'No se pudo enviar la selección. Inténtalo de nuevo.'; }
}
try {
  const params = new URLSearchParams(location.hash.slice(1) || location.search.slice(1));
  data = JSON.parse(params.get('data'));
  if (!data || !Array.isArray(data.years) || !Array.isArray(data.types) || !data.requestId) throw new Error();
  for (const year of data.years) el('year').add(new Option(String(year),String(year)));
  el('year').disabled = el('year-next').disabled = !data.years.length || !data.types.length;
  if (el('year-next').disabled) el('status').textContent = 'USC no ofrece años o tipos de ausencia disponibles.';
  el('year-next').onclick = renderTypes;
  el('type-back').onclick = () => showStep('year');
  el('type-next').onclick = openCalendar;
  el('calendar-back').onclick = () => showStep('type');
  el('attach-documents').onchange = updateHelp;
  el('send').onclick = sendSelection; updateHelp();
} catch {
  el('send').disabled = true;
  el('status').textContent = 'No se pudo cargar la selección. Abre /ausencias de nuevo.';
}
