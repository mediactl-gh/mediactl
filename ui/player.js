// web/plex.js

// ══════════════════════════════════════════════════════════════════════════
// TV PLAYER  (stacked 1×1 tile)
// ══════════════════════════════════════════════════════════════════════════

const _tvOn  = {};
const _tvApps = {};
const _tvTvsApps = {};  // tile id → apps array from tvs.json
const _tvActiveApp = {};  // tile id → current app id
const _tvPlayer = {};
const _tvPlayerClient = {};  // tile id → Plex client title/id
const _tvResolvedDev = {};

// icon map: package id → icon url (known apps only)
const _tvAppIcons = {
  'com.plexapp.android':                    'https://cdn.simpleicons.org/plex/e5a00d',
  'com.plexapp.plex':                       'https://cdn.simpleicons.org/plex/e5a00d',
  'org.jellyfin.androidtv':                 'https://cdn.simpleicons.org/jellyfin/00a4dc',
  'org.jellyfin.webos':                     'https://cdn.simpleicons.org/jellyfin/00a4dc',
  'org.jellyfin.tizen':                     'https://cdn.simpleicons.org/jellyfin/00a4dc',
  'com.google.android.youtube.tv':          'https://www.youtube.com/favicon.ico',
  'com.google.ios.youtube':                 'https://www.youtube.com/favicon.ico',
  'com.netflix.ninja':                      'https://www.netflix.com/favicon.ico',
  'com.netflix.Netflix':                    'https://www.netflix.com/favicon.ico',
  'com.disney.disneyplus':                  'https://www.disneyplus.com/favicon.ico',
  'com.amazon.amazonvideo.livingroom':      'https://www.primevideo.com/favicon.ico',
  'com.amazon.amazonvideo':                 'https://www.primevideo.com/favicon.ico',
  'com.apple.atve.androidtv.appletv':       'https://www.apple.com/favicon.ico',
  'com.apple.tv':                           'https://www.apple.com/favicon.ico',
  'com.wbd.stream':                         'https://www.max.com/favicon.ico',
  'com.hbo.max':                            'https://www.max.com/favicon.ico',
  'com.spotify.tv.android':                 'https://www.spotify.com/favicon.ico',
  'tv.twitch.android.app':                  'https://www.twitch.tv/favicon.ico',
};

function _tvDev(id) {
  const cfg = dashboard?.tiles?.find(t => t.id === id)?.config || {};
  return (Array.isArray(cfg.devices) ? cfg.devices[0] : cfg.devices) || cfg.device || '';
}
function _tvArgs(id, ...base) {
  const dev = _tvDev(id);
  return dev ? [...base, '--tv', dev] : base;
}
function tvKey(id, k) { runOp(_tvArgs(id, 'tv', 'key', k)); }

const _TV_BRANDS = ['android', 'androidtv', 'google', 'lg', 'webos', 'samsung', 'tizen', 'roku'];

// brand map cache: {name: {brand, host, mac, apps, …}} from /api/config?target=tvs
let _tvBrandMap = {};
let _tvBrandMapTs = 0;
async function _tvEnsureBrands() {
  if (_tvBrandMapTs && Date.now() - _tvBrandMapTs < 5000) return _tvBrandMap;
  try { _tvBrandMap = await (await fetch('/api/config?target=tvs')).json(); _tvBrandMapTs = Date.now(); }
  catch (_) { _tvBrandMap = {}; }
  return _tvBrandMap;
}
// First bound device with a smart brand, or the TV resolved by the panel.
function _tvSmartMember(id) {
  const cfg = dashboard?.tiles?.find(t => t.id === id)?.config || {};
  const devs = Array.isArray(cfg.devices) ? cfg.devices : [cfg.devices];
  return [...devs, cfg.device, _tvResolvedDev[id]]
    .find(d => _TV_BRANDS.includes(String(_tvBrandMap[d]?.brand || '').toLowerCase()))
    || _tvResolvedDev[id] || null;
}

function tvOpenPicker(id) {
  const tile = dashboard?.tiles?.find(t => t.id === id);
  if (!tile) return;
  if (!tile.config) tile.config = {};
  openDevicePicker(id, 'tv', {
    syncLabel: false,
    schema: [{ key: 'brand', values: _TV_BRANDS, default: 'android' }],
    onUpsert: name => _tvPairGuide(id, name),
  });
}

const _tvWheelSvg = `<svg viewBox="40 40 150 150" width="68" height="68" class="tv-wheel" style="display:block">
  <circle cx="115" cy="115" r="72" fill="var(--bg)" stroke="var(--border2)" stroke-width="1"/>
  <path class="tv-seg" d="M 68.72 59.84 A 72 72 0 0 1 161.28 59.84 L 133.00 93.55 A 28 28 0 0 0 97.00 93.55 Z" onclick="tvKey('__ID__','up')"/><polygon class="tv-glyph" points="115.0,59.0 109.5,68.5 120.5,68.5"/>
  <path class="tv-seg" d="M 170.16 68.72 A 72 72 0 0 1 170.16 161.28 L 136.45 133.00 A 28 28 0 0 0 136.45 97.00 Z" onclick="tvKey('__ID__','right')"/><polygon class="tv-glyph" points="171.0,115.0 161.5,109.5 161.5,120.5"/>
  <path class="tv-seg" d="M 161.28 170.16 A 72 72 0 0 1 68.72 170.16 L 97.00 136.45 A 28 28 0 0 0 133.00 136.45 Z" onclick="tvKey('__ID__','down')"/><polygon class="tv-glyph" points="115.0,171.0 109.5,161.5 120.5,161.5"/>
  <path class="tv-seg" d="M 59.84 161.28 A 72 72 0 0 1 59.84 68.72 L 93.55 97.00 A 28 28 0 0 0 93.55 133.00 Z" onclick="tvKey('__ID__','left')"/><polygon class="tv-glyph" points="59.0,115.0 68.5,109.5 68.5,120.5"/>
  <circle cx="115" cy="115" r="27" fill="var(--bg)" stroke="var(--border)" stroke-width="1.5"/>
  <circle class="tv-ok" cx="115" cy="115" r="24" onclick="tvKey('__ID__','ok')"/>
  <text class="tv-glyph" x="115" y="119" text-anchor="middle" font-size="10" font-weight="600" font-family="var(--font)" letter-spacing="1">OK</text>
</svg>`;

const _tvMuteIcon = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>`;

const _tvIco = {
  power:   `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>`,
  volUp:   `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="6" x2="12" y2="18"/><line x1="6" y1="12" x2="18" y2="12"/></svg>`,
  volDown: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="12" x2="18" y2="12"/></svg>`,
  home:    `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>`,
  back:    `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="14 6 8 12 14 18"/></svg>`,
  menu:    `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/></svg>`,
  chev:    `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`,
};

