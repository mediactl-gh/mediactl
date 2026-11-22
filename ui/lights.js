// web/lights.js

TILES['lights-control'] = {
  html(tile) {
    const id    = tile.id;
    const cfg   = tile.config || {};
    const label = cfg.label || '—';
    return `
    ${_tileHeader(id,
      `<span onclick="lightsOpenPicker('${id}')" title="Select lights" style="cursor:pointer">LIGHTS</span> <span id="${id}-label" onclick="renameTileLabel('${id}')" title="Rename tile"
        style="cursor:pointer;font-size:10px;color:var(--text2)">${label}</span>`,
      `<button class="icon-btn" onclick="lightsSettings('${id}')" title="Connect lights">⚙</button>`
    )}
    <div style="display:flex;flex-direction:column;justify-content:safe center;gap:8px;overflow:auto;flex:1;padding:0 14px 14px;min-height:0;min-width:0" id="${id}-body">
      <div class="muted">—</div>
    </div>`;
  },
  bind(id) { _lightsConnLabel(id); },
  events(id) {}
};

const _lightsState = {};
const _lightsIr = {};
const _lightsAcDefault = {on:false, temp:24, mode:0, fan:0};

// Ordered matching: IR ACs have no feedback; other ACs retain their indicators.
const _lightsComponents = {
  infrared_ac: {
    matches: d => d.category === 'infrared_ac' || (d.deviceType === 'ac' && d.source === 'ir'),
    render: (id, d) => _lightsAcRow(id, d, false),
    isOn: () => false,
  },
  ac: {
    matches: d => d.deviceType === 'ac' || d.kind === 'ac',
    render: (id, d) => _lightsAcRow(id, d, true),
    isOn: d => d.state.on,
  },
  light: {
    matches: d => d.deviceType === 'light',
    render: (id, d) => _lightsLightRow(id, d),
    isOn: d => d.state.on,
  },
};
function _lightsComponent(device) {
  return Object.values(_lightsComponents).find(component => component.matches(device));
}

(function _lightsRegisterGlobalHandlers() {
  onEvent('lights', evt => {
    document.querySelectorAll('.tile-lights-control').forEach(el => {
      const tileId = el.dataset.id;
      _lightsApply(tileId, evt);
    });
  });
  onEvent('lights.linked', () => {
    document.querySelectorAll('.tile-lights-control').forEach(el => _lightsConnLabel(el.dataset.id));
  });
  onEvent('ir.sent', () => { _lightsSyncAc().catch(() => {}); });
  onEvent('smartlife.qr',        evt => _lightsConnectQR(evt.token));
  onEvent('smartlife.error',     evt => { _lightsCloseQR(); _lightsConnectStatus('failed — ' + (evt.msg || 'error')); });
  onEvent('smartlife.connected', ()  => { _lightsCloseQR(); _lightsConnectStatus('connected'); _lightsCloseSettings(); });
})();


function lightsOpenPicker(id) {
  const tile = dashboard?.tiles?.find(t => t.id === id);
  if (!tile) return;
  if (!tile.config) tile.config = {};
  const cur = tile.config.devices ?? [...Object.keys(_lightsState[id] || {}), ...(_lightsIr[id] || []).map(d => d.name)];
  openDevicePicker(id, 'lights', {
    multi: true,
    selectedKeys: cur,
    onApply: async picked => {
      tile.config.devices = picked;
      _lightsRender(id);
      if (typeof saveDashboard === 'function') await saveDashboard();
    },
  });
}


// ── Connect (Smart Life QR) ─────────────────────────────────────────────────
let _lightsCfgPop = null;
let _lightsQRPop  = null;
const _LS_META  = `padding:8px 10px 3px;text-transform:uppercase;letter-spacing:.08em;
  font-size:9px;font-weight:700;color:var(--accent)`;
const _LS_INPUT = `width:100%;font-family:var(--mono);font-size:10px;background:var(--surface);
  border:1px solid var(--border2);color:var(--text);border-radius:4px;padding:3px 5px;outline:none;box-sizing:border-box`;

