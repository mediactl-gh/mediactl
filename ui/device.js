// device.js

// ══════════════════════════════════════════════════════════════════════════
// DEVICE TILE — unified browser-contributed device
// ══════════════════════════════════════════════════════════════════════════
//
// Represents a single browser endpoint (laptop, phone, tablet) and all
// the hardware capabilities it can contribute to the system:
//
//   camera     — video input (getUserMedia videoinput)
//   mic        — audio input (getUserMedia audioinput)
//   speaker    — audio output (enumerateDevices audiooutput)
//   network    — presence / connectivity (navigator.connection, IP)
//   geo        — geolocation (navigator.geolocation)
//   battery    — battery status (navigator.getBattery)
//   sensors    — accelerometer / gyroscope (DeviceMotionEvent)
//   clipboard  — clipboard read/write (navigator.clipboard)
//   notify     — push notifications (Notification API)
//
// The refresh button re-enumerates everything and re-posts to the server.
// Meta row: cam-view preview (same shell as camera tile) + right panel
// (identity line / per-cap actions bar / sync readout).
// Capabilities: two columns of icon + label + value badge, declared in
// DEVICE_CAPS. Tapping a row focuses it (actions bar follows); ops reuse
// camera.js plumbing (_runStart, _captureAndPost, _startMotionLoop).


// ══════════════════════════════════════════════════════════════════════════
// DEVICE DISCOVERY  (endpoint registration → devices[])
// ══════════════════════════════════════════════════════════════════════════
//
// Registers the physical browser endpoint into devices.json devices[].
// web.js _postUserDevices() calls this alongside discoverCameraDevices()
// and discoverAudioDevices().

async function discoverDeviceDevices() {
  const ip   = await _getMyIp();
  const type = _guessDeviceType();
  const caps = await _probeEndpointCaps();

  const devices = [{
    deviceType: type,
    identifier: `push://${ip}`,
    source:     ip,
    capabilities: Object.keys(caps),   // names only — live values go to capabilities.json
  }];

  return { devices };
}

function _guessDeviceType() {
  const ua = navigator.userAgent.toLowerCase();
  if (/ipad|tablet|playbook|silk/.test(ua))          return 'tablet';
  if (/mobile|iphone|ipod|android.*mobile/.test(ua)) return 'mobile';
  return 'desktop';
}


// ── shared endpoint capabilities ──────────────────────────────────────────
// Single source for the non-media capabilities. Consumed by both
// discoverDeviceDevices() (contribution → devices.json) and
// discoverAllDevices() (tile badges), so discovery is never duplicated.
// Add a capability here once and both paths pick it up.

async function _probeEndpointCaps() {
  const caps = {};

  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  caps.network = conn
    ? {
        type:          conn.type,
        effectiveType: conn.effectiveType,
        downlink:      conn.downlink,
        rtt:           conn.rtt,
        saveData:      conn.saveData,
      }
    : { online: navigator.onLine };

  try {
    if (navigator.getBattery) {
      const batt = await navigator.getBattery();
      caps.battery = { level: batt.level, charging: batt.charging };
    }
  } catch (_) {}

  caps.sensors = {
    motion:      'DeviceMotionEvent'      in window,
    orientation: 'DeviceOrientationEvent' in window,
  };

  caps.geo = { available: 'geolocation' in navigator };

  return caps;
}


// ══════════════════════════════════════════════════════════════════════════
// FULL CAPABILITY DISCOVERY  (tile refresh)
// ══════════════════════════════════════════════════════════════════════════
//
// Used by deviceRefresh() to populate the tile's capability badges.
// Not used for registry registration.