const _tvVolBtns  = [['vol_up', _tvIco.volUp, 'Volume up'], ['mute', _tvMuteIcon, 'Mute'], ['vol_down', _tvIco.volDown, 'Volume down']];
const _tvUtilBtns = [['home', _tvIco.home, 'Home'], ['back', _tvIco.back, 'Back'], ['menu', _tvIco.menu, 'Menu']];

function _tvBtnCol(id, cls, btns) {
  return `<div class="tv-ctl-group ${cls}">` + btns.map(([k, ico, title]) =>
    `<button class="icon-btn" onclick="tvKey('${id}','${k}')" title="${title}">${ico}</button>`).join('') + `</div>`;
}

function _tvAppIcon(appId) {
  return _tvAppIcons[appId] || '';
}

const _tvSidebarDropOpen = {};
function tvSidebarToggle(id) {
  _tvSidebarDropOpen[id] = !_tvSidebarDropOpen[id];
  _tvRenderSidebar(id);
}

function _tvCloseSidebarOnOutside(evt) {
  for (const id of Object.keys(_tvSidebarDropOpen)) {
    if (!_tvSidebarDropOpen[id]) continue;
    const root = document.getElementById(`${id}-sidebar`);
    if (root && root.contains(evt.target)) continue;
    _tvSidebarDropOpen[id] = false;
    _tvRenderSidebar(id);
  }
}
document.addEventListener('mousedown', _tvCloseSidebarOnOutside, true);
document.addEventListener('focusin', _tvCloseSidebarOnOutside, true);

function _playerSidebar(id, apps, activeAppId) {
  const allApps = apps || [];
  const list = allApps.slice(0, 7);
  let btns = list.map(a => {
    const icon = _tvAppIcon(a.id);
    const isActive = a.id === activeAppId;
    const imgHtml = icon
      ? `<img src="${icon}" onerror="this.style.opacity='.3'"/>`
      : `<span style="font-size:10px;font-weight:700;color:var(--text2)">${(a.title||'?')[0]}</span>`;
    return `<button class="icon-btn tv-app-btn${isActive ? ' active' : ''}" data-appid="${a.id}" onclick="tvLaunchApp('${id}','${a.id}','${a.title}')" title="${a.title}">
       ${imgHtml}
     </button>`;
  }).join('');
  if (allApps.length > 7) {
    btns += '<button class="icon-btn tv-app-more" onclick="tvSidebarToggle(this.closest(&quot;.tile&quot;)?.dataset.id)" title="Show all apps">…</button>';
  }
  if (_tvSidebarDropOpen[id]) {
    const more = allApps.slice(7);
    btns += '<div class="tv-app-dropdown">' + more.map(a => {
      const icon = _tvAppIcon(a.id);
      const img = icon
        ? '<img src="' + icon + '" onerror="this.style.opacity=0.3"/>'
        : '<span class="tv-app-dropdown-fallback">' + (a.title || '?')[0] + '</span>';
      return "<button onclick=\"tvLaunchApp('" + id + "','" + a.id + "','" + a.title + "');tvSidebarToggle('" + id + "')\" title=\"" + a.title + "\">" +
        img + '<span>' + a.title + '</span></button>';
    }).join('') + '</div>';
  }
  return `<div class="player-sidebar">
    ${btns}
  </div>`;
}

function _playerTvArea(id) {
  return `<div class="player-tv-area">
    ${_tvBtnCol(id, 'tv-vol', _tvVolBtns)}
    ${_tvWheelSvg.replaceAll('__ID__', id)}
    ${_tvBtnCol(id, 'tv-util', _tvUtilBtns)}
  </div>`;
}

// ══════════════════════════════════════════════════════════════════════════
// PANEL STATE MACHINE
// ══════════════════════════════════════════════════════════════════════════

async function _tvPanelState(id) {
  const tile = dashboard?.tiles?.find(t => t.id === id);
  const cfg = tile?.config || {};
  let dev = (Array.isArray(cfg.devices) ? cfg.devices[0] : cfg.devices) || cfg.device || '';

  // Old tiles copied the selected device into the title and auto-selected the first TV.
  // Treat that coupled state as the new unscoped default.
  if (dev && cfg.label === dev) {
    cfg.devices = [];
    delete cfg.device;
    cfg.label = '';
    dev = '';
    if (typeof saveDashboard === 'function') saveDashboard();
  }

  // fetch devices.json tvs
  const allDevs = await _fetchDevices();
  const tvsList = allDevs.tvs || [];
  if (!tvsList.length) { delete _tvResolvedDev[id]; return { state: 'none', dev: null }; }

  // An unscoped tile observes all players; use the first TV only to render controls.
  if (!dev) dev = tvsList[0]?.name || '';
  if (!dev) return { state: 'none', dev: null };
  _tvResolvedDev[id] = dev;

  // check tvs.json for paired status
  let tvsMeta = {};
  try { tvsMeta = await (await fetch('/api/config?target=tvs')).json(); } catch (_) {}
  _tvBrandMap = tvsMeta; _tvBrandMapTs = Date.now();
  dev = _tvSmartMember(id) || dev;
  _tvResolvedDev[id] = dev;
  const row = tvsList.find(tv => tv.name === dev) || {};
  const meta = tvsMeta[dev] || {};
  if (!(meta.host || row.identifier) || !(meta.brand || row.source || row.brand))
    return { state: 'setup', dev };
  if (meta.paired === true) return { state: 'ready', dev };
  return { state: 'unpaired', dev };
}

function _tvRenderUpper(id, html) {
  const el = document.getElementById(`${id}-player-upper`);
  if (el) el.innerHTML = html;
}

function _tvRenderLower(id, show) {
  const el = document.getElementById(`${id}-player-lower`);
  if (el) el.style.display = show ? '' : 'none';
}

function _tvPanelNone(id) {
  _tvRenderUpper(id, `<div class="muted">No TV discovered — <a onclick="tvOpenPicker('${id}')" style="color:var(--accent);cursor:pointer">set up</a></div>`);
  _tvRenderLower(id, false);
  setDot(id, 'gray');
}

function _tvPanelUnpaired(id, dev) {
  _tvRenderUpper(id, `<div class="muted">${_dpEscLine(dev)} not paired — <a onclick="_tvPairGuide('${id}','${_dpEscLine(dev)}')" style="color:var(--accent);cursor:pointer">set up</a></div>`);
  _tvRenderLower(id, true);
  setDot(id, 'amber');
}

function _tvPanelReady(id, dev) {
  _tvTvsApps[id] = (_tvBrandMap[dev]?.apps) || [];
  _tvRenderSidebar(id);
  _tvRenderLower(id, true);
  if (['google', 'android', 'androidtv'].includes(_tvBrandMap[dev]?.brand)) runOp(_tvArgs(id, 'tv', 'connect'));

  _tvRenderUpper(id, `<div class="muted">checking…</div>`);
}

function _tvPanelSetup(id, dev) {
  _tvRenderUpper(id, `<div class="muted">${_dpEscLine(dev)} not set up — <a onclick="tvOpenPicker('${id}')" style="color:var(--accent);cursor:pointer">set up</a></div>`);
  _tvRenderLower(id, false);
  setDot(id, 'amber');
}