async function lightsSettings(id) {
  if (_lightsCfgPop) { _lightsCloseSettings(); return; }
  const tileEl = tileRefs[id] || document.querySelector(`.tile[data-id="${id}"]`);
  if (!tileEl) return;
  const scfg = await fetch('/api/config?target=smartlife').then(r => r.json()).catch(() => ({}));

  const pop = document.createElement('div');
  pop.id = 'dp-pop';
  const scroll = document.createElement('div');
  scroll.className = 'dp-scroll';
  scroll.innerHTML = _lightsSettingsHTML(id, scfg);
  pop.appendChild(scroll);

  const rect = tileEl.getBoundingClientRect();
  let top = rect.top + 44, left = rect.left;
  const popW = Math.max(220, Math.min(rect.width, 320));
  if (top + 280 > window.innerHeight - 8) top = rect.top - 4;
  if (top < 4) top = 4;
  if (left + popW > window.innerWidth - 4) left = window.innerWidth - popW - 4;
  if (left < 4) left = 4;
  pop.style.cssText = `position:fixed;z-index:9000;top:${top}px;left:${left}px;width:${popW}px;
    max-height:340px;display:flex;flex-direction:column;background:var(--surface2);
    border:1px solid var(--border2);border-radius:10px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.4)`;
  document.body.appendChild(pop);
  _lightsCfgPop = pop;
  setTimeout(() => document.addEventListener('mousedown', _lightsCfgOutside, true), 10);
  document.addEventListener('keydown', _lightsCfgEsc);
}

function _lightsCloseSettings() {
  if (_lightsCfgPop) { _lightsCfgPop.remove(); _lightsCfgPop = null; }
  document.removeEventListener('mousedown', _lightsCfgOutside, true);
  document.removeEventListener('keydown', _lightsCfgEsc);
}
function _lightsCfgOutside(e) {
  if (!_lightsCfgPop) return;
  if (e.target.closest?.('[title="Connect lights"]')) return;
  if (!_lightsCfgPop.contains(e.target)) _lightsCloseSettings();
}
function _lightsCfgEsc(e) { if (e.key === 'Escape') _lightsCloseSettings(); }

function _lightsSettingsHTML(id, scfg) {
  const sConn   = !!(scfg && scfg.access_token);
  return `
    <div class="dp-meta" style="${_LS_META};padding-top:4px">smart light</div>

    <div id="ls-smartlife">
      <div class="dp-meta" style="${_LS_META};border-top:1px solid var(--border2);margin-top:4px">smart life</div>
      <div style="padding:2px 10px 8px;display:flex;flex-direction:column;gap:6px">
        ${sConn ? `<div style="font-size:10px;color:var(--green)">● connected</div>`
                : `<div class="muted" style="font-size:10px">enter your Smart Life user code, then scan the QR in the app</div>`}
        <input id="ls-code" style="${_LS_INPUT}" placeholder="user code (app → Me → Settings)" autocomplete="off" spellcheck="false">
        <div id="ls-status" style="font-size:10px;min-height:14px;color:var(--text3)"></div>
        <button class="btn accent" style="width:100%" onclick="lightsConnect('${id}')">${sConn ? 'Reconnect' : 'Connect'}</button>
      </div>
    </div>`;
}

async function _lightsConnLabel(id) {
  const body = document.getElementById(`${id}-body`);
  if (!body) return;
  let scfg = {};
  try { scfg = await (await fetch('/api/config?target=smartlife')).json(); } catch (_) {}
  const connected = !!scfg.access_token;
  await _lightsLoadIr(id);
  if (connected) { lightsHydrate(id); return; }
  if (_lightsIr[id].length) { _lightsRender(id); return; }
  body.innerHTML = `<div class="muted">not connected — <a onclick="lightsSettings('${id}')" style="color:var(--accent);cursor:pointer">set up</a></div>`;
}