async function discoverAllDevices() {
  const ip = await _getMyIp();
  const results = {
    ip,
    userAgent:  navigator.userAgent,
    platform:   navigator.platform || navigator.userAgentData?.platform || '',
    deviceType: _guessDeviceType(),
    cameras:    [],
    mics:       [],
    speakers:   [],
    network:    null,
    geo:        null,
    battery:    null,
    sensors:    { motion: false, orientation: false },
    clipboard:  !!navigator.clipboard,
    notify:     'Notification' in window ? Notification.permission : 'unsupported',
  };

  // ── media devices ────────────────────────────────────────────────────
  try {
    const raw = await navigator.mediaDevices.enumerateDevices();
    raw.forEach((d, i) => {
      const identifier = `push://${ip}`;
      if (d.kind === 'videoinput') {
        const isOrdinal = /^\d+$/.test(d.deviceId);
        results.cameras.push({
          name:        `camera-${results.cameras.length}`,
          identifier:  isOrdinal ? d.deviceId : identifier,
          deviceId:    d.deviceId,
          description: d.label || `camera-${i}`,
          source:      ip,
        });
      }
      if (d.kind === 'audioinput') {
        results.mics.push({
          name:        `mic-${results.mics.length}`,
          identifier,
          deviceId:    d.deviceId,
          description: d.label || `mic-${i}`,
          source:      ip,
        });
      }
      if (d.kind === 'audiooutput') {
        results.speakers.push({
          name:        `speaker-${results.speakers.length}`,
          identifier,
          deviceId:    d.deviceId,
          description: d.label || `speaker-${i}`,
          source:      ip,
        });
      }
    });
  } catch (e) { console.warn('device: enumerateDevices failed', e); }

  // ── endpoint capabilities (network / battery / sensors / geo) ─────────
  // shared with discoverDeviceDevices() — single discovery, no duplication
  const caps = await _probeEndpointCaps();
  Object.assign(results, caps);
  results.caps = caps;   // raw object for the status post / persistence

  return results;
}


// ══════════════════════════════════════════════════════════════════════════
// CAPABILITY REGISTRY
// ══════════════════════════════════════════════════════════════════════════
//
// Single source for rows, value badges, and whitelisted actions.
// badge(info, st)  → { state, text? }  — text defaults to ON/OFF/READY.
// actions[]        → { title, icon, primary?, op(info)→args | local(id, st) }
//   op    — backend op via _runStart (camera.js) → /api/start
//   local — browser-side, runs on this endpoint