// ══════════════════════════════════════════════════════════════════════════
// TILE DEFINITION
// ══════════════════════════════════════════════════════════════════════════

TILES['tv-player'] = {
  html(tile) {
    const id = tile.id;
    const cfg = tile.config || {};
    const selected = (Array.isArray(cfg.devices) ? cfg.devices[0] : cfg.devices) || cfg.device || '';
    const tvLabel = cfg.label && cfg.label !== selected ? cfg.label : '—';
    return `
    ${_tileHeader(id,
      `<span onclick="tvOpenPicker('${id}')" title="Select TV / player" style="cursor:pointer">PLAYER</span><span id="${id}-label" onclick="renameTileLabel('${id}')" title="Rename tile"
        style="cursor:pointer;font-size:10px;color:var(--text2)">${tvLabel}</span>`,
      `<button class="icon-btn" onclick="playerSettings('${id}')" title="Server settings">⚙</button>`
    )}
    <div id="${id}-body" class="player-stacked-body">
      <div class="player-main">
        <div id="${id}-player-upper" class="player-upper">
          <div class="muted">loading…</div>
        </div>
        <div id="${id}-player-lower" class="player-lower" style="display:none">
          <div class="player-divider"></div>
          ${_playerTvArea(id)}
        </div>
      </div>
      <div class="player-app-panel">
        <button class="icon-btn tv-pw-btn" id="${id}-power" onclick="_tvPowerToggle('${id}')" title="Power">${_tvIco.power}</button>
        <div id="${id}-sidebar" class="player-app-list">${_playerSidebar(id, [], null)}</div>
      </div>
    </div>`;
  },

  async bind(id) {
    const ps = await _tvPanelState(id);
    if (ps.state === 'none') { _tvPanelNone(id); return; }
    if (ps.state === 'setup') { _tvPanelSetup(id, ps.dev); return; }
    if (ps.state === 'unpaired') { _tvPanelUnpaired(id, ps.dev); return; }
    _tvPanelReady(id, ps.dev);
    await playerHydrate(id);
  },

  events(id) {
    onEvent('player.status', evt => {
      if (document.getElementById(`${id}-body`)) playerHydrate(id);
    });
    onEvent('player.linked', evt => {
      if (!document.getElementById(`${id}-body`)) return;
      if (evt.backend === 'jellyfin' && _jellyConnectId === id) {
        _jellyConnectId = null;
        _playerCloseSettings();
      }
      playerRefresh(id);
    });
    onEvent('player.pair', evt => {
      if (evt.backend === 'jellyfin') { _jellyfinConnectEvent(id, evt); return; }
      if (_plexConnHandler) { _plexConnHandler(evt); return; }
      const upper = document.getElementById(`${id}-player-upper`);
      if (!upper) return;
      if (evt.linked === 'False') {
        setDot(id, 'red');
        upper.innerHTML = `<div class="muted">link failed — ${evt.reason || 'error'}</div>`;
      } else if (evt.authUrl) {
        setDot(id, 'amber');
        upper.innerHTML = `<div class="muted">signing in to Plex…</div>`;
        if (_plexAuthWin) _plexAuthWin.location = evt.authUrl;
        else window.open(evt.authUrl, '_blank');
      }
    });
    onEvent('tv.app', evt => {
      if (!document.getElementById(`${id}-body`)) return;
      if (!_tvApps[id]) _tvApps[id] = [];
      if (evt.id && !_tvApps[id].some(a => a.appId === evt.id)) {
        _tvApps[id].push({ appId: evt.id, title: evt.title || evt.id });
      }
      if (!_tvTvsApps[id]) _tvTvsApps[id] = [];
      if (evt.id && !_tvTvsApps[id].some(a => a.id === evt.id)) {
        _tvTvsApps[id].push({ id: evt.id, title: evt.title || evt.id });
        _tvRenderSidebar(id);
      }
      const list = document.getElementById(`${id}-applist`);
      if (list && list.style.display !== 'none') _tvRenderApps(id, list, _tvApps[id]);
    });
    onEvent('tv.apps', evt => {
      if (!document.getElementById(`${id}-body`)) return;
      tvRefresh(id);
    });
    onEvent('tv.pair', evt => {
      if (!document.getElementById(`${id}-body`)) return;
      if (evt.status !== 'paired') return;
      const dev = _tvDev(id);
      if (dev && _tvBrandMap[dev]) _tvBrandMap[dev].paired = true;
      _tvPanelReady(id, dev);
      playerRefresh(id);
    });
    onEvent('network.discover', evt => {
      if (!document.getElementById(`${id}-body`)) return;
      const curDev = _tvDev(id);
      if (curDev) return;
      (async () => {
        const ps = await _tvPanelState(id);
        if (ps.state === 'none') return;
        if (ps.state === 'setup') { _tvPanelSetup(id, ps.dev); return; }
        if (ps.state === 'unpaired') { _tvPanelUnpaired(id, ps.dev); return; }
        _tvPanelReady(id, ps.dev);
        playerHydrate(id);
      })();
    });
  }
};

// ══════════════════════════════════════════════════════════════════════════
// PLEX LOGIC
// ══════════════════════════════════════════════════════════════════════════

let _plexAuthWin = null;
let _playerCfg = null;

function _isPlexApp(appId) {
  return appId === 'cdp-30' || appId === 'com.plexapp.android' || appId === 'com.plexapp.plex';
}

function _mediaBackendForApp(appId) {
  if (_isPlexApp(appId)) return 'plex';
  if (appId === 'org.jellyfin.androidtv' || appId === 'org.jellyfin.webos' || appId === 'org.jellyfin.tizen') return 'jellyfin';
  return '';
}

function _mediaConfigured(backend) {
  const cfg = _playerCfg?.[backend] || {};
  return backend === 'plex'
    ? !!(cfg.url && cfg.token)
    : !!(cfg.url && cfg.token && cfg.user_id);
}

function _mediaServerCfg(services) {
  const cfg = {};
  const servers = Object.entries(services?.['media-center'] || {})
    .filter(([, info]) => (info.capabilities || []).includes('server'));
  ['plex', 'jellyfin'].forEach(backend => {
    cfg[backend] = servers
      .find(([key, server]) => server.type === backend || key === backend || key.startsWith(`${backend}-`))?.[1]
      || {};
  });
  return cfg;
}

function _mediaSetupPrompt(id, backend) {
  if (_mediaBackendForApp(_tvActiveApp[id]) !== backend) return;
  const status = backend === 'jellyfin' ? 'Jellyfin unavailable' : 'Plex unavailable';
  setDot(id, 'amber');
  _tvRenderUpper(id, `<div class="muted">${status} —
    <a onclick="playerSettings('${id}','${backend}')" style="color:var(--accent);cursor:pointer">set up</a></div>`);
}