async function _lightsLoadIr(id) {
  try {
    const [devices, bindings, saved] = await Promise.all([
      fetch('/api/config?target=devices').then(r => r.json()),
      fetch('/api/config?target=ir').then(r => r.json()),
      fetch('/api/config?target=ac').then(r => r.json()),
    ]);
    _lightsIr[id] = [...(devices.ac || []),
      ...(devices.ir || []).filter(d => d.category === 'infrared_ac' || d.kind === 'ac')]
      .map(d => ({...d, deviceType:'ac', keys:bindings[d.identifier]?.keys || [],
        state:{..._lightsAcDefault, ...saved[d.identifier]}}));
  } catch (_) { _lightsIr[id] = []; }
}

async function lightsConnect(id) {
  const code = document.getElementById('ls-code')?.value.trim();
  if (!code) { _lightsConnectStatus('user code is required'); return; }
  _lightsConnectStatus('requesting QR…');
  await startProc('lights-connect', ['lights', 'connect', code], { mirror: false });
}

function _lightsConnectStatus(text) {
  const st = document.getElementById('ls-status');
  if (st) st.textContent = text;
}

function _lightsConnectQR(token) {
  _lightsConnectStatus('scan in the Smart Life app…');
  if (typeof QRCode === 'undefined' || !token) return;
  _lightsCloseQR();

  const pop = document.createElement('div');
  pop.style.cssText = `position:fixed;z-index:9500;top:50%;left:50%;transform:translate(-50%,-50%);
    display:flex;flex-direction:column;align-items:center;gap:10px;padding:16px;
    background:var(--surface2);border:1px solid var(--border2);border-radius:10px;
    box-shadow:0 8px 24px rgba(0,0,0,.4)`;
  pop.innerHTML = `<div style="font-size:9px;color:var(--text3);font-family:var(--mono);text-transform:uppercase;letter-spacing:.08em">scan in the Smart Life app</div>
    <div id="ls-qr"></div>`;
  document.body.appendChild(pop);
  _lightsQRPop = pop;

  new QRCode(pop.querySelector('#ls-qr'), {
    text:         token,
    width:        160, height: 160,
    colorDark:    '#e8eaf0', colorLight: '#181b22',
    correctLevel: QRCode.CorrectLevel.M,
  });

  setTimeout(() => document.addEventListener('mousedown', _lightsQROutside, true), 10);
  document.addEventListener('keydown', _lightsQREsc);
}

function _lightsCloseQR() {
  if (_lightsQRPop) { _lightsQRPop.remove(); _lightsQRPop = null; }
  document.removeEventListener('mousedown', _lightsQROutside, true);
  document.removeEventListener('keydown', _lightsQREsc);
}
function _lightsQROutside(e) { if (_lightsQRPop && !_lightsQRPop.contains(e.target)) _lightsCloseQR(); }
function _lightsQREsc(e) { if (e.key === 'Escape') _lightsCloseQR(); }


async function lightsRefresh(id) {
  const devs = dashboard.tiles.find(t => t.id === id)?.config?.devices;
  if (Array.isArray(devs) && devs.length) {
    runOp(['lights', 'status', '--device', devs.join(',')]);
  } else {
    runOp(['lights', 'status']);
  }
}

function lightsDeviceRenamed(oldName, newName) {
  document.querySelectorAll('.tile-lights-control').forEach(el => {
    const state = _lightsState[el.dataset.id];
    if (!state?.[oldName]) return;
    state[newName] = { ...state[oldName], ...state[newName], name: newName };
    delete state[oldName];
    _lightsRender(el.dataset.id);
  });
}

async function lightsHydrate(id) {
  let saved;
  try { saved = await (await fetch('/api/config?target=light')).json(); } catch (_) { saved = {}; }
  Object.values(saved || {}).forEach(state => {
    _lightsApply(id, { event: 'status', ...state });
  });
  _lightsRender(id);
}



