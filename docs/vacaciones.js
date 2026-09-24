/* Year is an allowance year, independent of the dates in the current calendar. */
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const el = id => document.getElementById(id);
const DAY_TYPES = {
  N: { className: 'dia-no-laboral', label: 'Día no laborable' },
  V: { className: 'dia-vacaciones', label: 'Vacaciones registradas' },
  P: { className: 'dia-parcial', label: 'Jornada parcial' }
};
const selectedDays = new Set();
let data, selectedYear, selectedType, calendar;
const number = value => new Intl.NumberFormat('es', {maximumFractionDigits: 2}).format(value);
const dateString = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const dateLabel = value => new Date(`${value}T12:00:00`).toLocaleDateString('es', {day: 'numeric', month: 'long', year: 'numeric'});
const sortedDays = () => [...selectedDays].sort();

function showStep(name) {
  for (const step of ['year', 'type', 'calendar']) el(`${step}-step`).hidden = step !== name;
  el('status').textContent = '';
  if (name === 'calendar') { calendar?.render(); calendar?.updateSize(); }
}
function dayTypes(value) {
  return [...new Set(data.entries.filter(entry => entry.start <= value && value <= entry.end).map(entry => entry.code))];
}
function updateSelection() {
  calendar.el.querySelectorAll('.fc-daygrid-day[data-date]').forEach(cell => {
    cell.classList.toggle('dia-seleccionado', selectedDays.has(cell.dataset.date));
  });
  el('selection-count').textContent = `${selectedDays.size} días seleccionados · ${number(selectedType.remainingDays - selectedDays.size)} días quedarían`;
  el('send').disabled = selectedDays.size === 0;
}
function renderTypes() {
  selectedYear = data.years.find(item => item.year === Number(el('year').value));
  selectedType = null;
  selectedDays.clear();
  el('types').replaceChildren();
  el('type-next').disabled = true;
  el('type-year').textContent = `Saldo de ${selectedYear.year}`;
  for (const kind of selectedYear.types) {
    const label = document.createElement('label'); label.className = 'type-option';
    const radio = document.createElement('input');
    radio.type = 'radio'; radio.name = 'vacationType'; radio.value = kind.id;
    radio.disabled = kind.remainingDays < 1;
    const name = document.createElement('span'); name.className = 'type-name'; name.textContent = kind.name;
    const balance = document.createElement('span'); balance.className = 'type-balance';
    balance.textContent = `${number(kind.remainingDays)} días`;
    if (kind.remainingHours > 0) balance.textContent += ` + ${number(kind.remainingHours)} h`;
    radio.onchange = () => { selectedType = kind; selectedDays.clear(); el('type-next').disabled = false; };
    label.append(radio, name, balance); el('types').append(label);
  }
  showStep('type');
}
function openCalendar() {
  const validRange = {start: data.today, end: `${selectedYear.year + 1}-03-01`};
  el('calendar-balance').textContent = `${selectedType.name} · Saldo ${selectedYear.year} · ${number(selectedType.remainingDays)} días disponibles`;
  showStep('calendar');
  if (!calendar) {
    calendar = new FullCalendar.Calendar(el('calendar'), {
      locale: 'es', initialView: 'dayGridMonth', initialDate: data.today,
      firstDay: 1, height: 'auto', fixedWeekCount: false,
      validRange,
      headerToolbar: {start: 'title', end: 'prev,next,today'}, buttonText: {today: 'Hoy'},
      dayCellClassNames(info) {
        const value = dateString(info.date);
        const classes = dayTypes(value).map(code => DAY_TYPES[code].className);
        if (selectedDays.has(value)) classes.push('dia-seleccionado');
        return classes;
      },
      dayCellDidMount(info) {
        const value = dateString(info.date);
        info.el.title = [dateLabel(value), ...dayTypes(value).map(code => DAY_TYPES[code].label)].join(' · ');
      },
      dateClick(info) {
        const value = info.dateStr;
        if (value < data.today || value >= `${selectedYear.year + 1}-03-01`) return;
        if (selectedDays.has(value)) selectedDays.delete(value);
        else {
          if (dayTypes(value).some(code => code === 'N' || code === 'V')) {
            el('status').textContent = 'Ese día es no laborable o ya tiene vacaciones registradas.'; return;
          }
          if (selectedDays.size + 1 > selectedType.remainingDays || selectedDays.size >= 100) {
            el('status').textContent = 'Has alcanzado el máximo de días disponibles para esta solicitud.'; return;
          }
          selectedDays.add(value);
        }
        el('status').textContent = ''; updateSelection();
      }
    });
    calendar.render();
  } else {
    calendar.setOption('validRange', validRange);
    if (dateString(calendar.getDate()) >= validRange.end) calendar.gotoDate(data.today);
  }
  updateSelection();
}
function sendSelection() {
  if (el('send').disabled) return;
  if (!tg?.sendData) { el('status').textContent = 'Abre esta selección desde el botón del bot en Telegram.'; return; }
  const payload = {
    type: 'vacation_request_submit', requestId: data.requestId, year: selectedYear.year,
    vacationTypeId: selectedType.id, days: sortedDays()
  };
  el('send').disabled = true;
  try { tg.sendData(JSON.stringify(payload)); }
  catch { el('send').disabled = false; el('status').textContent = 'No se pudo enviar la selección. Inténtalo de nuevo.'; }
}
try {
  const params = new URLSearchParams(location.hash.slice(1) || location.search.slice(1));
  data = JSON.parse(params.get('data'));
  if (!data || !Array.isArray(data.years) || !Array.isArray(data.entries) || !data.requestId) throw new Error();
  data.years = data.years.filter(item => data.today < `${item.year + 1}-03-01` &&
    (item.year === data.currentYear ||
    (item.year === data.currentYear - 1 && item.types.some(kind => kind.remainingDays > 0))));
  data.entries = data.entries.filter(entry => typeof entry === 'string' && DAY_TYPES[entry[0]]).map(entry => {
    const [start, end = start] = entry.slice(1).split(':'); return {code: entry[0], start, end};
  });
  for (const year of data.years) el('year').add(new Option(String(year.year), String(year.year)));
  el('year').disabled = el('year-next').disabled = data.years.length === 0;
  if (!data.years.length) el('status').textContent = 'No hay años de saldo disponibles en USC.';
  el('year-next').onclick = renderTypes;
  el('type-back').onclick = () => showStep('year');
  el('type-next').onclick = openCalendar;
  el('calendar-back').onclick = () => showStep('type');
  el('send').onclick = sendSelection;
} catch {
  el('send').disabled = true;
  el('status').textContent = 'No se pudo cargar la selección. Abre /vacaciones de nuevo en el bot.';
}