function _plexConnectPrompt(id) {
  const activeApp = _tvActiveApp[id];
  if (!_isPlexApp(activeApp)) return;
  _tvRenderUpper(id, `<div class="muted">plex not connected —
    <a onclick="playerSettings('${id}')" style="color:var(--accent);cursor:pointer">set up</a></div>`);
}

async function plexConnect(id) {
  const upper = document.getElementById(`${id}-player-upper`);
  setDot(id, 'amber');
  if (upper) upper.innerHTML = `<div class="muted" id="${id}-plex-conn">connecting…</div>`;
  _plexAuthWin = window.open('', '_blank');

  let settled = false;
  const timeout = setTimeout(() => {
    if (settled) return; settled = true;
    if (_plexAuthWin) { _plexAuthWin.close(); _plexAuthWin = null; }
    if (upper) upper.innerHTML = `<div class="muted">connection timed out — <a onclick="plexConnect('${id}')" style="color:var(--accent);cursor:pointer">retry</a></div>`;
    setDot(id, 'red');
  }, 30000);

  const origHandler = _plexConnHandler;
  _plexConnHandler = evt => {
    if (settled) return;
    if (evt.linked === 'False') {
      settled = true; clearTimeout(timeout);
      if (_plexAuthWin) { _plexAuthWin.close(); _plexAuthWin = null; }
      setDot(id, 'red');
      if (upper) upper.innerHTML = `<div class="muted">link failed — ${evt.reason || 'error'}</div>`;
    } else if (evt.authUrl) {
      settled = true; clearTimeout(timeout);
      if (_plexAuthWin) _plexAuthWin.location = evt.authUrl;
      else window.open(evt.authUrl, '_blank');
    }
    origHandler?.(evt);
  };

  const r = await startProc('player-pair', ['player', 'pair', '--player', 'plex'], { mirror: false });
  if (!r || !r.ok) {
    settled = true; clearTimeout(timeout);
    if (_plexAuthWin) { _plexAuthWin.close(); _plexAuthWin = null; }
    if (upper) upper.innerHTML = `<div class="muted">could not start — <a onclick="plexConnect('${id}')" style="color:var(--accent);cursor:pointer">retry</a></div>`;
    setDot(id, 'red');
  }
}

let _plexConnHandler = null;

let _playerCfgPop = null;
let _jellyConnectId = null;

async function playerSettings(id, preferredBackend = '') {
  if (_playerCfgPop) { _playerCloseSettings(); return; }
  if (typeof _mediaCloseConfig === 'function') _mediaCloseConfig();
  const tileEl = tileRefs[id] || document.querySelector(`.tile[data-id="${id}"]`);
  if (!tileEl) return;
  let cfg = {}, services = {};
  try {
    services = await fetch('/api/config?target=services').then(r => r.json());
    cfg = _mediaServerCfg(services);
    _playerCfg = cfg;
  } catch (_) {}
  if (preferredBackend) cfg = { ...cfg, backend: preferredBackend };
  const serverEntries = Object.entries(services?.['media-center'] || {})
    .filter(([, info]) => (info.capabilities || []).includes('server'));
  const jellyfinServers = serverEntries
    .filter(([key, server]) => key === 'jellyfin' || key.startsWith('jellyfin@') || server.type === 'jellyfin')
    .map(([, server]) => server).filter(server => server.url);

  const pop = document.createElement('div');
  pop.id = 'dp-pop';
  const scroll = document.createElement('div');
  scroll.className = 'dp-scroll';
  scroll.innerHTML = _playerSettingsHTML(id, cfg, jellyfinServers);
  pop.appendChild(scroll);

  const rect = tileEl.getBoundingClientRect();
  let top = rect.top + 44, left = rect.left;
  const popW = Math.max(220, Math.min(rect.width, 320));
  if (top + 240 > window.innerHeight - 8) top = rect.top - 4;
  if (top < 4) top = 4;
  if (left + popW > window.innerWidth - 4) left = window.innerWidth - popW - 4;
  if (left < 4) left = 4;
  pop.style.cssText = `position:fixed;z-index:9000;top:${top}px;left:${left}px;width:${popW}px;
    max-height:280px;display:flex;flex-direction:column;background:var(--surface2);
    border:1px solid var(--border2);border-radius:10px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.4)`;
  document.body.appendChild(pop);
  _playerCfgPop = pop;
  setTimeout(() => document.addEventListener('mousedown', _playerCfgOutside, true), 10);
  document.addEventListener('keydown', _playerCfgEsc);
  _playerTypeToggle();
}

function _playerCloseSettings() {
  if (_jellyConnectId) { stopProc('player-pair'); _jellyConnectId = null; }
  if (_playerCfgPop) { _playerCfgPop.remove(); _playerCfgPop = null; }
  document.removeEventListener('mousedown', _playerCfgOutside, true);
  document.removeEventListener('keydown', _playerCfgEsc);
}
function _playerCfgOutside(e) {
  if (!_playerCfgPop) return;
  if (e.target.closest?.('[title="Server settings"]')) return;
  if (!_playerCfgPop.contains(e.target)) _playerCloseSettings();
}
function _playerCfgEsc(e) { if (e.key === 'Escape') _playerCloseSettings(); }

const _PS_META = `padding:8px 10px 3px;text-transform:uppercase;letter-spacing:.08em;
  font-size:9px;font-weight:700;color:var(--accent)`;
const _PS_INPUT = `width:100%;font-family:var(--mono);font-size:10px;background:var(--surface);
  border:1px solid var(--border2);color:var(--text);border-radius:4px;padding:3px 5px;outline:none;box-sizing:border-box`;

