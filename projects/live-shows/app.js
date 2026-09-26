// ── Jazz Cup SVG placeholder ───────────────────────────────────────
// Inspired by the Solo Cup "Jazz" pattern (Gina Ekiss, 1992)
const JAZZ_CUP_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 267" preserveAspectRatio="xMidYMid slice">
  <rect width="400" height="267" fill="#f5f0e4"/>
  <path d="M0,162 C32,138 72,150 114,128 C156,106 180,122 224,102 C268,82 304,93 342,74 C368,60 388,50 400,44 L400,267 L0,267Z" fill="#2dc4c4"/>
  <path d="M0,212 C52,197 106,208 164,200 C222,192 282,204 340,198 C366,194 386,198 400,195 L400,267 L0,267Z" fill="#1fa8a8"/>
  <path d="M0,142 C26,124 48,150 76,134 C104,118 124,144 152,130 C180,116 200,140 228,127 C256,114 278,136 306,124 C334,112 356,132 384,120 C390,117 396,121 400,118" stroke="#9b5cc4" stroke-width="4" fill="none" stroke-linecap="round"/>
  <path d="M0,120 C22,110 40,122 62,114 C84,106 102,118 124,110 C146,102 164,116 186,108" stroke="#9b5cc4" stroke-width="2.5" fill="none" stroke-linecap="round" opacity="0.55"/>
  <polygon points="72,107 86,80 100,107" fill="#e8a020" opacity="0.9"/>
  <polygon points="194,76 210,48 226,76" fill="#9b5cc4" opacity="0.8"/>
  <polygon points="320,90 336,62 352,90" fill="#2dc4c4" opacity="0.6"/>
  <line x1="46" y1="126" x2="52" y2="97" stroke="#2dc4c4" stroke-width="3.5" stroke-linecap="round"/>
  <line x1="157" y1="98" x2="165" y2="70" stroke="#9b5cc4" stroke-width="3" stroke-linecap="round"/>
  <line x1="276" y1="106" x2="283" y2="80" stroke="#e8a020" stroke-width="3" stroke-linecap="round"/>
  <circle cx="136" cy="88" r="4.5" fill="#2dc4c4"/>
  <circle cx="250" cy="62" r="3.5" fill="#9b5cc4"/>
  <circle cx="374" cy="94" r="5.5" fill="#e8a020" opacity="0.75"/>