const _ICO = {
  play:   `<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`,
  snap:   `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>`,
  motion: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.93 4.93l2.12 2.12M16.95 16.95l2.12 2.12M4.93 19.07l2.12-2.12M16.95 7.05l2.12-2.12"/></svg>`,
  rec:    `<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="6"/></svg>`,
  text:   `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="14" y2="17"/></svg>`,
  wave:   `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`,
  scan:   `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1" fill="currentColor"/></svg>`,
  pin:    `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
};

const DEVICE_CAPS = {
  camera: {
    label: 'CAMERA', icon: _svgCamera,
    badge: i => i.cameras?.length ? { state: 'on', text: String(i.cameras.length) } : { state: 'off' },
    actions: [
      { title: 'Preview', primary: true, icon: _ICO.play,
        local: async (id, st) => { st.active['camera:Preview'] = await _devicePreviewToggle(id); } },
      { title: 'Snapshot', icon: _ICO.snap,
        local: (id, st) => {
          const cam = st.info?.cameras?.[0]?.name;
          if (cam && typeof _captureAndPost === 'function') _captureAndPost(cam);
        } },
      { title: 'Motion detect', icon: _ICO.motion,
        local: (id, st) => {
          const cam = st.info?.cameras?.[0]?.name;
          if (!cam || typeof _startMotionLoop !== 'function') return;
          const k = 'camera:Motion detect';
          if (st.active[k]) { _stopPushLoop(cam, id); _clearMotionTick(id); st.active[k] = false; }
          else              { _startMotionLoop(cam, id); st.active[k] = true; }
        } },
    ],
  },
  mic: {
    label: 'MIC', icon: _svgMic,
    badge: i => i.mics?.length ? { state: 'on', text: String(i.mics.length) } : { state: 'off' },
    actions: [
      { title: 'Record', primary: true, icon: _ICO.rec, op: i => ['audio', 'record', '--device', i.mics[0].name] },
      { title: 'Listen', icon: _ICO.text, op: i => ['audio', 'listen', '--device', i.mics[0].name] },
    ],
  },
  speaker: {
    label: 'SPEAKER', icon: _svgSpeaker,
    badge: i => i.speakers?.length ? { state: 'on', text: String(i.speakers.length) } : { state: 'off' },
    actions: [
      { title: 'Ping', primary: true, icon: _ICO.wave,
        local: () => { try { speechSynthesis.speak(new SpeechSynthesisUtterance('ping')); } catch (_) {} } },
    ],
  },
  network: {
    label: 'NETWORK', icon: _svgNetwork,
    badge: i => {
      const n = i.network;
      if (!n || n.online === false) return { state: 'off' };
      const t = n.effectiveType || n.type;
      return { state: 'on', text: t ? (n.rtt != null ? `${t}·${n.rtt}ms` : t) : undefined };
    },
    actions: [
      { title: 'Scan', primary: true, icon: _ICO.scan, op: i => ['device', 'discover', '--refresh', '--ip', i.ip] },
    ],
  },
  geo: {
    label: 'GEO', icon: _svgGeo,
    badge: (i, st) => st?.geoAcc != null ? { state: 'on', text: `±${st.geoAcc}m` }
                    : (i.geo?.available ? { state: 'ready' } : { state: 'off' }),
    actions: [
      { title: 'Locate', primary: true, icon: _ICO.pin,
        local: (id, st) => {
          navigator.geolocation?.getCurrentPosition(
            p => {
              st.geoAcc = Math.round(p.coords.accuracy);
              st.geoPos = [p.coords.latitude, p.coords.longitude];
              _updateCapBadge(id, 'geo', 'on', `±${st.geoAcc}m`);
            },
            () => _updateCapBadge(id, 'geo', 'off')
          );
        } },
    ],
  },
  battery: {
    label: 'BATTERY', icon: _svgBattery,
    badge: i => i.battery
      ? { state: 'on', text: `${Math.round(i.battery.level * 100)}%${i.battery.charging ? '+' : ''}` }
      : { state: 'na' },
    actions: [],
  },
  sensors: {
    label: 'SENSORS', icon: _svgSensors,
    badge: i => ({ state: i.sensors?.motion ? 'on' : 'off' }),
    actions: [],
  },
  notify: {
    label: 'NOTIFY', icon: _svgNotify,
    badge: i => i.notify === 'granted' ? { state: 'on' }
              : i.notify === 'denied'  ? { state: 'off' } : { state: 'ready' },
    actions: [],
  },
};


// ══════════════════════════════════════════════════════════════════════════
// DEVICE TILE REGISTRATION
// ══════════════════════════════════════════════════════════════════════════

const _deviceState = {};  // id → { discovered, info, live, focus, active, geoAcc, tick }
let _deviceConfig = null, _deviceConfigPending = null, _deviceConfigTs = 0;

async function _deviceLoadConfig(refresh=false) {
  if (_deviceConfigPending) return _deviceConfigPending;
  if (!refresh && _deviceConfig && Date.now() - _deviceConfigTs < 5000) return _deviceConfig;
  _deviceConfigPending = Promise.all([
    fetch('/api/config?target=devices,capabilities').then(r => {
      if (!r.ok) throw new Error('Device config unavailable');
      return r.json();
    }),
    _getMyIp(),
  ]).then(([data, ip]) => {
    _deviceConfigTs = Date.now();
    return (_deviceConfig = {...data, ip});
  }).finally(() => { _deviceConfigPending = null; });
  return _deviceConfigPending;
}

TILES['device'] = {
  html(tile) {
    const id    = tile.id;
    const cfg   = tile.config || {};
    const label = cfg.label || cfg.device || cfg.name || 'this device';
    const ip    = cfg.ip || '';

    return `
    ${_tileHeader(id,
      `<span onclick="openDevicePicker('${id}', 'device')" title="Select device" style="cursor:pointer">DEVICE</span> <span id="${id}-label" style="font-size:10px;color:var(--text2)">${label}</span>`,
      `<button class="icon-btn" onclick="deviceEdit('${id}')" title="Edit device">⚙</button>
       <button class="icon-btn" id="${id}-refresh-btn" onclick="deviceRefresh('${id}')" title="Refresh / rediscover">
         <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
           <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
           <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
         </svg>
       </button>`
    )}
    <div class="device-body" id="${id}-body" style="display:flex;flex-direction:column;gap:6px;padding:4px 10px 10px;height:100%;min-height:0;overflow:hidden">
      <div class="device-meta" id="${id}-meta">
        <div class="cam-view device-preview-cam" id="${id}-preview-wrap">
          <div class="cam-idle" id="${id}-preview-empty">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.25"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
          </div>
          <video id="${id}-preview" class="device-preview-stream" muted playsinline autoplay></video>
          <div class="cam-ts" id="${id}-preview-ts"></div>
        </div>
        <div class="device-meta-main">
          <div class="device-id-row">
            <span id="${id}-device-icon" style="font-size:16px;opacity:0.5;flex-shrink:0">${_deviceTypeIcon('desktop')}</span>
            <span id="${id}-device-info" style="font-size:10px;color:var(--text3,#888);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${ip || 'discovering…'}</span>
          </div>
          <div class="device-action-row">
            <span class="device-running" id="${id}-running"></span>
            <div class="device-actions" id="${id}-actions"></div>
          </div>
          <div class="device-readout" id="${id}-readout"></div>
        </div>
      </div>
      <div class="device-cap-grid stale" id="${id}-cap-grid">
        ${Object.entries(DEVICE_CAPS).map(([cap, d]) => _capRow(id, cap, d.label, d.icon())).join('')}
      </div>
    </div>`;
  },

  bind(id) {
    _deviceRenderPersisted(id);   // instant: last-known names + statuses
  },

  events(id, tile) {
    // listen for device-level SSE events
    onEvent('device', evt => {
      if (!document.getElementById(`${id}-body`)) return;
      switch (evt.event) {
        case 'refresh':      deviceRefresh(id);                break;
        case 'discovered':   _onDeviceDiscovered(id, evt);     break;
        case 'capability':   _onCapabilityUpdate(id, evt);     break;
      }
    });

    // also react to network changes
    window.addEventListener('online',  () => { if (_deviceState[id]?.local) _updateCapBadge(id, 'network', 'on'); });
    window.addEventListener('offline', () => { if (_deviceState[id]?.local) _updateCapBadge(id, 'network', 'off'); });
  }
};


// ══════════════════════════════════════════════════════════════════════════
// REFRESH / REDISCOVER
// ══════════════════════════════════════════════════════════════════════════

async function deviceEdit(id) {
  closeDevicePicker();
  const current = _tileDev(id);
  const devices = await _fetchDevices();
  const raw = (devices.devices || []).find(d => d.name === current);
  if (!raw) { toast('Select a device first', 'err'); return; }
  _dpOpenDeviceFormModal(id, 'device', _dpItem(raw, current), raw);
}

async function deviceRefresh(id) {
  const btn = document.getElementById(`${id}-refresh-btn`);
  if (btn) btn.classList.add('spin');
  setDot(id, 'amber');

  try {
    const current = await _deviceRenderPersisted(id, true);
    if (!current?.ready || !current.local) return;
    const info = await discoverAllDevices();
    if (_deviceState[id] !== current) return;
    for (const key of ['cameras', 'mics', 'speakers']) {
      info[key] = info[key].map(d => {
        const saved = current.info[key].find(s => s.deviceId === d.deviceId);
        return saved ? {...d, name:saved.name} : d;
      });
    }
    _deviceRender(id, {...current.info, ...info, ts:Date.now() / 1000}, true);
    _postDeviceInfo(id, info);
  } catch (e) {
    console.warn('device: refresh failed', e);
    setDot(id, 'red');
    _stopDevicePreview(id);
  } finally {
    if (btn) btn.classList.remove('spin');
  }
}


function _deviceRender(id, info, live=false) {
  const previous = _deviceState[id];
  const changed = previous?.info?.identifier !== info.identifier;
  if (changed) { clearInterval(previous?.tick); _stopDevicePreview(id); }
  const st = _deviceState[id] = {
    ...(changed ? {} : previous), info, live, ready:!!info.identifier,
    local:!!_deviceConfig?.ip && info.identifier === `push://${_deviceConfig.ip}`,
    discovered:info.ts ? info.ts * 1000 : null,
    active:changed ? {} : previous?.active || {},
  };
  const icon = document.getElementById(`${id}-device-icon`);
  const meta = document.getElementById(`${id}-device-info`);
  if (icon) icon.innerHTML = _deviceTypeIcon(info.deviceType);
  if (meta) meta.textContent = `${info.ip || '?'}  ·  ${info.platform || info.deviceType || 'device'}`;
  document.getElementById(`${id}-cap-grid`)?.classList.toggle('stale', !st.ready);
  Object.entries(DEVICE_CAPS).forEach(([cap, d]) => {
    const b = d.badge(info, st) || {};
    _updateCapBadge(id, cap, b.state || 'na', b.text);
  });
  _renderActions(id);
  _renderRunning(id);
  _startSyncTicker(id);
  setDot(id, live ? 'green' : '');
  return st;
}