function _playerSettingsHTML(id, cfg, jellyfinServers = []) {
  const backend = cfg.backend || 'plex';
  const p = cfg.plex || {}, j = cfg.jellyfin || {};
  const pConn = !!(p.url && p.token);
  const jConn = !!(j.url && j.token && j.user_id);
  const servers = [...jellyfinServers];
  if (j.url && !servers.some(server => server.url === j.url)) servers.unshift({ url: j.url, name: 'configured' });
  const jurl = j.url || servers[0]?.url || 'http://127.0.0.1:8096';
  const serverField = servers.length > 1
    ? `<select id="ps-url" style="${_PS_INPUT};flex:1;min-width:0;width:auto;margin-left:8px">
        ${servers.map(server => `<option value="${server.url}"${server.url === jurl ? ' selected' : ''}>${server.name ? server.name + ' · ' : ''}${server.url}</option>`).join('')}
       </select>`
    : `<input id="ps-url" style="${_PS_INPUT};flex:1;min-width:0;width:auto;margin-left:8px" placeholder="http://host:8096" value="${jurl}" autocomplete="off" spellcheck="false">`;
  return `
    <div class="dp-meta" style="${_PS_META};padding-top:4px">media center</div>
    <div class="dp-row"><div class="dp-row-pick" style="display:flex;align-items:center" onclick="event.stopPropagation()">
      <span class="dp-row-dim">type</span>
      <select id="ps-type" onchange="_playerTypeChange('${id}')"
        style="margin-left:auto;font-family:var(--mono);font-size:10px;background:var(--surface2);
        color:var(--text);border:1px solid var(--border2);border-radius:4px;padding:1px 2px;outline:none">
        <option value="plex"${backend === 'plex' ? ' selected' : ''}>Plex</option>
        <option value="jellyfin"${backend === 'jellyfin' ? ' selected' : ''}>Jellyfin</option>
      </select>
    </div></div>

    <div id="ps-plex">
      <div class="dp-meta" style="${_PS_META};border-top:1px solid var(--border2);margin-top:4px">plex</div>
      <div style="padding:2px 10px 8px">
        ${pConn ? `<div style="font-size:10px;color:var(--green);margin-bottom:6px">● connected</div>`
                : `<div class="muted" style="font-size:10px;margin-bottom:6px">sign in with your Plex account</div>`}
        <button class="btn accent" style="width:100%" onclick="_playerCloseSettings();plexConnect('${id}')">${pConn ? 'Reconnect' : 'Connect Plex'}</button>
      </div>
    </div>

    <div id="ps-jelly" style="display:none">
      <div class="dp-meta" style="${_PS_META};border-top:1px solid var(--border2);margin-top:4px">jellyfin</div>
      <div style="padding:2px 10px 8px;display:flex;flex-direction:column;gap:6px">
        ${jConn ? `<div style="font-size:10px;color:var(--green)">● connected · ${j.url}</div>` : ''}
        <div class="dp-row" style="margin:0 -10px"><div class="dp-row-pick" style="display:flex;align-items:center" onclick="event.stopPropagation()">
          <span class="dp-row-dim">server</span>
          ${serverField}
        </div></div>
        <div id="ps-status" style="font-size:10px;min-height:14px;color:var(--text3)"></div>
        <button class="btn accent" id="ps-jelly-connect" style="width:100%" onclick="jellyfinConnect('${id}')">${jConn ? 'Reconnect' : 'Quick Connect'}</button>
      </div>
    </div>`;
}

function _playerTypeToggle() {
  const t = document.getElementById('ps-type')?.value || 'plex';
  const pe = document.getElementById('ps-plex'), je = document.getElementById('ps-jelly');
  if (pe) pe.style.display = t === 'plex'     ? '' : 'none';
  if (je) je.style.display = t === 'jellyfin' ? '' : 'none';
}

function _playerTypeChange(id) {
  _playerTypeToggle();
}

async function jellyfinConnect(id) {
  const url  = document.getElementById('ps-url').value.trim();
  const st   = document.getElementById('ps-status');
  const btn  = document.getElementById('ps-jelly-connect');
  if (!url) { if (st) st.textContent = 'server is required'; return; }
  _jellyConnectId = id;
  if (st) st.textContent = 'starting…';
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
  const r = await startProc('player-pair', ['player', 'pair', '--player', 'jellyfin',
                                              '--url', url], { mirror: false });
  if (!r?.ok) _jellyfinConnectEvent(id, { linked: 'False', reason: 'server-unavailable' });
}

function _jellyfinConnectEvent(id, evt) {
  if (_jellyConnectId !== id) return;
  const st = document.getElementById('ps-status');
  const btn = document.getElementById('ps-jelly-connect');
  if (!st || !btn) return;
  if (evt.code) {
    st.innerHTML = `<div>code <span style="font-family:var(--mono);font-size:15px;color:var(--accent);letter-spacing:.08em">${evt.code}</span></div>
      <div style="margin-top:3px">In Jellyfin, open Settings → Quick Connect and enter this code.</div>`;
    btn.disabled = true;
    btn.textContent = 'Waiting…';
    return;
  }
  if (evt.linked === 'False') {
    const expired = evt.reason === 'quick-connect-expired';
    st.textContent = evt.reason === 'quick-connect-disabled' ? 'Quick Connect is disabled on the server'
      : expired ? 'Code expired' : 'Server unavailable';
    btn.disabled = false;
    btn.textContent = expired ? 'New code' : 'Retry';
    _jellyConnectId = null;
  }
}

async function playerOpen(id) {
  let cfg = {};
  try {
    const services = await fetch('/api/config?target=services').then(r => r.json());
    cfg = _mediaServerCfg(services);
  } catch (_) {}
  const url = (cfg[cfg.backend || 'plex'] || {}).url;
  if (url) window.open(url.replace(/\/$/, '') + '/web', '_blank');
}

function _playerApply(id, evt, player) {
  const dev = _tvSmartMember(id);
  if (!dev) return;
  if (evt.tv && dev && evt.tv !== dev) return;
  if (dev && _tvBrandMap[dev]?.paired !== true) {
    _tvPanelUnpaired(id, dev);
    return;
  }

  const on = evt.tv_on === true || evt.tv_on === 'True';
  _tvSetState(id, on);
  if (!on) {
    _tvActiveApp[id] = '';
    _tvSetActiveApp(id, '');
    _tvRenderUpper(id, `<div class="muted">TV unavailable</div>`);
    return;
  }

  const appId = evt.app_id || '';
  if (player && evt.app) _tvPlayer[id] = player;
  _tvPlayerClient[id] = evt.client_id || evt.client || null;
  _tvActiveApp[id] = appId;
  _tvSetActiveApp(id, appId);
  const mediaBackend = _mediaBackendForApp(appId);
  if (mediaBackend && !_mediaConfigured(mediaBackend)) {
    _mediaSetupPrompt(id, mediaBackend);
    return;
  }
  if (appId && !mediaBackend) {
    _tvUpdateNpForApp(id, appId);
    return;
  }
  if (evt.error) {
    _plexUnreachable(id);
    return;
  }
  _playerApplyStatus(id, evt);
}

async function playerHydrate(id) {
  const dev = _tvSmartMember(id);
  try {
    const [players, services] = await Promise.all([
      fetch('/api/config?target=player').then(r => r.json()),
      fetch('/api/config?target=services').then(r => r.json()).catch(() => ({})),
    ]);
    _playerCfg = _mediaServerCfg(services);
    if (!dev) return;
    if (dev && _tvBrandMap[dev]?.paired !== true) { _tvPanelUnpaired(id, dev); return; }
    const playerEntries = Object.entries(services?.['media-center'] || {})
      .filter(([, info]) => !(info.capabilities || []).includes('server'));
    const names = new Set(playerEntries.map(([name]) => name));
    const active = _mediaBackendForApp(_tvBrandMap[dev]?.current_app);
    const latest = Object.entries(players || {})
      .filter(([, state]) => state.tv === dev)
      .sort((a, b) => (Number(b[1].updated_at) || 0) - (Number(a[1].updated_at) || 0))[0];
    if (latest) {
      const [player, state] = latest;
      _playerApply(id, state, names.has(player) ? player : null);
    }
    if (!_tvPlayer[id] && active) {
      const match = playerEntries.find(([, info]) => info.type === active && info.tv === dev);
      if (match) _tvPlayer[id] = match[0];
    }
  } catch (_) {}
}

