// web/weather.js

TILES['weather'] = {
  html(tile) {
    const id  = tile.id;
    return `
    ${_tileHeader(id,
      `WEATHER <span id="${id}-label" style="font-size:10px;color:var(--text2)"></span>`,
      `<button class="icon-btn weather-cfg-btn" onclick="weatherSettings('${id}')" title="Settings">⚙</button>`
    )}
    <div style="display:flex;flex-direction:column;gap:0;padding:0 14px 14px" id="${id}-body">
      <div class="weather-current" style="position:relative;overflow:hidden"></div>
      <div class="weather-stats" style="display:none;gap:0;border-top:1px solid var(--border);padding-top:8px"></div>
      <div id="${id}-forecast" style="display:none;justify-content:space-between;border-top:1px solid var(--border);padding-top:8px;margin-top:8px"></div>
    </div>`;
  },
  bind(id) { weatherLoad(id); },
  events(id) {
    const st = {};
    onEvent('weather.updated', evt => {
      _weatherOnCurrent(id, st, evt);
      _weatherOnForecast(id, st, evt);
    });
    onEvent('weather.cleared', () => _weatherClearDisplay(id));
  }
};

function weatherLoad(id) {
  fetch('/api/config?target=weather')
    .then(r => r.json())
    .then(d => {
      const location = d.location || d.current?.location || '';
      const label = document.getElementById(`${id}-label`);
      if (label) label.textContent = location;
      if (!location) { _weatherShowUnconfigured(id); return; }
      if (d.current)  _weatherOnCurrent(id, {}, d.current);
      if (d.forecast) _weatherOnForecast(id, {}, d.forecast);
    })
    .catch(() => {});
}

function weatherRefresh(id, location) {
  const loc = (location || '').trim();
  const args = loc && loc !== 'weather' ? ['--location', loc] : [];
  runOp(['weather', 'current',  ...args]);
}

let _weatherCfgPop = null;

async function weatherSettings(id) {
  if (_weatherCfgPop) { weatherCloseSettings(); return; }
  const tileEl = tileRefs[id] || document.querySelector(`.tile[data-id="${id}"]`);
  if (!tileEl) return;
  let cfg = {};
  try { cfg = await (await fetch('/api/config?target=weather')).json(); } catch (_) {}
  const pop = document.createElement('div');
  pop.id = 'dp-pop';
  const scroll = document.createElement('div');
  scroll.className = 'dp-scroll';
  scroll.innerHTML = `
    <div class="dp-meta" style="padding:4px 10px 3px;text-transform:uppercase;letter-spacing:.08em;font-size:9px;font-weight:700;color:var(--accent)">weather</div>
    <div class="dp-row"><div class="dp-row-pick" style="display:flex;align-items:center" onclick="event.stopPropagation()">
      <span class="dp-row-dim">location</span>
      <input id="weather-location-${id}" type="text" style="margin-left:auto;font-family:var(--mono);font-size:10px;width:120px;background:var(--surface,transparent);border:1px solid var(--border2);color:var(--text);border-radius:4px;padding:1px 4px;outline:none" placeholder="city or town">
    </div></div>`;
  pop.appendChild(scroll);
  const input = scroll.querySelector(`#weather-location-${id}`);
  if (input) input.value = cfg.location || cfg.current?.location || '';
  const rect = tileEl.getBoundingClientRect();
  const hdrH = 44;
  let top = rect.top + hdrH, left = rect.left;
  const popW = Math.max(200, Math.min(rect.width, 320));
  if (top + 220 > window.innerHeight - 8) top = rect.top - 4;
  if (left + popW > window.innerWidth - 4) left = window.innerWidth - popW - 4;
  if (left < 4) left = 4;
  if (top < 4) top = 4;
  pop.style.cssText = `position:fixed;z-index:9000;top:${top}px;left:${left}px;width:${popW}px;max-height:220px;display:flex;flex-direction:column;background:var(--surface2);border:1px solid var(--border2);border-radius:10px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.4)`;
  document.body.appendChild(pop);
  _weatherCfgPop = pop;
  setTimeout(() => document.addEventListener('mousedown', _weatherCfgOutside, true), 10);
  document.addEventListener('keydown', _weatherCfgEsc);
  input.onkeydown = e => {
    e.stopPropagation();
    if (e.key === 'Enter') weatherSaveLocation(id);
    if (e.key === 'Escape') weatherCloseSettings();
  };
  input?.focus();
  input?.select();
}

async function weatherSaveLocation(id) {
  const input = document.getElementById(`weather-location-${id}`);
  const value = input?.value.trim() || '';
  const label = document.getElementById(`${id}-label`);
  if (label) label.textContent = value;
  weatherCloseSettings();
  if (value) {
    weatherRefresh(id, value);
  } else {
    runOp(['weather', 'clear-location']);
    _weatherClearDisplay(id);
  }
}

function weatherCloseSettings() {
  if (_weatherCfgPop) { _weatherCfgPop.remove(); _weatherCfgPop = null; }
  document.removeEventListener('mousedown', _weatherCfgOutside, true);
  document.removeEventListener('keydown', _weatherCfgEsc);
}
function _weatherCfgOutside(e) {
  if (!_weatherCfgPop) return;
  if (e.target.closest?.('.weather-cfg-btn')) return;
  if (!_weatherCfgPop.contains(e.target)) weatherCloseSettings();
}
function _weatherCfgEsc(e) { if (e.key === 'Escape') weatherCloseSettings(); }