// ══════════════════════════════════════════════════════════════════════════
// FOCUS + ACTIONS BAR
// ══════════════════════════════════════════════════════════════════════════
//
// Tapping a cap row focuses it (no op fired). The actions bar in the right
// panel follows the focused cap.

function deviceCapFocus(id, cap) {
  const st = _deviceState[id] || (_deviceState[id] = { active: {} });
  st.focus = st.focus === cap ? null : cap;

  // toggled on: highlight this row, gray out all the others
  Object.keys(DEVICE_CAPS).forEach(c => {
    const row = document.getElementById(`${id}-cap-${c}`);
    if (!row) return;
    row.classList.toggle('focused', c === st.focus);
    row.style.opacity = st.focus && c !== st.focus ? '0.45' : '';
  });

  // upper right panel: indicate the toggled cap
  const ind = document.getElementById(`${id}-running`);
  if (ind) ind.innerHTML = st.focus
    ? `${DEVICE_CAPS[st.focus].icon()}<span style="font-size:9px;font-weight:600;letter-spacing:0.04em">${DEVICE_CAPS[st.focus].label}</span>`
    : '';

  _renderActions(id);
}

function _renderActions(id) {
  const bar = document.getElementById(`${id}-actions`);
  if (!bar) return;
  const st  = _deviceState[id] || {};
  const def = st.focus && DEVICE_CAPS[st.focus];
  if (!def || !def.actions.length) { bar.innerHTML = ''; return; }
  bar.innerHTML = def.actions.map((a, i) => {
    const act = st.active?.[`${st.focus}:${a.title}`];
    return `<button class="icon-btn${a.primary && !act ? ' primary' : ''}${act ? ' active' : ''}"
      ${_deviceCanAct(st, st.focus, a) ? '' : 'disabled'} onclick="deviceCapAction('${id}','${st.focus}',${i})"
      title="${a.title}">${a.icon}</button>`;
  }).join('');
}