function _activeMediaPlayer() {
  const id = Object.keys(_tvActiveApp).find(id => _mediaBackendForApp(_tvActiveApp[id]));
  if (!id) return '';
  const backend = _mediaBackendForApp(_tvActiveApp[id]);
  const player = _tvPlayer[id];
  return player && player.startsWith(`${backend}-`) ? player : backend;
}

function _plexUnreachable(id, server) {
  if (!document.getElementById(`${id}-body`)) return;
  const activeApp = _tvActiveApp[id];
  if (!_isPlexApp(activeApp)) return;
  if (!_mediaConfigured('plex')) {
    _mediaSetupPrompt(id, 'plex');
    return;
  }
  setDot(id, 'red');
  _tvRenderUpper(id, `<div class="muted">${server || 'server'} unreachable —
    <a onclick="playerRefresh('${id}')" style="color:var(--accent);cursor:pointer">retry</a></div>`);
}

async function playerControl(id, op) {
  const client = _tvPlayerClient[id];
  const player = _tvPlayer[id];
  await runOp(['player', 'key', op, ...(client ? [client] : []), ...(player ? ['--player', player] : [])]);
}

async function playerRefresh(id) {
  const upper = document.getElementById(`${id}-player-upper`);
  if (upper && !upper.dataset.playerReady && !_tvActiveApp[id]) _tvRenderUpper(id, `<div class="muted">loading…</div>`);
  const dev = _tvSmartMember(id);
  const player = _tvPlayer[id];
  const r = await runOp(['player', 'status', ...(dev ? ['--tv', dev] : []),
                         ...(player ? ['--player', player] : [])]);
  if (!r?.ok) { _plexUnreachable(id); return; }
  await playerHydrate(id);
}

function _playerApplyStatus(id, evt) {
  if (!document.getElementById(`${id}-body`)) return;

  // don't overwrite if a non-media app is active
  const activeApp = _tvActiveApp[id];
  if (activeApp && !_mediaBackendForApp(activeApp)) return;

  const upper = document.getElementById(`${id}-player-upper`);
  if (!upper) return;
  upper.dataset.playerReady = '1';

  const idle      = !evt.state || evt.state === 'stopped';
  const isPlaying = !idle && evt.state === 'playing';
  const isEpisode = !idle && evt.type === 'episode';

  const topLine = isEpisode ? evt.grandparent || evt.title : evt.title;
  const sub     = isEpisode
    ? `S${evt.season||'?'} · E${evt.episode||'?'}${evt.title ? '  ·  ' + evt.title : ''}`
    : (evt.year || '');

  const statePill = idle
    ? `<span class="status-pill" style="opacity:.45">Idle</span>`
    : `<span class="status-pill ${isPlaying ? 'green' : 'amber'}">${isPlaying ? '● Playing' : '❚❚ Paused'}</span>`;
  const controls = `
    <div style="display:flex;gap:2px;margin-top:2px">
    <button class="icon-btn" onclick="playerControl('${id}','play')" title="Play" ${idle || isPlaying ? 'disabled style="opacity:.25;cursor:default"' : ''}>
      <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="m7 4 13 8-13 8z"/></svg>
    </button>
    <button class="icon-btn" onclick="playerControl('${id}','pause')" title="Pause" ${idle || !isPlaying ? 'disabled style="opacity:.25;cursor:default"' : ''}>
      <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4h4v16H6zM14 4h4v16h-4z"/></svg>
    </button>
    <button class="icon-btn" onclick="playerControl('${id}','stop')" title="Stop" ${idle ? 'disabled style="opacity:.25;cursor:default"' : ''}>
      <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M5 5h14v14H5z"/></svg>
    </button>
    </div>`;

  const poster = (!idle && evt.thumb)
    ? `<img src="${evt.thumb}" alt="" onerror="this.style.display='none'"
         style="width:44px;height:66px;object-fit:cover;border-radius:5px;flex-shrink:0;border:1px solid var(--border)">`
    : `<div style="width:44px;height:66px;border-radius:5px;flex-shrink:0;border:1px solid var(--border);background:var(--surface2);display:flex;align-items:center;justify-content:center;color:var(--text3);opacity:.5;font-size:16px">▶</div>`;

  upper.innerHTML = `
    <div style="display:flex;gap:8px;align-items:flex-start">
      ${poster}
      <div style="min-width:0;flex:1;display:flex;flex-direction:column;gap:2px">
        ${idle ? '' : `<div style="font-size:11px;font-weight:700;line-height:1.2;word-break:break-word">${topLine || '—'}</div>`}
        ${idle || !sub ? '' : `<div style="font-size:9px;color:var(--text3);font-family:var(--mono)">${sub}</div>`}
        <div style="margin-top:1px">${statePill}</div>
        ${controls}
      </div>
    </div>`;
}

// ══════════════════════════════════════════════════════════════════════════
// TV LOGIC
// ══════════════════════════════════════════════════════════════════════════

function _tvSetState(id, on) {
  _tvOn[id] = on; setDot(id, on ? 'green' : 'gray');
  const pwBtn = document.getElementById(`${id}-power`);
  if (pwBtn) pwBtn.classList.toggle('active', on);
}

function _tvPowerToggle(id) {
  const sm = _tvSmartMember(id);
  if (!sm) return;
  if (_tvOn[id]) {
    runOp(_tvArgs(id, 'tv', 'off'));
    _tvSetState(id, false);
  } else {
    runOp(_tvArgs(id, 'tv', 'on'));
    _tvSetState(id, true);
    setTimeout(() => playerRefresh(id), 8000);
  }
}

function _tvSetActiveApp(id, appId) {
  const body = document.getElementById(`${id}-body`);
  if (!body) return;
  body.querySelectorAll('.tv-app-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.appid && btn.dataset.appid === appId);
  });
}

function _tvRenderSidebar(id) {
  const el = document.getElementById(`${id}-sidebar`);
  if (!el) return;
  el.innerHTML = _playerSidebar(id, _tvTvsApps[id] || [], _tvActiveApp[id]);
}

function _tvAppFriendlyName(appId) {
  if (!appId) return '—';
  const known = {
    'com.plexapp.android': 'Plex', 'com.plexapp.plex': 'Plex',
    'org.jellyfin.androidtv': 'Jellyfin', 'org.jellyfin.webos': 'Jellyfin', 'org.jellyfin.tizen': 'Jellyfin',
    'com.google.android.youtube.tv': 'YouTube', 'com.google.ios.youtube': 'YouTube',
    'com.spotify.tv.android': 'Spotify',
    'com.netflix.ninja': 'Netflix', 'com.netflix.Netflix': 'Netflix',
    'com.disney.disneyplus': 'Disney+',
    'com.amazon.amazonvideo.livingroom': 'Prime Video', 'com.amazon.amazonvideo': 'Prime Video',
    'com.apple.atve.androidtv.appletv': 'Apple TV', 'com.apple.tv': 'Apple TV',
    'com.wbd.stream': 'Max', 'com.hbo.max': 'Max',
    'tv.twitch.android.app': 'Twitch',
    'com.google.android.apps.tv.launcherx': 'Home',
  };
  if (known[appId]) return known[appId];
  const parts = appId.split('.');
  return parts[parts.length - 1].replace(/([a-z])([A-Z])/g, '$1 $2');
}