function _lightsApply(id, evt) {
  if (!_lightsState[id]) _lightsState[id] = {};
  const { name } = evt; if (!name) return;
  const prev = _lightsState[id][name] || {};
  switch (evt.event) {
    case 'on':         _lightsState[id][name] = { ...prev, on: true, mode: evt.mode || prev.mode, h: evt.h != null ? parseInt(evt.h) : prev.h, s: prev.s }; break;
    case 'off':        _lightsState[id][name] = { ...prev, on: false }; break;
    case 'brightness': _lightsState[id][name] = { ...prev, on: true, bright: parseInt(evt.pct) || prev.bright || 80 }; break;
    case 'status':     _lightsState[id][name] = { ...prev, available: evt.available === true || evt.available === 'True', on: evt.on === true || evt.on === 'True', mode: evt.mode || prev.mode || '?', bright: parseInt(evt.bright) || prev.bright || 80, error: evt.error || null }; break;
    case 'scene':
    case 'error':      return;
    default: return;
  }
  _lightsRender(id);
}

function _lightsRender(id) {
  const body = document.getElementById(`${id}-body`);
  if (!body) return;
  const selected = dashboard.tiles.find(t => t.id === id)?.config?.devices;
  const visible = name => !Array.isArray(selected) || !selected.length || selected.includes(name);
  const devices = [
    ...Object.entries(_lightsState[id] || {}).map(([name, state]) => ({name, state, deviceType:'light'})),
    ...(_lightsIr[id] || []),
  ].filter(d => visible(d.name));
  const rows = devices.map(device => ({device, component:_lightsComponent(device)}))
    .filter(row => row.component);
  setDot(id, rows.some(({device, component}) => component.isOn(device)) ? 'amber' : '');
  body.innerHTML = rows.length
    ? rows.map(({device, component}) => component.render(id, device)).join('')
    : '<div class="muted">no devices</div>';
}

function _lightsLightRow(id, {name, state:s}) {
  return `
    <div class="lights-row">
      <span class="lights-name">${name}</span>
      <button class="icon-btn" title="Colour"
        onclick="lightsColorPop('${id}','${name}',this)">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="8" cy="8" r="7" stroke="var(--text2)" stroke-width="1.2"/>
          <path d="M8 1 A7 7 0 0 1 15 8" stroke="#f74f4f" stroke-width="2.5" stroke-linecap="round" fill="none"/>
          <path d="M15 8 A7 7 0 0 1 8 15" stroke="#f5a623" stroke-width="2.5" stroke-linecap="round" fill="none"/>
          <path d="M8 15 A7 7 0 0 1 1 8" stroke="#34d17a" stroke-width="2.5" stroke-linecap="round" fill="none"/>
          <path d="M1 8 A7 7 0 0 1 8 1" stroke="#4f8ef7" stroke-width="2.5" stroke-linecap="round" fill="none"/>
          <circle cx="8" cy="8" r="2.5" fill="white" opacity="0.9"/>
        </svg>
      </button>
      <input type="range" class="slider" min="1" max="100" value="${s.bright || 80}"
        style="flex:1 1 60px;min-width:0;max-width:100%;height:6px;cursor:pointer"
        onchange="lightsSliderChange('${id}','${name}',this.value)">
      <div class="toggle${s.on ? ' on' : ''}"
        onclick="(el=>runOp(['lights',el.classList.toggle('on')?'on':'off','--device','${name}']))(this)"
        title="Toggle"></div>
    </div>`;
}

function _lightsAcModeIcon(mode) {
  if (mode === 1) return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`;
  if (mode === 2) return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3s6 6.7 6 11a6 6 0 0 1-12 0c0-4.3 6-11 6-11z"/></svg>`;
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M4.9 6l14.2 12M4.9 18L19.1 6M3 12h18"/></svg>`;
}