function _weatherClearDisplay(id) {
  _weatherShowUnconfigured(id);
}

function _weatherShowUnconfigured(id) {
  const body = document.getElementById(`${id}-body`);
  if (!body) return;
  body.querySelector('.weather-current').innerHTML = `<div class="muted">location not configured — <a onclick="weatherSettings('${id}')" style="color:var(--accent);cursor:pointer">set up</a></div>`;
  body.querySelector('.weather-stats').innerHTML = '';
  body.querySelector('.weather-stats').style.display = 'none';
  body.querySelector(`#${id}-forecast`).innerHTML = '';
  body.querySelector(`#${id}-forecast`).style.display = 'none';
  setDot(id, 'gray');
}

function _weatherOnCurrent(id, st, evt) {
  st.current = evt;
  const lbl = document.getElementById(`${id}-label`);
  if (lbl && evt.location) lbl.textContent = evt.location;
  const body = document.getElementById(`${id}-body`);
  if (!body) return;
  setDot(id, 'green');
  body.querySelector('.weather-stats').style.display = 'flex';
  const c = evt;
  body.querySelector('.weather-current').innerHTML = `
    <div style="position:relative;overflow:hidden;padding-bottom:8px">
      <img id="${id}-cityimg-el" style="position:absolute;top:0;right:0;width:75%;height:100%;object-fit:cover;-webkit-mask-image:linear-gradient(to right,transparent 0%,black 25%);mask-image:linear-gradient(to right,transparent 0%,black 25%);display:none;pointer-events:none;z-index:0">
      <div style="position:relative;z-index:1">
        <div style="font-size:28px;font-weight:700;line-height:1">${c.temp}°</div>
        <div style="font-size:10px;color:var(--text2);margin-top:2px">${_wmoLabel(parseInt(c.wmo_code))}</div>
        <div style="font-size:9px;color:var(--text3);font-family:var(--mono);margin-top:1px">${c.location || ''}</div>
      </div>
    </div>`;

  body.querySelector('.weather-stats').innerHTML = `
    ${_stat('Feels', `${c.feels}°`)}
    ${_stat('Humidity', `${c.humidity}%`)}
    ${_stat('Wind', `${c.wind_kmh} km/h`)}`;

  setTimeout(() => {
    const el = document.getElementById(`${id}-cityimg-el`);
    if (!el || !evt.location) return;
    fetch(`https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrnamespace=6&gsrsearch=${encodeURIComponent(evt.location)}+skyline&gsrlimit=1&prop=imageinfo&iiprop=url&iiurlwidth=600&format=json&origin=*`)
      .then(r => r.json())
      .then(d => {
        const url = d.query.pages[Object.keys(d.query.pages)[0]].imageinfo[0].url;
        el.src = url;
        el.onload = () => el.style.display = 'block';
      })
      .catch(() => {});
  }, 0);
}

function _weatherOnForecast(id, st, evt) {
  st.hours = (evt.hours || '').split('|').filter(Boolean).map((l, i) => {
    const [time, temp, condition, wmo_code] = l.split(',');
    const item = { time, temp: parseInt(temp), condition, wmo_code: parseInt(wmo_code) };
    return item;
  });
  const el = document.getElementById(`${id}-forecast`);
  if (!el) return;
  el.style.display = 'flex';
  el.innerHTML = st.hours.map(h => `
    <div style="display:flex;flex-direction:column;align-items:center;gap:2px">
      <span style="font-size:9px;font-family:var(--mono);color:var(--text3)">${h.time}</span>
      <span style="font-size:9px;color:var(--text2)">${h.condition}</span>
      <span style="font-size:10px;font-family:var(--mono);color:var(--text2)">${h.temp}°</span>
    </div>`).join('');
}

function _stat(label, val) {
  return `<div style="flex:1;display:flex;flex-direction:column;gap:2px">
    <span style="font-size:9px;color:var(--text3);font-family:var(--mono);text-transform:uppercase;letter-spacing:.06em">${label}</span>
    <span style="font-size:13px;font-weight:600">${val}</span>
  </div>`;
}

// WMO code → label
const _WMO = {
  0:'clear sky',        1:'mainly clear',             2:'partly cloudy',          3:'overcast',
  45:'fog',             48:'rime fog',
  51:'light drizzle',   53:'drizzle',                 55:'heavy drizzle',
  56:'light freezing drizzle', 57:'freezing drizzle',
  61:'light rain',      63:'rain',                    65:'heavy rain',
  66:'light freezing rain', 67:'freezing rain',
  71:'light snow',      73:'snow',                    75:'heavy snow',
  77:'snow grains',
  80:'light showers',   81:'showers',                 82:'heavy showers',
  85:'light snow showers', 86:'snow showers',
  95:'thunderstorm',    96:'light thunderstorm with hail', 99:'thunderstorm with hail',
};

// WMO code → emoji (7 groups)
function _wmoEmoji(code) {
  if ([0,1].includes(code))                                    return '☀️';
  if ([2,3].includes(code))                                    return '☁️';
  if ([45,48].includes(code))                                  return '🌫️';
  if ([51,53,55,56,57].includes(code))                        return '🌦️';
  if ([61,63,65,66,67,80,81,82].includes(code))               return '🌧️';
  if ([71,73,75,77,85,86].includes(code))                     return '❄️';
  if ([95,96,99].includes(code))                              return '⛈️';
  return '';
}

function _wmoLabel(code) { return _WMO[code] || 'unknown'; }