function _tvUpdateNpForApp(id, appId, appTitle) {
  const isPlex = !appId || _isPlexApp(appId);
  if (isPlex) return;
  const upper = document.getElementById(`${id}-player-upper`);
  if (!upper) return;
  const title = _tvAppFriendlyName(appId);
  const icon = _tvAppIcon(appId);
  const imgHtml = icon
    ? `<img src="${icon}" onerror="this.style.display='none'" style="width:28px;height:28px;border-radius:6px;object-fit:contain">`
    : `<div style="width:28px;height:28px;border-radius:6px;background:var(--surface2);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;color:var(--text3)">${title[0]}</div>`;
  upper.innerHTML = `<div style="display:flex;gap:10px;align-items:center;justify-content:center;height:100%;overflow:hidden">
    ${imgHtml}
    <div style="font-size:12px;font-weight:700;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${title}</div>
  </div>`;
}

async function tvRefresh(id) {
  await _tvEnsureBrands();
  const dev = _tvSmartMember(id) || _tvDev(id);
  if (dev) {
    _tvTvsApps[id] = _tvBrandMap[dev]?.apps || [];
    _tvRenderSidebar(id);
  }
  await playerHydrate(id);
}

function _tvRenderApps(id, list, apps) {
  const rest = (apps || []).filter(a => !_tvQuickApps.some(q => q.appId === a.appId));
  list.innerHTML = rest.length
    ? rest.map(a => `<button onclick="tvLaunchApp('${id}','${a.appId.replace(/'/g,"\\'")}','${a.title.replace(/'/g,"\\'")}')">${a.title}</button>`).join('')
    : `<button style="color:var(--text3);cursor:default">no other apps</button>`;
}

function tvAppToggle(id) {
  const list = document.getElementById(`${id}-applist`);
  if (!list) return;
  const open = list.style.display === 'none';
  list.style.display = open ? '' : 'none';
  document.getElementById(`${id}-appmore`)?.classList.toggle('active', open);
  if (open) _tvRenderApps(id, list, _tvApps[id]?.length ? _tvApps[id] : null);
}

async function tvLaunchApp(id, appId, title) {
  const r = await runOp(_tvArgs(id, 'tv', 'app', appId));
  if (r?.ok) {
    _tvActiveApp[id] = appId;
    _tvSetActiveApp(id, appId);
    _tvUpdateNpForApp(id, appId, title);
    setTimeout(() => playerRefresh(id), 1200);
  }
}

// ── one-click pairing ──────────────────────────────────────────────────────

let _tvPairListening = false;
let _tvPairState = null;
let _tvPairWatch = null;

function _tvPairEnsureListener() {
  if (_tvPairListening) return;
  _tvPairListening = true;
  onEvent('tv.pair', evt => { _tvPairState?.onEvt?.(evt); });
  onEvent('proc.exit', evt => {
    if (_tvPairState && evt.source === _tvPairState.proc)
      _tvPairState.onEvt?.({ status: 'exit', code: evt.code });
  });
}

async function _tvPairGuide(id, name) {
  if (typeof _showModal !== 'function') return;
  _tvPairEnsureListener();

  const tile = dashboard?.tiles?.find(t => t.id === id);
  if (tile) {
    if (!tile.config) tile.config = {};
    tile.config.devices = [name];
    const el = document.getElementById(`${id}-label`);
    if (el) el.textContent = tile.config.label || '—';
    if (typeof saveDashboard === 'function') saveDashboard();
  }

  const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const nameSafe = esc(name);
  const procId = `tv-pair-${name}`;

  _showModal(`
    <h3>Pair ${nameSafe}</h3>
    <div id="tv-pair-msg" style="font-size:13px;color:var(--text2);line-height:1.55">Click Pair, then watch your TV for a 6-digit code.</div>
    <div id="tv-pair-codewrap" style="display:none;margin-top:12px">
      <input id="tv-pair-code" maxlength="6" inputmode="numeric" autocomplete="off" placeholder="000000"
        style="font-family:var(--mono);font-size:24px;letter-spacing:.25em;text-align:center;width:100%;box-sizing:border-box;
               background:var(--surface2);border:1px solid var(--border2);border-radius:10px;padding:12px;color:var(--text)" />
    </div>
    <div id="tv-pair-status" style="font-size:11px;min-height:18px;margin-top:10px;color:var(--text3)"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
      <button class="btn" id="tv-pair-cancel">Cancel</button>
      <button class="btn accent" id="tv-pair-go">Pair</button>
    </div>
  `);

  const msgEl    = document.getElementById('tv-pair-msg');
  const wrap     = document.getElementById('tv-pair-codewrap');
  const codeEl   = document.getElementById('tv-pair-code');
  const statusEl = document.getElementById('tv-pair-status');
  const goBtn    = document.getElementById('tv-pair-go');
  const cancelBtn = document.getElementById('tv-pair-cancel');

  let settled = false;
  const setStatus = (txt, color) => { statusEl.textContent = txt; statusEl.style.color = color || 'var(--text3)'; };

  const startPairing = async () => {
    settled = false;
    msgEl.textContent = 'Look at your TV for the 6-digit code…';
    wrap.style.display = 'none'; codeEl.value = '';
    goBtn.textContent = 'Pair'; goBtn.disabled = true; goBtn.onclick = startPairing;
    setStatus('Starting…');
    await stopProc(procId);
    const r = await startProc(procId, ['tv', 'pair', '--tv', name]);
    if (!r || !r.ok) {
      fail('Could not start pairing — check server logs.');
      return;
    }
  };

  const submitCode = async () => {
    const code = (codeEl.value || '').trim();
    if (code.length < 4) { codeEl.focus(); return; }
    goBtn.disabled = true;
    setStatus('Verifying code…');
    let r;
    try { r = await sendProcInput(procId, code); }
    catch (e) { fail('Could not reach the server — ' + (e?.message || 'network error')); return; }
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      fail(`Couldn't send code (HTTP ${r.status}${d.detail ? ': ' + d.detail : ''}). Restart the web server, then Try again.`);
      return;
    }
    clearTimeout(_tvPairWatch);
    _tvPairWatch = setTimeout(() => {
      if (!settled) setStatus('Still pairing… if the TV didn\'t accept the code, click Try again.', 'var(--text3)');
    }, 20000);
  };

  const succeed = () => {
    if (settled) return; settled = true;
    clearTimeout(_tvPairWatch);
    setStatus('Paired ✓', 'var(--green,#34d399)');
    _tvSetState(id, true);
    _tvPairState = null;
    setTimeout(closeModal, 900);
  };

  const fail = txt => {
    if (settled) return; settled = true;
    clearTimeout(_tvPairWatch);
    setStatus(txt, 'var(--red,#ef4444)');
    wrap.style.display = 'none';
    goBtn.textContent = 'Try again'; goBtn.disabled = false; goBtn.onclick = startPairing;
  };

  codeEl.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); if (goBtn.textContent === 'Submit') submitCode(); } };

  cancelBtn.onclick = () => { clearTimeout(_tvPairWatch); _tvPairState = null; stopProc(procId); closeModal(); };
  goBtn.onclick = startPairing;

  _tvPairState = {
    proc: procId, name,
    onEvt: evt => {
      if (evt.tv && evt.tv !== name) return;
      if (evt.status === 'waiting_code') {
        settled = false;
        msgEl.textContent = 'Enter the 6-digit code shown on your TV:';
        wrap.style.display = ''; setStatus('');
        goBtn.textContent = 'Submit'; goBtn.disabled = false; goBtn.onclick = submitCode;
        setTimeout(() => codeEl.focus(), 50);
      } else if (evt.status === 'paired') {
        succeed();
      } else if (evt.status === 'error') {
        fail((evt.msg || 'Pairing failed') + ' — try again');
      } else if (evt.status === 'exit') {
        if (String(evt.code) === '0') succeed();
        else fail('Pairing failed — try again');
      }
    },
  };
}