</svg>`;

// ── Constants ──────────────────────────────────────────────────────
const LARGE_VENUES = new Set([
  'save-on-foods memorial centre',
  'royal theatre',
  'mcpherson playhouse',
  'port theatre',
  'alix goolden hall',
  'victoria conservatory of music',
  'mary winspear centre',
  'charlie white theatre',
]);

const VENUE_RANK = {
  'save-on-foods memorial centre': 10,
  'royal theatre': 9,
  'mcpherson playhouse': 8,
  'port theatre': 7,
  'alix goolden hall': 7,
  'victoria conservatory of music': 7,
  'mary winspear centre': 6,
  'charlie white theatre': 6,
  'capital ballroom': 5,
  'upstairs entertainment': 4,
  'lucky bar': 3,
  "the queen's hotel & bar": 3,
  'the globe live studio': 3,
  'the duke saloon': 3,
  "hermann's jazz club": 3,
};

// Sports keywords — safety net on top of scraper-level filtering
const SPORTS_RE = /\b(nhl|nba|mlb|nfl|mls|royals game|hockey game|basketball game|lacrosse|rugby match|harbourcats|nightowls|night owls|pacific fc)\b| vs /i;

const WEEKDAY_FEATURE_PRICE = 70;

// How far forward the calendar grid runs. Recurring events are expanded years
// out by the scrapers (one monthly song circle reaches 2028), which otherwise
// builds ~100 week rows of blank space. Events past the horizon stay in List
// view and are linked from a note at the bottom of the calendar.
const CAL_HORIZON_MONTHS = 8;

// Bucket for events whose source gave us no usable city.
const UNKNOWN_CITY = 'Other';

// Rough south→north ordering for the filter pills. Cities not listed here are
// appended (by event count) rather than dropped, so new scraper sources still
// get a working button.
const CITY_ORDER = [
  'Victoria', 'Mill Bay', 'Duncan', 'Lake Cowichan', 'Nanaimo',
  'Port Alberni', 'Tofino', 'Comox Valley', 'Campbell River',
  'Salt Spring Island', 'Galiano Island', 'Mayne Island', 'Pender Island',
  'Gibsons', 'Roberts Creek', 'Sechelt', 'Madeira Park', 'Egmont',
  'Powell River', 'Van Anda',
];

// ── State ──────────────────────────────────────────────────────────
let ALL_EVENTS = [];
let activeCity = 'all';
let activeView = 'cal';
let currentDayISO = null;
let expandedBubbleId = null;

// ── DOM refs ───────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const calHeaders = document.querySelector('.week-col-headers');

// ── Text safety ────────────────────────────────────────────────────
// Scraped strings from 14 third-party sites end up inside innerHTML, so they
// get escaped on the way out. Some feeds are already HTML-encoded (and a few
// are double-encoded, e.g. "SoCal Restaurant &#038; Lounge"), so decodeText()
// runs once over the data on the way in — decode in, escape out.
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ESC_MAP[c]);
}

const decoder = document.createElement('textarea');
function decodeText(s) {
  if (typeof s !== 'string' || !s.includes('&')) return s;
  decoder.innerHTML = s;       // textarea content is RCDATA — never executes
  return decoder.value;
}

// Only http(s) links are followed; a scraped `javascript:` URL is dropped.
function httpUrl(u) {
  return typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null;
}

// ── Helpers ────────────────────────────────────────────────────────
function parseDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}
function toISO(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}
function todayISO() { return toISO(new Date()); }
function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
function weekMonday(d) {
  const r = new Date(d);
  const dow = r.getDay();
  r.setDate(r.getDate() + (dow === 0 ? -6 : 1 - dow));
  return r;
}
function fmtMonth(d) { return d.toLocaleDateString('en-CA', { month: 'long', year: 'numeric' }); }
function fmtDay(iso) {
  return parseDate(iso).toLocaleDateString('en-CA', { weekday: 'long', month: 'long', day: 'numeric' });
}
function fmtTime(t) {
  if (!t) return '';
  const [h, m] = t.split(':').map(Number);
  const ampm = h >= 12 ? 'pm' : 'am';
  const h12 = h % 12 || 12;
  return m === 0 ? `${h12}${ampm}` : `${h12}:${String(m).padStart(2,'0')}${ampm}`;
}
function cityClass(city) {
  if (city === 'Victoria') return 'vic';
  if (city === 'Nanaimo')  return 'nan';
  return 'other';
}
function cityLabel(city) {
  if (city === 'Victoria') return 'VIC';
  if (city === 'Nanaimo')  return 'NAN';
  if (!city) return UNKNOWN_CITY.slice(0,3).toUpperCase();
  return city.slice(0,3).toUpperCase();
}

// Normalise the city field. Sources hand us nulls, empty strings, stray
// province suffixes ("Victoria Bc") and a bare "Bc"; everything unusable folds
// into one explicit UNKNOWN_CITY bucket so it stays filterable.
function normCity(raw) {
  let c = (raw === null || raw === undefined ? '' : String(raw))
    .replace(/\s+/g, ' ')
    .trim();
  if (!c) return UNKNOWN_CITY;
  // Title-case, then strip a trailing province token.
  c = c.replace(/\S+/g, w => w[0].toUpperCase() + w.slice(1).toLowerCase());
  c = c.replace(/[,\s]+B\.?C\.?$/i, '').trim();
  if (!c || /^B\.?C\.?$/i.test(c)) return UNKNOWN_CITY;
  return c;
}

function filteredEvents() {
  return ALL_EVENTS.filter(e => {
    if (SPORTS_RE.test(e.title)) return false;
    if (activeCity !== 'all' && e.city !== activeCity) return false;
    return true;
  });
}

// Decode encoded entities and normalise cities once, at load, so every render
// path sees clean values.
function normalizeEvents(events) {
  return events.map(e => ({
    ...e,
    title:  decodeText(e.title)  || '',
    venue:  decodeText(e.venue)  || '',
    price:  decodeText(e.price)  || '',
    genre:  decodeText(e.genre)  || '',
    city:   normCity(e.city),
  }));
}

function byDate(events) {
  const m = new Map();
  for (const e of events) {
    if (!m.has(e.date)) m.set(e.date, []);
    m.get(e.date).push(e);
  }
  return m;
}

function parsePrice(priceStr) {
  if (!priceStr) return 0;
  const m = priceStr.match(/[\d.]+/);
  return m ? parseFloat(m[0]) : 0;
}

function venueScore(venueName) {
  return VENUE_RANK[(venueName || '').toLowerCase()] || 0;
}

function isLargeVenue(venueName) {
  return LARGE_VENUES.has((venueName || '').toLowerCase());
}

function shouldFeature(dayIndex, events) {
  if (dayIndex >= 4) return true; // Fri/Sat/Sun always
  return events.some(e =>
    parsePrice(e.price) >= WEEKDAY_FEATURE_PRICE || isLargeVenue(e.venue)
  );
}

const NON_MUSIC_PHOTO_RE = /\b(wrestling|wwe|ufc|mma|boxing|fight night|fight card|rodeo|demolition derby|monster truck|car show|air show|trade show|home show|gun show|dog show|horse show|pageant|parade|easter|egg hunt|meat draw|bingo|trivia|yoga|meditation|art show|craft fair|market|gallery|exhibit)\b/i;

function pickFeatured(events) {
  const withImage = events.filter(e => e.image_url);
  const pool = withImage.length > 0 ? withImage : events;
  return pool.slice().sort((a, b) => {
    // Deprioritise non-music events as the calendar photo
    const aNonMusic = NON_MUSIC_PHOTO_RE.test(a.title || '') ? 1 : 0;
    const bNonMusic = NON_MUSIC_PHOTO_RE.test(b.title || '') ? 1 : 0;
    if (aNonMusic !== bNonMusic) return aNonMusic - bNonMusic;
    const pd = parsePrice(b.price) - parsePrice(a.price);
    if (pd !== 0) return pd;
    return venueScore(b.venue) - venueScore(a.venue);
  })[0];
}

// Build the Jazz cup filler element
function makeJazzCup() {
  const el = document.createElement('div');
  el.className = 'jazz-cup-filler';
  el.innerHTML = JAZZ_CUP_SVG;
  return el;
}

// ── Filters & View Toggle ──────────────────────────────────────────
// Buttons are derived from the cities actually present in the upcoming data, so
// there are no dead pills and no unreachable events.
function renderCityFilter() {
  const today = todayISO();
  const counts = new Map();
  for (const e of ALL_EVENTS) {
    if (e.date < today) continue;
    if (SPORTS_RE.test(e.title)) continue;
    counts.set(e.city, (counts.get(e.city) || 0) + 1);
  }

  const known   = CITY_ORDER.filter(c => counts.has(c));
  const extra   = [...counts.keys()]
    .filter(c => c !== UNKNOWN_CITY && !CITY_ORDER.includes(c))
    .sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b));
  const cities  = [...known, ...extra];
  if (counts.has(UNKNOWN_CITY)) cities.push(UNKNOWN_CITY);   // always last

  const total = [...counts.values()].reduce((a, b) => a + b, 0);
  const rows  = [{ city: 'all', label: 'All', count: total }]
    .concat(cities.map(c => ({ city: c, label: c, count: counts.get(c) })));

  if (activeCity !== 'all' && !cities.includes(activeCity)) activeCity = 'all';

  $('city-filter').innerHTML = rows.map(r => `
    <button class="filter-btn${r.city === activeCity ? ' active' : ''}"
            data-city="${esc(r.city)}"
            title="${esc(r.label)} — ${r.count} upcoming">${esc(r.label)}</button>`).join('');
}

document.getElementById('city-filter').addEventListener('click', e => {
  const btn = e.target.closest('.filter-btn');
  if (!btn) return;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeCity = btn.dataset.city;
  renderCalendar();
  renderList();
});

document.querySelector('.view-toggle').addEventListener('click', e => {
  const btn = e.target.closest('.toggle-btn');
  if (!btn) return;
  setView(btn.dataset.view);
});

function setView(v) {
  activeView = v;
  document.querySelectorAll('.main-view').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
  $(`${v}-view`).classList.add('active');
  document.querySelector(`[data-view="${v}"]`).classList.add('active');
}

// ── Calendar Render ────────────────────────────────────────────────
function renderCalendar() {
  const events = filteredEvents();
  const dateMap = byDate(events);
  const today = todayISO();

  const dates = events.map(e => e.date).sort();
  if (!dates.length) { $('cal-body').innerHTML = ''; return; }

  const firstDate = parseDate(today);

  // Cap the grid at a fixed forward window so a single recurring event expanded
  // to 2028 can't add two years of blank week rows. Snapped to the end of the
  // month so the cut-off matches the month named in the note below.
  const horizon = parseDate(today);
  horizon.setDate(1);
  horizon.setMonth(horizon.getMonth() + CAL_HORIZON_MONTHS + 1);
  horizon.setDate(0);
  const horizonISO = toISO(horizon);

  const lastShown = dates.filter(d => d <= horizonISO).pop() || today;
  const beyond    = dates.filter(d => d > horizonISO).length;

  let cur = weekMonday(firstDate);
  const end = addDays(weekMonday(parseDate(lastShown)), 6);

  const body = $('cal-body');
  body.innerHTML = '';

  let lastMonth = -1;

  function insertMonthLabel(d) {
    const m = d.getMonth();
    if (m === lastMonth) return;
    if (lastMonth !== -1) {
      const brk = document.createElement('div');
      brk.className = 'month-break';
      body.appendChild(brk);
    }
    lastMonth = m;
    const ml = document.createElement('div');
    ml.className = 'month-label';
    ml.textContent = fmtMonth(d);
    body.appendChild(ml);
  }

  function makeBlankCol(i) {
    const col = document.createElement('div');
    col.className = 'day-col out-of-month ' + (i >= 4 ? 'weekend' : 'weekday');
    return col;
  }

  function makeDayCol(d, i) {
    const iso = toISO(d);
    const isWeekend = i >= 4;
    const isToday   = iso === today;
    const dayEvents = dateMap.get(iso) || [];
    const count     = dayEvents.length;
    const featured  = count > 0 && shouldFeature(i, dayEvents);

    const col = document.createElement('div');
    col.className = [
      'day-col',
      isWeekend ? 'weekend' : 'weekday',
      featured ? 'featured' : 'compact',
      isToday  ? 'today' : '',
      count > 0 ? 'has-events' : '',
    ].filter(Boolean).join(' ');
    col.dataset.iso = iso;

    if (featured && count > 0) {
      const featEv = pickFeatured(dayEvents);

      const dn = document.createElement('div');
      dn.className = 'featured-day-num';
      dn.textContent = d.getDate();
      col.appendChild(dn);

      const wrap = document.createElement('div');
      wrap.className = 'day-thumb-wrap';
      wrap.dataset.count = count === 1 ? '1 event' : `${count} events`;

      if (featEv.image_url) {
        const img = document.createElement('img');
        img.className = 'day-thumb';
        img.src = featEv.image_url;
        img.alt = '';
        img.loading = 'lazy';
        img.onerror = function() { this.replaceWith(makeJazzCup()); };
        wrap.appendChild(img);
      } else {
        wrap.appendChild(makeJazzCup());
      }
      col.appendChild(wrap);

      const cap = document.createElement('div');
      cap.className = 'day-caption';

      const dateLabel = document.createElement('div');
      dateLabel.className = 'day-caption-date';
      dateLabel.textContent = d.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
      cap.appendChild(dateLabel);

      const actList = document.createElement('div');
      actList.className = 'day-caption-acts';
      const others = dayEvents.filter(e => e !== featEv);
      const listEvs = [featEv, ...others];
      const showCount = 4;
      listEvs.slice(0, showCount).forEach((ev, idx) => {
        const r = document.createElement('div');
        r.className = 'day-caption-act' + (idx === 0 ? ' featured-act' : '');
        r.textContent = ev.title;
        actList.appendChild(r);
      });
      if (dayEvents.length > showCount) {
        const more = document.createElement('div');
        more.className = 'day-caption-more';
        more.textContent = `+${dayEvents.length - showCount} more`;
        actList.appendChild(more);
      }
      cap.appendChild(actList);
      col.appendChild(cap);

    } else {
      const dn = document.createElement('div');
      dn.className = 'compact-day-num';
      dn.textContent = d.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
      col.appendChild(dn);

      if (count > 0) {
        const list = document.createElement('div');
        list.className = 'compact-event-list';
        dayEvents.slice(0, 4).forEach(ev => {
          const r = document.createElement('div');
          r.className = 'compact-ev';
          r.innerHTML = `<span class="compact-ev-time">${fmtTime(ev.start_time)}</span>${esc(ev.title)}`;
          list.appendChild(r);
        });
        if (count > 4) {
          const more = document.createElement('div');
          more.className = 'compact-ev-more';
          more.textContent = `+${count - 4} more`;
          list.appendChild(more);
        }
        col.appendChild(list);
      }
    }

    if (count > 0) {
      col.addEventListener('click', () => openDayPanel(iso));
      if (!isWeekend) {
        col.addEventListener('mouseenter', () => {
          calHeaders.className = `week-col-headers day-hover-${i}`;
        });
        col.addEventListener('mouseleave', () => {
          calHeaders.className = 'week-col-headers';
        });
      }
    }

    return col;
  }

  function appendWeekRow(weekDates, activeSet) {
    const row = document.createElement('div');
    row.className = 'week-row';
    weekDates.forEach((d, i) => {
      row.appendChild(activeSet.has(i) ? makeDayCol(d, i) : makeBlankCol(i));
    });
    body.appendChild(row);
  }

  while (cur <= end) {
    const weekDates = Array.from({length: 7}, (_, i) => addDays(cur, i));
    const startMonth = weekDates[0].getMonth();
    const endMonth   = weekDates[6].getMonth();

    if (startMonth === endMonth) {
      insertMonthLabel(weekDates[0]);
      appendWeekRow(weekDates, new Set([0,1,2,3,4,5,6]));
    } else {
      // Week crosses a month boundary — split into two partial rows
      const splitIdx = weekDates.findIndex(d => d.getMonth() !== startMonth);
      insertMonthLabel(weekDates[0]);
      appendWeekRow(weekDates, new Set(Array.from({length: splitIdx}, (_, i) => i)));
      insertMonthLabel(weekDates[splitIdx]);
      appendWeekRow(weekDates, new Set(Array.from({length: 7 - splitIdx}, (_, i) => i + splitIdx)));
    }

    cur = addDays(cur, 7);
  }

  if (beyond > 0) {
    const note = document.createElement('div');
    note.className = 'cal-overflow-note';
    note.innerHTML = `
      ${beyond} scheduled ${beyond === 1 ? 'show' : 'shows'} fall after
      ${esc(fmtMonth(horizon))} — mostly recurring dates booked far ahead.
      <br><button class="cal-overflow-btn" type="button">See them in List view</button>`;
    note.querySelector('.cal-overflow-btn').addEventListener('click', () => setView('list'));
    body.appendChild(note);
  }
}

// ── List Render ────────────────────────────────────────────────────
function renderList() {
  const events = filteredEvents();
  const dateMap = byDate(events);
  const today = todayISO();
  const body = $('list-body');
  body.innerHTML = '';

  const sortedDates = [...dateMap.keys()].filter(d => d >= today).sort();

  for (const iso of sortedDates) {
    const evs = dateMap.get(iso);
    const group = document.createElement('div');
    group.className = 'list-date-group';

    const label = document.createElement('div');
    label.className = 'list-date-label';
    label.textContent = parseDate(iso).toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
    group.appendChild(label);

    for (const ev of evs) {
      const row = document.createElement('div');
      row.className = 'list-event-row';
      // Venue is null on some sources — drop the line rather than print "null".
      const subParts = [
        ev.venue ? esc(ev.venue) : '',
        ev.genre ? `<span style="color:var(--accent);font-size:11px">${esc(ev.genre)}</span>` : '',
      ].filter(Boolean);
      row.innerHTML = `
        <div class="list-ev-time">${fmtTime(ev.start_time)}</div>
        <div class="list-ev-body">
          <div class="list-ev-title">${esc(ev.title)}</div>
          ${subParts.length ? `<div class="list-ev-venue">${subParts.join(' · ')}</div>` : ''}
        </div>
        <div class="list-ev-right">
          <span class="city-badge ${cityClass(ev.city)}">${esc(cityLabel(ev.city))}</span>
          ${ev.price ? `<span class="list-ev-price">${esc(ev.price)}</span>` : ''}
        </div>`;
      const url = httpUrl(ev.ticket_url);
      if (url) row.addEventListener('click', () => window.open(url, '_blank', 'noopener'));
      group.appendChild(row);
    }
    body.appendChild(group);
  }
}

// ── Day Panel ──────────────────────────────────────────────────────
function openDayPanel(iso) {
  currentDayISO = iso;
  expandedBubbleId = null;
  renderDayPanel();
  const panel = $('day-panel');
  panel.classList.add('open');
  panel.removeAttribute('aria-hidden');
}

function closeDayPanel() {
  $('day-panel').classList.remove('open');
  $('day-panel').setAttribute('aria-hidden', 'true');
  currentDayISO = null;
  expandedBubbleId = null;
}

function shiftDay(dir) {
  const d = parseDate(currentDayISO);
  d.setDate(d.getDate() + dir);
  currentDayISO = toISO(d);
  expandedBubbleId = null;
  renderDayPanel();
}

function renderDayPanel() {
  $('day-panel-title').textContent = fmtDay(currentDayISO);

  const events = filteredEvents();
  const dateMap = byDate(events);
  const evs = dateMap.get(currentDayISO) || [];
  const grid = $('bubble-grid');
  grid.innerHTML = '';

  if (evs.length === 0) {
    const msg = document.createElement('div');
    msg.className = 'no-events-msg';
    msg.textContent = 'No shows this day.';
    grid.appendChild(msg);
    return;
  }

  // Group by city dynamically (cities are already normalised at load)
  const cityMap = new Map();
  evs.forEach(e => {
    const c = e.city || UNKNOWN_CITY;
    if (!cityMap.has(c)) cityMap.set(c, []);
    cityMap.get(c).push(e);
  });
  // Sort: the geographic order used by the filter pills, unknowns last
  const sortedCities = [...cityMap.keys()].sort((a, b) => {
    if (a === UNKNOWN_CITY) return 1;
    if (b === UNKNOWN_CITY) return -1;
    const ia = CITY_ORDER.indexOf(a), ib = CITY_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b);
  });
  const sections = sortedCities.map(c => ({
    label:  c,
    cls:    cityClass(c),
    events: cityMap.get(c),
  }));

  let globalIdx = 0;
  sections.forEach(sec => {
    const section = document.createElement('div');
    section.className = 'city-section';

    // Only show city header when there are multiple sections
    if (sections.length > 1) {
      const hdr = document.createElement('div');
      hdr.className = `city-section-header ${sec.cls}`;
      hdr.textContent = sec.label;
      section.appendChild(hdr);
    }

    sec.events.forEach(ev => {
      section.appendChild(buildBubble(ev, globalIdx++));
    });

    grid.appendChild(section);
  });
}

function buildBubble(ev, idx) {
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.dataset.idx = idx;

  // ── Compact row (always visible) ──────────────────────────────
  const compactRow = document.createElement('div');
  compactRow.className = 'bubble-compact-row';

  // Small square thumbnail
  const thumbWrap = document.createElement('div');
  thumbWrap.className = 'bubble-thumb-wrap';
  if (ev.image_url) {
    const img = document.createElement('img');
    img.className = 'bubble-thumb';
    img.src = ev.image_url;
    img.alt = '';
    img.loading = 'lazy';
    img.onerror = function() { this.replaceWith(makeJazzCup()); };
    thumbWrap.appendChild(img);
  } else {
    thumbWrap.appendChild(makeJazzCup());
  }
  compactRow.appendChild(thumbWrap);

  // Text
  const text = document.createElement('div');
  text.className = 'bubble-text';
  text.innerHTML = `
    ${ev.start_time ? `<div class="bubble-time">${fmtTime(ev.start_time)}</div>` : ''}
    <div class="bubble-title">${esc(ev.title)}</div>
    ${ev.venue ? `<div class="bubble-venue">${esc(ev.venue)}</div>` : ''}
    <div class="bubble-pills">
      ${ev.genre ? `<span class="genre-pill">${esc(ev.genre)}</span>` : ''}
      <span class="city-badge ${cityClass(ev.city)}">${esc(cityLabel(ev.city))}</span>
    </div>`;
  compactRow.appendChild(text);
  bubble.appendChild(compactRow);

  // ── Expanded body (revealed on click) ─────────────────────────
  const expandedBody = document.createElement('div');
  expandedBody.className = 'bubble-expanded-body';

  // Inner wrapper: image + details side by side
  const expandedInner = document.createElement('div');
  expandedInner.className = 'bubble-expanded-inner';

  // 216px image
  const fullImgWrap = document.createElement('div');
  fullImgWrap.className = 'bubble-full-img-wrap';
  if (ev.image_url) {
    const img = document.createElement('img');
    img.className = 'bubble-full-img';
    img.src = ev.image_url;
    img.alt = ev.title;
    img.loading = 'lazy';
    img.onerror = function() { this.replaceWith(makeJazzCup()); };
    fullImgWrap.appendChild(img);
  } else {
    fullImgWrap.appendChild(makeJazzCup());
  }
  expandedInner.appendChild(fullImgWrap);
  expandedBody.appendChild(expandedInner);

  // Expanded details
  const expandedContent = document.createElement('div');
  expandedContent.className = 'bubble-expanded-content';
  expandedContent.innerHTML = `
    ${ev.start_time ? `<div class="bubble-exp-time">${fmtTime(ev.start_time)}</div>` : ''}
    <div class="bubble-exp-title">${esc(ev.title)}</div>
    ${ev.venue ? `<div class="bubble-exp-venue">${esc(ev.venue)}</div>` : ''}
    <div class="bubble-exp-pills">
      ${ev.genre ? `<span class="genre-pill">${esc(ev.genre)}</span>` : ''}
      <span class="city-badge ${cityClass(ev.city)}">${esc(cityLabel(ev.city))}</span>
    </div>
    ${ev.price ? `<div class="bubble-detail-row"><span class="bubble-price">${esc(ev.price)}</span></div>` : ''}`;

  const actions = document.createElement('div');
  actions.className = 'bubble-actions';
  const ticketUrl = httpUrl(ev.ticket_url);
  if (ticketUrl) {
    const a = document.createElement('a');
    a.className = 'bubble-btn primary';
    a.href = ticketUrl;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = 'Get Tickets →';
    a.addEventListener('click', e => e.stopPropagation());
    actions.appendChild(a);
  }
  expandedContent.appendChild(actions);
  expandedInner.appendChild(expandedContent);
  bubble.appendChild(expandedBody);

  bubble.addEventListener('click', () => toggleBubble(bubble));
  return bubble;
}

function toggleBubble(bubble) {
  const isExpanded = bubble.classList.contains('expanded');
  document.querySelectorAll('#bubble-grid .bubble.expanded').forEach(b => b.classList.remove('expanded'));
  if (!isExpanded) {
    bubble.classList.add('expanded');
    bubble.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

// ── Day panel nav ──────────────────────────────────────────────────
$('day-back').addEventListener('click', closeDayPanel);
$('day-close').addEventListener('click', closeDayPanel);
$('day-prev').addEventListener('click', () => shiftDay(-1));
$('day-next').addEventListener('click', () => shiftDay(1));
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDayPanel(); });

// ── Bootstrap ──────────────────────────────────────────────────────
fetch('events.json')
  .then(r => r.json())
  .then(data => {
    ALL_EVENTS = normalizeEvents(data.events || []);
    renderCityFilter();
    renderCalendar();
    renderList();

    requestAnimationFrame(() => {
      const todayMon = toISO(weekMonday(new Date()));
      for (const row of document.querySelectorAll('.week-row')) {
        const first = row.querySelector('.day-col');
        if (first && first.dataset.iso === todayMon) {
          row.scrollIntoView({ behavior: 'instant', block: 'start' });
          break;
        }
      }
    });
  })
  .catch(err => {
    $('cal-body').innerHTML = `<div style="padding:48px;color:var(--mid);text-align:center">Could not load events.json</div>`;
    console.error(err);
  });