function _deviceCanAct(st, cap, action) {
  if (!st?.ready || !action || (action.local && !st.local)) return false;
  if (cap === 'network') return !!st.info.ip;
  return !['off', 'na'].includes(DEVICE_CAPS[cap].badge(st.info, st).state);
}

async function deviceCapAction(id, cap, i) {
  const st = _deviceState[id];
  const a  = DEVICE_CAPS[cap]?.actions?.[i];
  if (!_deviceCanAct(st, cap, a)) return;
  if (a.local) {
    await a.local(id, st);
  } else if (typeof _runStart === 'function') {
    _runStart(`device-${cap}-${a.title.toLowerCase().replace(/\s+/g, '-')}`, a.op(st.info));
  }
  _renderActions(id);
  _renderRunning(id);
}

// Caps with an active op toggle keep an amber edge on their row. The focused
// cap (gray-out + upper-right indicator) is owned by deviceCapFocus.
function _renderRunning(id) {
  const st = _deviceState[id] || {};
  const running = Object.keys(DEVICE_CAPS).filter(cap =>
    Object.entries(st.active || {}).some(([k, v]) => v && k.startsWith(`${cap}:`)));
  Object.keys(DEVICE_CAPS).forEach(cap =>
    document.getElementById(`${id}-cap-${cap}`)?.classList.toggle('running', running.includes(cap)));
}

