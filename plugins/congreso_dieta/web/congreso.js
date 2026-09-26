/* Congress cases: cancel an open one or pick a continuous date range for a new one. */
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const el = id => document.getElementById(id);
const label = value => new Date(`${value}T12:00:00`).toLocaleDateString('es', {day: 'numeric', month: 'long', year: 'numeric'});
let data, start = null, end = null, sent = false, calendar;

function send(payload) {
  if (sent) return;
  sent = true;
  el('send').disabled = true;
  tg?.sendData(JSON.stringify({...payload, token: data.token}));
}
const insideCase = day => data.cases.some(c => c.start <= day && day <= c.end);
const blocked = day => day < data.minStart || insideCase(day);
function problem() {
  if (!start) return 'Pulsa el primer día del congreso.';
  if (!end) return 'Pulsa el último día (o el mismo día si dura uno).';
  if (start.slice(0, 4) !== end.slice(0, 4)) return 'El congreso no puede cruzar el cambio de año.';
  if (data.cases.some(c => c.start <= end && start <= c.end)) return 'Las fechas se solapan con un trámite en curso.';
  return '';
}
function refresh() {
  el('selection').textContent = start ? (end ? `Del ${label(start)} al ${label(end)}` : `Desde el ${label(start)}`) : 'Sin fechas seleccionadas';
  const message = problem();
  el('status').textContent = start && end ? message : '';
  el('send').disabled = sent || Boolean(message);
  calendar?.el.querySelectorAll('.fc-daygrid-day[data-date]').forEach(cell => {
    const day = cell.dataset.date;
    cell.classList.toggle('dia-seleccionado', Boolean(start) && day >= start && day <= (end || start));
    cell.classList.toggle('dia-bloqueado', blocked(day));
  });
}
function pick(day) {
  if (sent || blocked(day)) return;
  if (!start || end || day < start) { start = day; end = null; } else { end = day; }
  refresh();
}
function renderCases() {
  el('cases-section').hidden = !data.cases.length;
  el('cases').replaceChildren(...data.cases.map(item => {
    const card = document.createElement('div'); card.className = 'case';
    const title = document.createElement('strong'); title.textContent = `Del ${label(item.start)} al ${label(item.end)}`;
    const status = document.createElement('p'); status.textContent = item.status;
    card.append(title, status);
    if (item.problem) { const p = document.createElement('p'); p.className = 'problem'; p.textContent = item.problem; card.append(p); }
    const cancel = document.createElement('button'); cancel.className = 'secondary'; cancel.textContent = 'Cancelar';
    cancel.onclick = () => send({type: 'congreso_dieta_cancel', case: item.id});
    card.append(cancel);
    return card;
  }));
}
try {
  // Telegram appends its own tgWebApp* parameters to the fragment; read only ours.
  const params = new URLSearchParams(location.hash.slice(1) || location.search.slice(1));
  data = JSON.parse(params.get('data'));
} catch {
  el('status').textContent = 'No se pudieron leer los datos. Abre /congreso_dieta de nuevo.';
}
if (data) {
  renderCases();
  calendar = new FullCalendar.Calendar(el('calendar'), {
    initialView: 'dayGridMonth', locale: 'es', firstDay: 1, height: 'auto', initialDate: data.minStart,
    fixedWeekCount: false, headerToolbar: {start: 'title', end: 'prev,next,today'}, buttonText: {today: 'Hoy'},
    dateClick: info => pick(info.dateStr), datesSet: refresh,
  });
  calendar.render();
  el('send').onclick = () => { if (!problem()) send({type: 'congreso_dieta_new', start, end}); };
  refresh();
}