// ══════════════════════════════════════════════════════════════════════════
// CSS — stacked layout, compact wheel, compact controls
// ══════════════════════════════════════════════════════════════════════════

(function _injectPlayerStackedCSS() {
  if (document.getElementById('player-stacked-css')) return;
  const style = document.createElement('style');
  style.id = 'player-stacked-css';
  style.textContent = `
    .player-stacked-body {
      flex: 1; display: flex; flex-direction: row;
      padding: 8px 10px 8px; min-height: 0; overflow: visible; gap: 0;
    }
    .player-main {
      flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0;
    }
    .player-upper {
      flex: 1; min-height: 0; padding: 2px 0 0; display: flex; flex-direction: column; justify-content: center;
    }
    .player-lower {
      display: flex; flex-direction: column; flex-shrink: 0;
    }
    .player-lower .player-divider {
      height: 1px; background: var(--border); margin: 5px 0; flex-shrink: 0;
    }
    .player-lower .player-tv-area {
      flex: 1.1; min-height: 0; width: 100%;
      display: flex; align-items: center; justify-content: space-evenly; gap: 2px;
    }
    .player-np-section {
      flex: 1; min-height: 0; padding: 2px 0 0; display: flex; flex-direction: column; justify-content: center;
    }
    .player-divider {
      height: 1px; background: var(--border); margin: 5px 0; flex-shrink: 0;
    }
    .player-tv-area {
      flex: 1.1; min-height: 0; width: 100%;
      display: flex; align-items: center; justify-content: space-evenly; gap: 2px;
    }
    .player-app-panel {
      display: flex; flex-direction: column; align-items: center; gap: 4px;
      width: 76px; box-sizing: border-box;
      flex-shrink: 0; min-height: 0; padding-left: 8px; margin-top: -6px;
    }
    .player-app-panel .tv-pw-btn { flex-shrink: 0; align-self: flex-end; width: 22px; height: 22px; }
    .player-app-panel .tv-pw-btn svg { width: 12px; height: 12px; }
    .player-app-list { min-height: 0; display: flex; }
    .player-sidebar {
      position: relative; flex-shrink: 0; display: grid; grid-template-columns: 1fr 1fr;
      grid-auto-rows: minmax(0, 32px); gap: 4px;
      align-content: start; overflow: visible;
    }
    .player-sidebar .tv-app-btn { width: 32px; height: 100%; min-height: 0; transition: all 0.15s ease; }
    .player-sidebar .tv-app-btn img { width: 21px; height: 21px; border-radius: 3px; object-fit: contain; }
    .player-sidebar .tv-app-more { width: 32px; height: 100%; min-height: 0; font-size: 16px; color: var(--text2); }
    .player-sidebar .tv-app-dropdown {
      position: absolute; z-index: 3; right: 0; top: 50%; transform: translateY(-50%);
      display: flex; flex-direction: column; gap: 3px; min-width: 132px; max-height: 180px;
      overflow-y: auto; padding: 6px; background: var(--surface2);
      border: 1px solid var(--border2); border-radius: 8px; box-shadow: 0 8px 20px rgba(0,0,0,.35);
    }
    .player-sidebar .tv-app-dropdown button {
      display: flex; align-items: center; gap: 7px; width: 100%; min-height: 28px;
      padding: 3px 5px; border: 0; border-radius: 5px; color: var(--text);
      background: transparent; font-size: 11px; text-align: left; cursor: pointer;
    }
    .player-sidebar .tv-app-dropdown button:hover { background: var(--surface3); }
    .player-sidebar .tv-app-dropdown img { width: 19px; height: 19px; border-radius: 3px; object-fit: contain; flex-shrink: 0; }
    .player-sidebar .tv-app-dropdown-fallback {
      width: 19px; height: 19px; display: inline-flex; align-items: center; justify-content: center;
      color: var(--text2); font-size: 10px; font-weight: 700; flex-shrink: 0;
    }
    .player-sidebar .tv-app-btn:has(img) { border: 1px solid var(--border2); }
    .player-sidebar .tv-app-btn.active {
      border-color: var(--accent) !important;
      background: var(--accent-dim);
      box-shadow: 0 0 10px var(--accent-border);
      transform: scale(1.08);
    }
    .tv-wheel { flex-shrink: 0; }
    .tv-wheel .tv-seg {
      fill: var(--surface2); stroke: var(--border); stroke-width: 1;
      cursor: pointer; transition: fill 0.12s;
    }
    .tv-wheel .tv-seg:hover,
    .tv-wheel .tv-ok:hover  { fill: var(--accent-dim); }
    .tv-wheel .tv-seg:active,
    .tv-wheel .tv-ok:active { fill: var(--accent-mid); }
    .tv-wheel .tv-ok {
      fill: var(--surface2); cursor: pointer; transition: fill 0.12s;
    }
    .tv-wheel .tv-glyph { fill: var(--text3); pointer-events: none; user-select: none; }

    .tv-ctl-group {
      flex-shrink: 0; position: relative;
      display: flex; flex-direction: column; align-items: center; gap: 2px;
    }
    .player-tv-area .tv-ctl-group .icon-btn { width: 26px; height: 26px; }
    .player-tv-area .tv-vol .icon-btn svg,
    .player-tv-area .tv-util .icon-btn svg { width: 14px; height: 14px; }
  `;
  document.head.appendChild(style);
})();