function _startSyncTicker(id) {
  const st = _deviceState[id];
  clearInterval(st.tick);
  const paint = () => {
    const el = document.getElementById(`${id}-readout`);
    if (!el) { clearInterval(st.tick); return; }
    if (!st.live) {
      el.textContent = st.discovered ? `saved ${new Date(st.discovered).toLocaleString()}` : 'saved config';
      return;
    }
    const s = Math.round((Date.now() - st.discovered) / 1000);
    el.textContent = s < 5 ? 'synced just now' : `synced ${s < 60 ? s + 's' : Math.floor(s / 60) + 'm'} ago`;
  };
  paint();
  if (st.live) st.tick = setInterval(paint, 5000);
}

// Local camera preview into the tile's cam-view. getUserMedia here is the
// preview itself; snapshot/motion reuse camera.js push plumbing.
async function _devicePreviewToggle(id) {
  const video = document.getElementById(`${id}-preview`);
  if (!video) return false;
  if (video.srcObject) { _stopDevicePreview(id); return false; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    video.srcObject = stream;
    video.style.display = 'block';
    const empty = document.getElementById(`${id}-preview-empty`);
    if (empty) empty.style.display = 'none';
    return true;
  } catch (e) {
    console.warn('device: preview failed', e);
    return false;
  }
}


// ══════════════════════════════════════════════════════════════════════════
// SERVER SYNC
// ══════════════════════════════════════════════════════════════════════════

const _deviceDiscoveredCache = {};

async function _postDeviceInfo(id, info) {
  try {
    // use existing endpoints for camera + audio
    if (typeof _postUserDevices === 'function') {
      await _postUserDevices();  // existing web.js hook
    }

    // persist the live capability readings (volatile, keyed by identifier)
    fetch('/api/devices/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ capabilities: info.caps }),
    }).catch(() => {});

    // also emit a device-level summary — only on real change
    const payload = {
      tileId:   id,
      ip:       info.ip,
      type:     info.deviceType,
      cameras:  info.cameras.length,
      mics:     info.mics.length,
      speakers: info.speakers.length,
      network:  info.network?.type,
      battery:  info.battery ? `${Math.round(info.battery.level * 100)}%${info.battery.charging ? '+' : ''}` : 'n/a',
      sensors:  info.sensors?.motion ? 'on' : 'off',
      geo:      info.geo?.available ? 'ready' : 'off',
    };
    const prev = _deviceDiscoveredCache[id];
    const next = JSON.stringify(payload);
    if (prev === next) return;
    _deviceDiscoveredCache[id] = next;
    _emitEvent('device.discovered', payload);
  } catch (e) {
    console.warn('device: _postDeviceInfo failed', e);
  }
}


// ══════════════════════════════════════════════════════════════════════════
// EVENT HANDLERS
// ══════════════════════════════════════════════════════════════════════════