function _lightsFanIcon(speed) {
  return `<svg width="14" height="14" viewBox="0 0 18 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 12V9"/><path d="M7 12V${speed > 0 ? 6 : 9}" opacity="${speed > 0 ? 1 : .3}"/><path d="M12 12V${speed > 1 ? 3 : 9}" opacity="${speed > 1 ? 1 : .3}"/><path d="M17 12V${speed > 2 ? 1 : 9}" opacity="${speed > 2 ? 1 : .3}"/></svg>`;
}

function _lightsAcRow(id, remote, indicators) {
  const s = remote.state;
  return `<div style="display:flex;align-items:center;gap:6px;padding:4px 0;flex-shrink:0">
    <span style="min-width:70px;max-width:70px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;font-family:var(--font);font-size:12px;font-weight:600;color:var(--text)">${remote.name}</span>
    <button class="icon-btn" title="Mode" onclick="lightsAcCycle('${id}','${remote.name}','mode')" style="width:22px;height:22px;flex-shrink:0;${indicators ? 'color:var(--accent)' : ''}">${indicators ? _lightsAcModeIcon(s.mode) : '↻'}</button>
    <button class="icon-btn" title="Fan speed" onclick="lightsAcCycle('${id}','${remote.name}','fan')" style="width:22px;height:22px;flex-shrink:0">${indicators ? _lightsFanIcon(s.fan) : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="2"/><path d="M10 10C2 3 17 0 14 10M14 10c10-2 5 12-1 4M11 14c-2 10-12-1-1-3"/></svg>'}</button>
    <div style="flex:1;display:flex;align-items:center;justify-content:center;gap:3px;min-width:70px">
      <button class="media-icon-btn" onclick="lightsAcTemp('${id}','${remote.name}',-1)" title="Temperature down">−</button>
      ${indicators ? `<span style="min-width:28px;text-align:center;font:10px var(--mono);color:var(--text2)">${s.temp}°</span>` : ''}
      <button class="media-icon-btn" onclick="lightsAcTemp('${id}','${remote.name}',1)" title="Temperature up">+</button>
    </div>
    ${indicators
      ? `<div class="toggle${s.on?' on':''}" onclick="lightsAcPower('${id}','${remote.name}')" style="flex-shrink:0;margin-left:4px" title="Toggle"></div>`
      : `<button class="icon-btn" onclick="lightsAcPower('${id}','${remote.name}')" style="flex-shrink:0;margin-left:4px" title="Power"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2v10M6 5a9 9 0 1 0 12 0"/></svg></button>`}
  </div>`;
}

function _lightsAc(id, name) { return (_lightsIr[id] || []).find(r => r.name === name); }

async function _lightsSyncAc() {
  const saved = await (await fetch('/api/config?target=ac')).json();
  Object.entries(_lightsIr).forEach(([id, remotes]) => {
    remotes.forEach(r => { r.state = {..._lightsAcDefault, ...saved[r.identifier]}; });
    _lightsRender(id);
  });
}

// Serialize clicks and read back persisted commands, including after a failed send.
let _lightsAcQueue = Promise.resolve();
function _lightsAcCommand(id, name, command) {
  _lightsAcQueue = _lightsAcQueue.then(async () => {
    await _lightsSyncAc();
    const r = _lightsAc(id, name); if (!r) return;
    await runOp(['ir', 'send', ...command(r.state), '--remote', name]);
    await _lightsSyncAc();
  }).catch(() => {});
  return _lightsAcQueue;
}

function lightsAcPower(id, name) {
  return _lightsAcCommand(id, name, s => [s.on ? 'PowerOff' : 'PowerOn']);
}

function lightsAcTemp(id, name, delta) {
  return _lightsAcCommand(id, name, s => ['T', String(Math.max(16, Math.min(30, s.temp + delta)))]);
}

function lightsAcCycle(id, name, field) {
  return _lightsAcCommand(id, name, s => [field === 'mode' ? 'M' : 'F',
    String((s[field] + 1) % (field === 'mode' ? 3 : 4))]);
}

function lightsSliderChange(tileId, name, val) {
  const pct = parseInt(val);
  if (!_lightsState[tileId]) _lightsState[tileId] = {};
  if (!_lightsState[tileId][name]) _lightsState[tileId][name] = {};
  // Always track current brightness in state
  _lightsState[tileId][name].bright = pct;
  const mode = _lightsState[tileId][name].mode || 'white';
  if (mode === 'colour') {
    const h = _lightsState[tileId][name].h ?? 0;
    const s = _lightsState[tileId][name].s ?? 100;
    runOp(['lights', 'colour', String(h), String(s), String(pct), '--device', name]);
  } else {
    runOp(['lights', 'brightness', String(pct), '--device', name]);
  }
}

function lightsColorPop(tileId, name, btn) {
  const popId = `${tileId}-${name}-colorpop`.replace(/\s+/g, '_');
  const existing = document.getElementById(popId);
  if (existing) { existing.remove(); return; }

  const st = (_lightsState[tileId]?.[name]) || {};
  const initMode = st.mode === 'white' ? 'white' : 'colour';

  const pop = document.createElement('div');
  pop.id = popId;
  pop.className = 'popover';

  const btnRect = btn.getBoundingClientRect();
  const left = Math.max(8, Math.min(btnRect.left - 70, window.innerWidth - 184));
  pop.style.cssText = `position:fixed;top:${btnRect.bottom + 6}px;left:${left}px;width:172px;padding:10px 10px 14px;max-height:none;overflow:visible;z-index:9999`;

  // Slider shared HTML helper
  function sliderRow(gradient) {
    return `<div style="position:relative;height:16px;display:flex;align-items:center">
      <div style="position:absolute;top:5px;bottom:5px;left:0;right:0;border-radius:4px;background:${gradient};pointer-events:none"></div>
      <input type="range" min="0" max="100" style="position:relative;width:100%;margin:0;cursor:pointer;appearance:none;-webkit-appearance:none;background:transparent;z-index:1"
    `;
  }

  pop.innerHTML = `
    <div style="font-size:9px;color:var(--text3);font-family:var(--mono);margin-bottom:8px;text-transform:uppercase;letter-spacing:.08em">${name}</div>

    <!-- mode toggle -->
    <div style="display:flex;gap:0;margin-bottom:10px;border:1px solid var(--border2);border-radius:6px;overflow:hidden">
      <button id="${popId}-tab-colour" onclick="lightsPopTab('${popId}','colour','${tileId}','${name}')"
        style="flex:1;padding:4px 0;font-size:10px;font-family:var(--font);font-weight:400;letter-spacing:0;border:none;cursor:pointer;transition:all .12s;text-align:center;
               background:${initMode==='colour'?'var(--accent)':'transparent'};
               color:${initMode==='colour'?'#fff':'var(--text2)'}">Colour</button>
      <button id="${popId}-tab-white" onclick="lightsPopTab('${popId}','white','${tileId}','${name}')"
        style="flex:1;padding:4px 0;font-size:10px;font-family:var(--font);font-weight:400;letter-spacing:0;border:none;border-left:1px solid var(--border2);cursor:pointer;transition:all .12s;text-align:center;
               background:${initMode==='white'?'var(--accent)':'transparent'};
               color:${initMode==='white'?'#fff':'var(--text2)'}">White</button>
    </div>

    <!-- colour panel -->
    <div id="${popId}-panel-colour" style="display:${initMode==='colour'?'flex':'none'};flex-direction:column;gap:8px">
      <canvas id="${popId}-disk" width="152" height="152" style="width:152px;height:152px;display:block;margin:0 auto;cursor:crosshair;border-radius:50%"></canvas>
      <div style="display:flex;flex-direction:column;gap:3px">
        <div style="font-size:9px;color:var(--text3);font-family:var(--mono);letter-spacing:.06em">BRIGHTNESS</div>
        <input type="range" class="slider" min="1" max="100" value="${st.bright ?? 80}" id="${popId}-colour-bright"
          style="width:100%;height:6px;cursor:pointer"
          onchange="lightsColourBright('${tileId}','${name}',this.value,'${popId}')">
      </div>
      <div style="display:flex;flex-direction:column;gap:3px">
        <div style="font-size:9px;color:var(--text3);font-family:var(--mono);letter-spacing:.06em">SATURATION</div>
        <input type="range" class="slider" min="0" max="100" value="${st.s ?? 100}" id="${popId}-colour-sat"
          style="width:100%;height:6px;cursor:pointer"
          onchange="lightsColourSat('${tileId}','${name}',this.value,'${popId}')">
      </div>
    </div>

    <!-- white panel -->
    <div id="${popId}-panel-white" style="display:${initMode==='white'?'flex':'none'};flex-direction:column;gap:8px">
      <div style="display:flex;flex-direction:column;gap:3px">
        <div style="font-size:9px;color:var(--text3);font-family:var(--mono);letter-spacing:.06em">TEMPERATURE</div>
        <div style="position:relative;height:16px;display:flex;align-items:center">
          <div style="position:absolute;top:5px;bottom:5px;left:0;right:0;border-radius:4px;background:linear-gradient(to right,#ff9500,#ffeccc,#cce4ff,#7ab8ff);pointer-events:none"></div>
          <input type="range" min="0" max="100" value="${st.temp ?? 50}" id="${popId}-white-temp"
            style="position:relative;width:100%;margin:0;cursor:pointer;appearance:none;-webkit-appearance:none;background:transparent;z-index:1"
            onchange="lightsWhiteApply('${tileId}','${name}','${popId}')">
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:3px">
        <div style="font-size:9px;color:var(--text3);font-family:var(--mono);letter-spacing:.06em">BRIGHTNESS</div>
        <input type="range" class="slider" min="1" max="100" value="${st.bright ?? 80}" id="${popId}-white-bright"
          style="width:100%;height:6px;cursor:pointer"
          onchange="lightsWhiteApply('${tileId}','${name}','${popId}')">
      </div>
    </div>
  `;

  document.body.appendChild(pop);

  // Draw colour wheel
  const canvas = document.getElementById(`${popId}-disk`);
  const ctx = canvas.getContext('2d');
  const sz = 152, cx = sz / 2, cy = sz / 2, radius = sz / 2 - 1;
  for (let deg = 0; deg < 360; deg++) {
    const a0 = (deg - 0.75) * Math.PI / 180;
    const a1 = (deg + 0.75) * Math.PI / 180;
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
    grad.addColorStop(0,    'white');
    grad.addColorStop(0.08, 'white');
    grad.addColorStop(1,    `hsl(${deg},100%,50%)`);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, radius, a0, a1);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
  }
  ctx.globalCompositeOperation = 'destination-in';
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalCompositeOperation = 'source-over';
  const wheelImage = ctx.getImageData(0, 0, sz, sz);

  // Restore previous marker if colour was already selected
  if (st.mode === 'colour' && st.h != null && st.s != null) {
    const rad = (st.h * Math.PI) / 180;
    const dist = (st.s / 100) * radius;
    _lightsDrawMarker(ctx, wheelImage, cx + dist * Math.cos(rad), cy + dist * Math.sin(rad));
  }

  canvas.addEventListener('click', e => {
    const rect = canvas.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (sz / rect.width);
    const py = (e.clientY - rect.top)  * (sz / rect.height);
    const dx = px - cx, dy = py - cy;
    if (Math.sqrt(dx*dx + dy*dy) > radius) return;
    const h = Math.round(((Math.atan2(dy, dx) * 180 / Math.PI) + 360) % 360);
    const s = Math.min(100, Math.round(Math.sqrt(dx*dx + dy*dy) / radius * 100));
    const v = parseInt(document.getElementById(`${popId}-colour-bright`)?.value ?? 80);
    _lightsDrawMarker(ctx, wheelImage, px, py);
    if (!_lightsState[tileId]) _lightsState[tileId] = {};
    if (!_lightsState[tileId][name]) _lightsState[tileId][name] = {};
    _lightsState[tileId][name] = { ..._lightsState[tileId][name], mode: 'colour', h, s, bright: v };
    runOp(['lights', 'colour', String(h), String(s), String(v), '--device', name]);
  });

  const close = e => {
    if (!pop.contains(e.target) && e.target !== btn) {
      pop.remove();
      document.removeEventListener('click', close);
    }
  };
  setTimeout(() => document.addEventListener('click', close), 0);
}

function _lightsDrawMarker(ctx, wheelImage, px, py) {
  ctx.putImageData(wheelImage, 0, 0);
  ctx.beginPath();
  ctx.arc(px, py, 6, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(0,0,0,0.6)';
  ctx.lineWidth = 2.5;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(px, py, 6, 0, Math.PI * 2);
  ctx.strokeStyle = 'white';
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

function lightsPopTab(popId, mode, tileId, name) {
  const tabColour   = document.getElementById(`${popId}-tab-colour`);
  const tabWhite    = document.getElementById(`${popId}-tab-white`);
  const panelColour = document.getElementById(`${popId}-panel-colour`);
  const panelWhite  = document.getElementById(`${popId}-panel-white`);

  [tabColour, tabWhite].forEach(t => {
    t.style.background = 'transparent';
    t.style.color      = 'var(--text2)';
  });
  const active = mode === 'colour' ? tabColour : tabWhite;
  active.style.background = 'var(--accent)';
  active.style.color      = '#fff';

  panelColour.style.display = mode === 'colour' ? 'flex' : 'none';
  panelWhite.style.display  = mode === 'white'  ? 'flex' : 'none';

  // Apply immediately on switch
  if (tileId && name) {
    if (mode === 'colour') {
      const h = _lightsState[tileId]?.[name]?.h ?? 0;
      const s = _lightsState[tileId]?.[name]?.s ?? 100;
      const v = parseInt(document.getElementById(`${popId}-colour-bright`)?.value ?? 80);
      if (!_lightsState[tileId]) _lightsState[tileId] = {};
      if (!_lightsState[tileId][name]) _lightsState[tileId][name] = {};
      _lightsState[tileId][name].mode = 'colour';
      runOp(['lights', 'colour', String(h), String(s), String(v), '--device', name]);
    } else {
      lightsWhiteApply(tileId, name, popId);
    }
  }
}

function lightsColourBright(tileId, name, val, popId) {
  const pct = parseInt(val);
  if (!_lightsState[tileId]?.[name]) return;
  _lightsState[tileId][name].bright = pct;
  const h = _lightsState[tileId][name].h ?? 0;
  const s = _lightsState[tileId][name].s ?? 100;
  runOp(['lights', 'colour', String(h), String(s), String(pct), '--device', name]);
}

function lightsColourSat(tileId, name, val, popId) {
  const s = parseInt(val);
  if (!_lightsState[tileId]?.[name]) return;
  _lightsState[tileId][name].s = s;
  const h = _lightsState[tileId][name].h ?? 0;
  const v = _lightsState[tileId][name].bright ?? 80;
  runOp(['lights', 'colour', String(h), String(s), String(v), '--device', name]);
}

function lightsWhiteApply(tileId, name, popId) {
  const temp   = parseInt(document.getElementById(`${popId}-white-temp`)?.value  ?? 50);
  const bright = parseInt(document.getElementById(`${popId}-white-bright`)?.value ?? 80);
  if (!_lightsState[tileId]) _lightsState[tileId] = {};
  if (!_lightsState[tileId][name]) _lightsState[tileId][name] = {};
  _lightsState[tileId][name] = { ..._lightsState[tileId][name], mode: 'white', temp, bright };
  runOp(['lights', 'white', String(temp), String(bright), '--device', name]);
}