function _onDeviceDiscovered(id, evt) {
  if (evt.tileId === id && _deviceState[id]?.live) setDot(id, 'green');
}

// On load: paint badges from the persisted stores — names from devices.json,
// values from capabilities.json — without re-probing the browser.
async function _deviceRenderPersisted(id, refresh=false) {
  const name = _tileDev(id);
  try {
    const {devices:devs, capabilities:caps} = await _deviceLoadConfig(refresh);
    if (_tileDev(id) !== name || !document.getElementById(`${id}-body`)) return;
    const row = (devs.devices || []).find(d => d.name === name) || {};
    const identifier = row.identifier || '';
    const ip = String(identifier).replace(/^push:\/\//, '');
    const status = caps?.[identifier] || {};
    const media = key => (devs[key] || []).filter(d => ip &&
      (d.identifier === identifier || d.source === ip));
    return _deviceRender(id, {...row, ...status, identifier, ip,
      cameras:media('cameras'), mics:media('mic'), speakers:media('speaker')});
  } catch (e) {
    setDot(id, 'red');
    console.warn('device: config failed', e);
  }
}

function _onCapabilityUpdate(id, evt) {
  if ((evt.tileId === id || (evt.ip && evt.ip === _deviceState[id]?.info?.ip)) && evt.cap && evt.state) {
    _updateCapBadge(id, evt.cap, evt.state);
  }
}


// ══════════════════════════════════════════════════════════════════════════
// CAPABILITY ROW HELPERS
// ══════════════════════════════════════════════════════════════════════════

function _capRow(id, cap, label, iconSvg) {
  return `
  <div class="device-cap-row" id="${id}-cap-${cap}" onclick="deviceCapFocus('${id}','${cap}')">
    <span class="device-cap-icon">${iconSvg}</span>
    <span class="device-cap-label">${label}</span>
    <span class="device-cap-badge" id="${id}-badge-${cap}">…</span>
  </div>`;
}

function _updateCapBadge(id, cap, state, text) {
  const badge = document.getElementById(`${id}-badge-${cap}`);
  if (!badge) return;

  const colors = {
    on:    { bg: 'rgba(52,211,153,0.15)', color: '#34d399', text: 'ON'    },
    off:   { bg: 'rgba(239,68,68,0.12)',   color: '#ef4444', text: 'OFF'   },
    ready: { bg: 'rgba(251,191,36,0.12)',  color: '#fbbf24', text: 'READY' },
    na:    { bg: 'var(--bg3,rgba(255,255,255,0.05))', color: 'var(--text3,#666)', text: 'N/A' },
  };
  const c = colors[state] || colors.na;
  badge.style.background = c.bg;
  badge.style.color      = c.color;
  badge.textContent      = text || c.text;

  const icon = document.querySelector(`#${id}-cap-${cap} .device-cap-icon`);
  if (icon) { icon.style.color = c.color; icon.style.opacity = '1'; }
}


// ══════════════════════════════════════════════════════════════════════════
// CAMERA PREVIEW (small tile in meta row)
// ══════════════════════════════════════════════════════════════════════════

function _stopDevicePreview(id) {
  const video = document.getElementById(`${id}-preview`);
  const empty = document.getElementById(`${id}-preview-empty`);
  const ts    = document.getElementById(`${id}-preview-ts`);
  if (video?.srcObject) {
    video.srcObject.getTracks().forEach(t => t.stop());
    video.srcObject = null;
  }
  if (video) {
    video.style.display = 'none';
  }
  if (empty) {
    empty.style.display = '';
  }
  if (ts) {
    ts.textContent = '';
  }
}


// ══════════════════════════════════════════════════════════════════════════
// DEVICE-TYPE ICONS
// ══════════════════════════════════════════════════════════════════════════

function _deviceTypeIcon(type) {
  switch (type) {
    case 'mobile':
      return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`;
    case 'tablet':
      return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`;
    default: // desktop
      return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`;
  }
}


// ══════════════════════════════════════════════════════════════════════════
// CAPABILITY SVG ICONS (12×12)
// ══════════════════════════════════════════════════════════════════════════

function _svgCamera() {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>`;
}

function _svgMic() {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>`;
}

function _svgSpeaker() {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`;
}

function _svgNetwork() {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1" fill="currentColor"/></svg>`;
}

function _svgGeo() {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`;
}

function _svgBattery() {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="6" width="18" height="12" rx="2" ry="2"/><line x1="23" y1="10" x2="23" y2="14"/></svg>`;
}

function _svgSensors() {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>`;
}

function _svgNotify() {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>`;
}


// ══════════════════════════════════════════════════════════════════════════
// CSS — spin animation for refresh button
// ══════════════════════════════════════════════════════════════════════════

(function _injectDeviceCSS() {
  if (document.getElementById('device-tile-css')) return;
  const style = document.createElement('style');
  style.id = 'device-tile-css';
  style.textContent = `
    @keyframes device-spin {
      from { transform: rotate(0deg); }
      to   { transform: rotate(360deg); }
    }
    .spin svg {
      animation: device-spin 0.6s linear infinite;
    }
    .device-cap-row {
      user-select: none;
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 4px 5px;
      border-radius: 4px;
      transition: background 0.2s;
      min-width: 0;
      cursor: pointer;
    }
    .device-cap-row:hover {
      background: var(--hover, rgba(255, 255, 255, 0.03));
    }
    .device-cap-row.focused {
      background: var(--hover, rgba(255, 255, 255, 0.05));
    }
    .device-cap-row.running {
      box-shadow: inset 2px 0 0 #fbbf24;
      background: rgba(251, 191, 36, 0.05);
    }
    .device-running {
      display: flex;
      align-items: center;
      gap: 3px;
      flex-shrink: 0;
      margin-right: auto;
      color: #fbbf24;
    }
    .device-running-ico {
      display: flex;
      cursor: pointer;
    }
    .device-cap-grid.stale {
      opacity: 0.55;
    }
    .device-cap-icon {
      opacity: 0.4;
      flex-shrink: 0;
      width: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .device-cap-label {
      font-size: 8px;
      font-weight: 600;
      letter-spacing: 0.04em;
      color: var(--text3, #888);
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .device-cap-badge {
      flex-shrink: 0;
      font-size: 7px;
      padding: 1px 4px;
      border-radius: 2px;
      background: var(--bg3, rgba(255, 255, 255, 0.05));
      color: var(--text3, #666);
    }
    .device-meta {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 2px 2px 8px;
      flex-shrink: 0;
      border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.06));
    }
    .device-preview-cam {
      flex-shrink: 0;
      width: 88px;
      height: 66px;
      min-height: 66px;
      cursor: default;
    }
    .device-preview-cam .device-preview-stream {
      display: none;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .device-meta-main {
      flex: 1;
      min-width: 0;
      min-height: 66px;
      display: flex;
      flex-direction: column;
      align-items: stretch;
      gap: 2px;
      padding-top: 2px;
    }
    .device-id-row {
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      height: 16px;
    }
    .device-action-row {
      display: flex;
      align-items: center;
      gap: 4px;
      height: 22px;
      min-width: 0;
    }
    .device-actions {
      display: flex;
      align-items: center;
      gap: 2px;
      flex-shrink: 0;
    }
    .device-actions .icon-btn.primary {
      color: #34d399;
    }
    .device-actions .icon-btn.active {
      color: #fbbf24;
    }
    .device-actions .icon-btn[disabled] {
      opacity: 0.4;
      pointer-events: none;
    }
    .device-readout {
      margin-top: auto;
      font-size: 9px;
      color: var(--text3, #666);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-align: right;
    }
    .device-cap-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 2px 10px;
      margin-top: auto;
      flex-shrink: 0;
    }
  `;
  document.head.appendChild(style);
})();
