// web.js

// ── State ──────────────────────────────────────────────────────────────────
let dashboard   = { tiles: [] };
let editMode    = false;
let _editDashboardSnapshot = null;
let _pendingRemovedTileIds = new Set();
let dragSrc     = null;
let tileRefs    = {};
let _currentUser = null;
let _sourcesCamera = {};
let _sourcesAudio  = {};
let _sourcesPushFps = 2;
let _myIpCache = null;
const _termTailES = {};
const _COLS_MIN = 2, _COLS_MAX = 8;
let _devicesContributed = false;
const _tvMode = new URLSearchParams(location.search).get('display') === 'tv';
let _tvObserver = null;
let _tvLastTileFocus = null;
let _tvRestoreFrame = 0;
let _tvKeyboardGesture = false;

const _TV_FOCUS_SELECTOR = [
  '[data-tv-tile-title]',
  'button',
  'a[href]',
  'input:not([type="hidden"])',
  'select',
  'textarea',
  'video[controls]',
  'audio[controls]',
  '[onclick]',
  '[role="button"]',
  '[role="menuitem"]',
  '[role="tab"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="switch"]',
  '[contenteditable="true"]'
].join(',');

// ── Event bus ──────────────────────────────────────────────────────────────
const _handlers = [];

function onEvent(prefix, fn) {
  _handlers.push({ prefix, fn });
}

function _routeEvent(evt) {
  const hint = evt.hint || '';
  for (const h of _handlers) {
    if (hint === h.prefix || hint.startsWith(h.prefix + '.') || h.prefix === '*') {
      try { h.fn(evt); } catch(e) {}
    }
  }
}

function _connectEvents() {
  const es = new EventSource('/api/events');
  es.onopen    = () => { document.querySelector('.logo-dot')?.style.setProperty('background', 'var(--green)'); };
  es.onmessage = e => { try { _routeEvent(JSON.parse(e.data)); } catch(_) {} };
  es.onerror   = () => { es.close(); setTimeout(_connectEvents, 3000); };
}

// ── View Preferences ────────────────────────────────────────────────────────
const THEMES = [
  { id: 'light',           name: 'Light',           swatch: ['#f0f1f5', '#ffffff', '#2f6fe0', '#1a1c26'] },
  { id: 'dark',            name: 'Midnight',        swatch: ['#0a0b0f', '#181b22', '#4f8ef7', '#e8eaf0'] },
  { id: 'nord',            name: 'Nord',            swatch: ['#2e3440', '#434c5e', '#88c0d0', '#eceff4'] },
  { id: 'dracula',         name: 'Dracula',         swatch: ['#282a36', '#44475a', '#bd93f9', '#f8f8f2'] },
  { id: 'solarized-dark',  name: 'Solarized Dark',  swatch: ['#002b36', '#0a4453', '#268bd2', '#c1d0d0'] },
  { id: 'solarized-light', name: 'Solarized Light', swatch: ['#fdf6e3', '#eee8d5', '#268bd2', '#073642'] },
  { id: 'gruvbox',         name: 'Gruvbox Dark',    swatch: ['#282828', '#3c3836', '#fabd2f', '#ebdbb2'] },
];
function _currentTheme() {
  let t = localStorage.getItem('theme');
  if (!t) t = localStorage.getItem('lightMode') === '0' ? 'dark' : 'light'; // migrate old pref
  return THEMES.some(x => x.id === t) ? t : 'gruvbox';
}
function _applyPrefs() {
  const lg = localStorage.getItem('fontLg') === '1';
  document.body.classList.toggle('font-lg', lg);
  const theme = _currentTheme();
  document.documentElement.setAttribute('data-theme', theme);
  const def = THEMES.find(x => x.id === theme);
  if (def) document.querySelector('meta[name=theme-color]')?.setAttribute('content', def.swatch[0]);
}
function toggleFontSize() {
  localStorage.setItem('fontLg', localStorage.getItem('fontLg') !== '1' ? '1' : '0');
  _applyPrefs();
}
function setTheme(id) {
  localStorage.setItem('theme', id);
  _applyPrefs();
  _renderThemePicker();
}
function _renderThemePicker() {
  const cur = _currentTheme();
  const cards = THEMES.map(t => `
    <button class="theme-card ${t.id === cur ? 'active' : ''}" onclick="setTheme('${t.id}')">
      <span class="theme-swatch">${t.swatch.map(c => `<span style="background:${c}"></span>`).join('')}</span>
      <span class="tname">${t.name}</span>
      <i class="tcheck" data-lucide="check"></i>
    </button>`).join('');
  document.getElementById('modal').innerHTML = `
    <h3>Theme</h3>
    <div class="theme-grid">${cards}</div>
    <div style="display:flex;justify-content:flex-end">
      <button class="btn" onclick="closeModal()">close</button>
    </div>`;
  lucide?.createIcons();
}
function openThemePicker() {
  _renderThemePicker();
  const modal = document.getElementById('modal');
  if (modal) modal.dataset.locked = 'false';
  document.getElementById('modal-backdrop').style.display = 'flex';
}
function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen().catch(() => {});
  }
}

function _bindFullscreenIconSync() {
  document.addEventListener('fullscreenchange', () => {
    const icon = document.querySelector('#fs-btn i');
    if (!icon) return;
    icon.setAttribute('data-lucide', document.fullscreenElement ? 'minimize' : 'maximize');
    lucide?.createIcons();
  });
}

// ── Session / Init ──────────────────────────────────────────────────────────
// Android TV / D-pad navigation. Activated only by ?display=tv.
function _initTvNavigation() {
  if (!_tvMode || document.body.classList.contains('tv-mode')) return;
  document.body.classList.add('tv-mode');
  _tvDecorateFocusables(document);

  document.addEventListener('keydown', _tvKeydown, true);
  document.addEventListener('pointerdown', e => {
    if (_tvIsTextInput(e.target)) {
      _tvKeyboardGesture = true;
      _tvRequestKeyboard();
    }
  }, true);
  document.addEventListener('focusin', e => {
    if (!(e.target instanceof HTMLElement)) return;
    const tile = e.target.closest('.tile');
    tile?.classList.add('tv-has-focus');
    if (_tvIsTextInput(e.target)) {
      if (_tvKeyboardGesture) _tvKeyboardGesture = false;
      else _tvHideKeyboard();
    }
    if (tile && e.target.dataset.tvFocusable === 'true') {
      _tvLastTileFocus = {
        tileId: tile.dataset.id || '',
        focusKey: e.target.dataset.tvFocusKey || '',
        element: e.target
      };
    }
    if (_tvLastTileFocus && !tile && e.target.id === 'user-chip') _tvScheduleRestore();
  });
  document.addEventListener('focusout', e => {
    const tile = e.target instanceof HTMLElement ? e.target.closest('.tile') : null;
    if (!tile) return;
    requestAnimationFrame(() => {
      if (!tile.contains(document.activeElement)) tile.classList.remove('tv-has-focus');
    });
  });

  _tvObserver = new MutationObserver(mutations => {
    for (const mutation of mutations) {
      if (mutation.type === 'childList') {
        mutation.addedNodes.forEach(node => {
          if (node.nodeType === Node.ELEMENT_NODE) _tvDecorateFocusables(node);
        });
      } else if (mutation.target instanceof HTMLElement) {
        _tvDecorateFocusables(mutation.target);
      }
      if (_tvLastTileFocus?.focusKey && !_tvHasActiveTileFocus()) _tvScheduleRestore();
    }
  });
  _tvObserver.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['disabled', 'aria-disabled', 'style', 'class']
  });
}

function _tvHasActiveTileFocus() {
  const active = document.activeElement;
  return active instanceof HTMLElement
    && active.closest('.tile')
    && active.dataset.tvFocusable === 'true';
}

function _tvScheduleRestore() {
  if (!_tvMode || !_tvLastTileFocus || _tvActiveScope() !== document) return;
  if (_tvLastTileFocus.element?.isConnected
      && document.activeElement !== document.body) return;
  if (document.activeElement instanceof HTMLElement
      && document.activeElement.closest('.tile')) return;
  cancelAnimationFrame(_tvRestoreFrame);
  _tvRestoreFrame = requestAnimationFrame(() => {
    const active = document.activeElement;
    const userChip = document.getElementById('user-chip');
    if (active !== document.body && active !== userChip) return;
    const tile = [...document.querySelectorAll('.tile')]
      .find(el => el.dataset.id === _tvLastTileFocus.tileId);
    if (!tile) return;
    const target = [...tile.querySelectorAll('[data-tv-focusable="true"]')]
      .find(el => el.dataset.tvFocusKey === _tvLastTileFocus.focusKey)
      || tile.querySelector('[data-tv-tile-title]');
    _tvFocus(target);
  });
}

function _tvDecorateFocusables(root) {
  if (!_tvMode || !(root instanceof Element || root instanceof Document)) return;
  const elements = new Set();
  if (root instanceof Element && root.matches(_TV_FOCUS_SELECTOR)) elements.add(root);
  if (root instanceof Element && typeof root.onclick === 'function') elements.add(root);
  root.querySelectorAll?.(_TV_FOCUS_SELECTOR).forEach(el => elements.add(el));
  root.querySelectorAll?.('*').forEach(el => {
    if (typeof el.onclick === 'function') elements.add(el);
  });

  for (const el of elements) {
    if (!(el instanceof HTMLElement)) continue;
    if (el.matches('[disabled], [aria-disabled="true"]')) continue;
    if (!el.hasAttribute('tabindex')) el.tabIndex = 0;
    el.dataset.tvFocusable = 'true';

    if (el.hasAttribute('data-tv-tile-title')) {
      el.setAttribute('role', 'heading');
      el.setAttribute('aria-level', '2');
      if (!el.getAttribute('aria-label')) {
        const label = el.textContent.trim().replace(/\s+/g, ' ');
        if (label) el.setAttribute('aria-label', label);
      }
    } else if ((el.hasAttribute('onclick') || typeof el.onclick === 'function')
        && !/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) {
      if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
    }
  }

  for (const el of elements) {
    if (el instanceof HTMLElement && el.dataset.tvFocusable === 'true') {
      const base = _tvFocusBase(el);
      const tile = el.closest('.tile');
      const peers = tile
        ? [...tile.querySelectorAll('[data-tv-focusable="true"]')]
            .filter(peer => _tvFocusBase(peer) === base)
        : [];
      const ordinal = Math.max(0, peers.indexOf(el));
      el.dataset.tvFocusKey = `${base}:${ordinal}`;
    }
  }
}

function _tvFocusBase(el) {
  if (el.hasAttribute('data-tv-tile-title')) return 'tile-title';
  if (el.id) return `id:${el.id}`;
  const title = el.getAttribute('title');
  if (title) return `title:${title}`;
  const label = el.getAttribute('aria-label');
  if (label) return `label:${label}`;
  const action = el.getAttribute('onclick');
  if (action) return `action:${action}`;
  const text = el.textContent.trim().replace(/\s+/g, ' ').slice(0, 40);
  return `${el.tagName.toLowerCase()}:${text}`;
}

function _tvIsVisible(el) {
  if (!(el instanceof HTMLElement) || !el.isConnected) return false;
  if (el.matches('[disabled], [aria-disabled="true"], [aria-hidden="true"]')) return false;
  if (el.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
  const style = getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function _tvActiveScope() {
  for (const selector of ['#modal-backdrop', '#sec-label-backdrop', '#lightbox']) {
    const overlay = document.querySelector(selector);
    if (overlay && _tvIsVisible(overlay)) return overlay;
  }
  return document;
}

function _tvFocusables() {
  const scope = _tvActiveScope();
  _tvDecorateFocusables(scope);
  return [...scope.querySelectorAll('[data-tv-focusable="true"]')].filter(_tvIsVisible);
}

function _tvFocus(el) {
  if (!el) return false;
  try { el.focus({ preventScroll: true }); } catch (_) { el.focus(); }
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
  return document.activeElement === el;
}

function _tvFocusFirstTile(preferredTileId = '') {
  if (!_tvMode) return;
  const preferred = preferredTileId ? tileRefs[preferredTileId] : null;
  const title = preferred?.querySelector('[data-tv-tile-title]')
    || document.querySelector('#tile-grid [data-tv-tile-title]');
  _tvFocus(title);
}

function _tvIsTextInput(el) {
  return el instanceof HTMLElement
    && el.matches('textarea, input:not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="submit"]), [contenteditable="true"]');
}

function _tvRequestKeyboard() {
  _tvKeyboardGesture = true;
  try { window.MediactlTv?.showKeyboard?.(); } catch (_) {}
  setTimeout(() => { _tvKeyboardGesture = false; }, 500);
}

function _tvHideKeyboard() {
  try { window.MediactlTv?.hideKeyboard?.(); } catch (_) {}
}

function _tvTextControlOwnsArrow(el, key) {
  if (!(el instanceof HTMLElement)) return false;
  if (el.matches('textarea, [contenteditable="true"]')) return true;
  if (el.matches('input[type="range"]')) return key === 'ArrowLeft' || key === 'ArrowRight';
  if (el.matches('input:not([type]), input[type="text"], input[type="search"], input[type="email"], input[type="url"], input[type="tel"], input[type="number"], input[type="password"]')) {
    return key === 'ArrowLeft' || key === 'ArrowRight';
  }
  return el.matches('video[controls], audio[controls]');
}

function _tvKeydown(event) {
  if (!_tvMode || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  const current = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  if (event.key === 'Enter' || event.key === ' ') {
    if (!current || event.repeat) return;
    if (_tvIsTextInput(current)) {
      event.preventDefault();
      _tvKeyboardGesture = true;
      _tvRequestKeyboard();
      return;
    }
    const nativeActivation = current.matches('button, a[href], input, select, textarea, video, audio');
    if (!nativeActivation && (current.hasAttribute('onclick')
        || typeof current.onclick === 'function'
        || current.getAttribute('role') === 'button')) {
      event.preventDefault();
      current.click();
    }
    return;
  }

  if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  if (current && _tvTextControlOwnsArrow(current, event.key)) return;
  event.preventDefault();
  event.stopPropagation();
  _tvMoveFocus(event.key, current);
}

function _tvMoveFocus(key, current) {
  const focusables = _tvFocusables();
  if (!focusables.length) return false;
  if (!current || !focusables.includes(current) || !_tvIsVisible(current)) {
    return _tvFocus(focusables[0]);
  }

  const from = current.getBoundingClientRect();
  const fromX = from.left + from.width / 2;
  const fromY = from.top + from.height / 2;
  const horizontal = key === 'ArrowLeft' || key === 'ArrowRight';
  const positive = key === 'ArrowRight' || key === 'ArrowDown';
  const candidates = [];

  for (const el of focusables) {
    if (el === current) continue;
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const primaryDelta = horizontal ? x - fromX : y - fromY;
    if ((positive && primaryDelta <= 2) || (!positive && primaryDelta >= -2)) continue;

    const primary = Math.abs(primaryDelta);
    const secondary = horizontal ? Math.abs(y - fromY) : Math.abs(x - fromX);
    const beamOverlap = horizontal
      ? rect.bottom > from.top && rect.top < from.bottom
      : rect.right > from.left && rect.left < from.right;
    const sameTile = el.closest('.tile') === current.closest('.tile');
    const score = primary + secondary * 2.5 - (sameTile ? 12 : 0);
    candidates.push({ el, score, beamOverlap });
  }

  if (!candidates.length) return false;
  const beam = candidates.filter(candidate => candidate.beamOverlap);
  const pool = beam.length ? beam : candidates;
  pool.sort((a, b) => a.score - b.score);
  return _tvFocus(pool[0].el);
}

async function init() {
  _applyPrefs();
  _bindFullscreenIconSync();
  _initTvNavigation();

  const meRes = await fetch('/api/me');
  if (meRes.status === 401) {
    _startRegistrationFromAuthGate?.();
    return;
  }
  _currentUser = await meRes.json();
  _connectEvents();
  _renderUserChip(_currentUser);
  document.getElementById('panels').style.visibility = 'visible';

  dashboard = await fetch('/api/dashboard').then(r => r.json()).catch(() => ({ tiles: [] }));
  if (!dashboard.layout) dashboard.layout = 'default';
  _userMenuActiveName = dashboard.layout;

  _applyCols();
  renderGrid();
  _registerAllHandlers();
  _registerWebUserHandlers();
  _registerWebLifecycleHandlers();
  _postUserDevices();
}

// ── Grid ───────────────────────────────────────────────────────────────────
function renderGrid() {
  const focusedTileId = _tvMode
    ? document.activeElement?.closest?.('.tile')?.dataset.id || ''
    : '';
  Object.values(_termTailES).forEach(es => es.close());
  Object.keys(_termTailES).forEach(k => delete _termTailES[k]);
  const grid = document.getElementById('tile-grid');
  grid.querySelectorAll('.tile').forEach(el => TILES[el.dataset.type]?.unbind?.(el.dataset.id));
  document.body.classList.toggle('browse-layout', dashboard.tiles.length === 1 && dashboard.tiles[0].type === 'browse');
  grid.innerHTML = '';
  tileRefs = {};
  dashboard.tiles.forEach(tile => {
    const el = createTile(tile);
    tileRefs[tile.id] = el;
    grid.appendChild(el);
  });
  if (_tvMode) {
    requestAnimationFrame(() => {
      _tvDecorateFocusables(grid);
      if (document.activeElement === document.body || !document.activeElement?.isConnected) {
        if (_tvLastTileFocus?.tileId) _tvScheduleRestore();
        else _tvFocusFirstTile(focusedTileId);
      }
    });
  }
}

function createTile(tile) {
  const el  = document.createElement('div');
  const def = TILES[tile.type] || TILES['_unknown'];
  if (!def) { console.warn('No tile definition for type:', tile.type); return el; }
  el.className  = `tile tile-${tile.type}`;
  el.dataset.id = tile.id;
  el.dataset.type = tile.type;
  el.draggable  = editMode;
  if ((tile.w || 1) > 1) el.style.gridColumn = `span ${tile.w}`;
  if ((tile.h || 1) > 1) el.style.gridRow    = `span ${tile.h}`;
  el.innerHTML  = def.html(tile);
  _bindDrag(el, tile);
  setTimeout(() => { if (el.isConnected) def.bind(tile.id, tile); }, 200);
  return el;
}

function _bindDrag(el, tile) {
  el.addEventListener('dragstart', () => { dragSrc = tile.id; el.style.opacity = '.5'; });
  el.addEventListener('dragend',   () => { el.style.opacity = ''; });
  el.addEventListener('dragover',  e => { e.preventDefault(); el.classList.add('drag-over'); });
  el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
  el.addEventListener('drop', e => {
    e.preventDefault(); el.classList.remove('drag-over');
    if (dragSrc && dragSrc !== tile.id) {
      const ai = dashboard.tiles.findIndex(t => t.id === dragSrc);
      const bi = dashboard.tiles.findIndex(t => t.id === tile.id);
      if (ai >= 0 && bi >= 0) {
        [dashboard.tiles[ai], dashboard.tiles[bi]] = [dashboard.tiles[bi], dashboard.tiles[ai]];
        dragSrc = null;
        renderGrid();
      }
    }
  });
}

// ── Layout Editing ──────────────────────────────────────────────────────────
function _syncEditModeUI() {
  const tabs = document.getElementById('tabs');
  for (const id of ['edit-done-btn', 'edit-layout-btn', 'edit-cancel-btn']) {
    document.getElementById(id).style.display = editMode ? '' : 'none';
  }
  if (tabs) tabs.classList.toggle('editing', editMode);
  document.querySelectorAll('.tile').forEach(t => t.draggable = editMode);
}
function enterEditMode() {
  if (editMode || dashboard.view?.editable === false) return;
  _editDashboardSnapshot = JSON.parse(JSON.stringify(dashboard));
  _pendingRemovedTileIds.clear();
  editMode = true;
  _syncEditModeUI();
}
function exitEditMode() {
  if (!editMode) return;
  editMode = false;
  _editDashboardSnapshot = null;
  _pendingRemovedTileIds.clear();
  dragSrc = null;
  _syncEditModeUI();
}

async function saveAndExitEditMode(layoutName = '') {
  if (!editMode) return;
  const removedTileIds = [..._pendingRemovedTileIds];
  if (!await saveDashboard(layoutName)) return false;
  exitEditMode();
  for (const id of removedTileIds) {
    fetch(`/api/stop/${encodeURIComponent(id)}`, { method: 'POST' }).catch(() => {});
  }
  return true;
}

function openSaveLayout() {
  _showModal(`
    <h3>Save layout</h3>
    <form id="save-layout-form">
      <div class="form-field">
        <label class="form-label" for="save-layout-name">Layout name</label>
        <input class="form-input" id="save-layout-name" required pattern="[a-zA-Z0-9]+((_|-)[a-zA-Z0-9]+)*" autocomplete="off" title="Letters and numbers separated by hyphens or underscores">
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
        <button type="button" class="btn" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn accent">Save layout</button>
      </div>
    </form>
  `);
  const input = document.getElementById('save-layout-name');
  input.value = dashboard.layout || _userMenuActiveName || 'default';
  input.focus();
  input.select();
  document.getElementById('save-layout-form').onsubmit = async e => {
    e.preventDefault();
    const button = e.target.querySelector('[type="submit"]');
    button.disabled = true;
    if (await saveAndExitEditMode(input.value.trim())) closeModal();
    button.disabled = false;
  };
}

function cancelEditMode() {
  if (!editMode) return;
  dashboard = _editDashboardSnapshot || dashboard;
  closeUserMenu();
  exitEditMode();
  _applyCols();
  renderGrid();
}

async function saveDashboard(layoutName = '') {
  dashboard.layout = _userMenuActiveName || dashboard.layout || 'default';
  try {
    const r = await fetch('/api/dashboard' + (layoutName ? `?template=${encodeURIComponent(layoutName)}` : ''), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(dashboard)
    });
    if (!r.ok) throw new Error('save failed');
    if (layoutName) {
      dashboard.layout = _userMenuActiveName = layoutName;
      localStorage.setItem('dashboardTemplate', layoutName);
      _userMenuLoaded = false;
    }
    toast(layoutName ? 'Dashboard and layout saved' : 'Dashboard saved', 'ok');
    return true;
  } catch (_) {
    toast('Save failed', 'err');
    return false;
  }
}

async function saveConfigValue(target, path, value) {
  try {
    const r = await fetch('/api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target, path, value })
    });
    if (!r.ok) {
      let detail = '';
      try { detail = (await r.json())?.detail || ''; } catch (_) {}
      throw new Error(detail || `HTTP ${r.status}`);
    }
    return true;
  } catch (e) {
    toast(`Setting save failed${e?.message ? `: ${e.message}` : ''}`, 'err');
    return false;
  }
}

async function saveTileConfig(tileId, updates) {
  const tile = dashboard?.tiles?.find(t => t.id === tileId);
  if (!tile) {
    toast('Setting save failed: tile not found', 'err');
    return false;
  }
  const previous = { ...(tile.config || {}) };
  tile.config = { ...previous };
  Object.assign(tile.config, updates);
  const saved = await saveDashboard();
  if (!saved) tile.config = previous;
  return saved;
}

// ── Layout Zoom (cols per row) ──────────────────────────────────────────────
function _applyCols() {
  const cols = dashboard.view?.cols ?? 5;
  const editable = dashboard.view?.editable !== false;
  const adjustableColumns = dashboard.view?.adjustable_columns !== false;
  document.getElementById('user-menu-edit').style.display = editable ? '' : 'none';
  document.getElementById('add-tile-btn').style.display = editable ? '' : 'none';
  const columnControls = document.querySelector('.user-menu-cols');
  columnControls.style.display = adjustableColumns ? '' : 'none';
  document.documentElement.style.setProperty('--cols', cols);
  const el = document.getElementById('user-menu-cols');
  if (el) el.textContent = adjustableColumns ? cols : '—';
  const size = dashboard.view?.size ?? 'small';
  const grid = document.getElementById('tile-grid');
  grid.dataset.size = size;
  grid.style.zoom = {small:1, medium:1.15, big:1.3}[size] ?? 1;
  document.querySelectorAll('#user-menu [data-size]').forEach(b => b.setAttribute('aria-pressed', b.dataset.size === size));
}

async function setDashboardSize(size) {
  enterEditMode();
  if (!dashboard.view) dashboard.view = {};
  const previous = dashboard.view.size;
  dashboard.view.size = size;
  _applyCols();
  if (dashboard.view.editable === false && !await saveDashboard()) {
    dashboard.view.size = previous;
    _applyCols();
  }
}

function zoomIn() {
  if (dashboard.view?.adjustable_columns === false) return;
  const cur = dashboard.view?.cols ?? 5;
  if (cur >= _COLS_MAX) return;
  enterEditMode();
  if (!dashboard.view) dashboard.view = {};
  dashboard.view.cols = cur + 1;
  _applyCols();
}

function zoomOut() {
  if (dashboard.view?.adjustable_columns === false) return;
  const cur = dashboard.view?.cols ?? 5;
  if (cur <= _COLS_MIN) return;
  enterEditMode();
  if (!dashboard.view) dashboard.view = {};
  dashboard.view.cols = cur - 1;
  _applyCols();
}

function renameTileLabel(id) {
  const el   = document.getElementById(`${id}-label`);
  if (!el || el.tagName === 'INPUT') return;
  const tile = dashboard.tiles.find(t => t.id === id);
  if (!tile) return;
  const previous = (tile.config || {}).label || '';
  const input = document.createElement('input');
  input.className   = 'tile-label-input';
  input.style.cssText = 'width:80px;font-size:10px;padding:1px 4px';
  input.value       = previous;
  input.placeholder = 'name…';
  let done = false;
  const finish = async save => {
    if (done) return;
    done = true;
    const v = save ? input.value.trim() : previous;
    if (!tile.config) tile.config = {};
    tile.config.label = v;
    el.textContent = v || '—';
    input.replaceWith(el);
    if (save && !await saveDashboard()) {
      tile.config.label = previous;
      el.textContent = previous || '—';
    }
  };
  input.onmousedown = e => e.stopPropagation();
  input.onclick = e => e.stopPropagation();
  input.onblur = () => finish(true);
  input.onkeydown = e => {
    if (e.key === 'Enter')  input.blur();
    if (e.key === 'Escape') finish(false);
  };
  el.replaceWith(input);
  input.focus(); input.select();
}

function editTileLabel(id) { renameTileLabel(id); }

function removeTile(id) {
  if (!editMode) enterEditMode();
  _pendingRemovedTileIds.add(id);
  dashboard.tiles = dashboard.tiles.filter(t => t.id !== id);
  renderGrid();
}

function _installAddTileModalContent() {
  const tpl = document.getElementById('tpl-add-tile-modal');
  const modal = document.getElementById('modal');
  if (tpl && modal) modal.innerHTML = tpl.innerHTML;
}

function openAddTile() {
  if (dashboard.view?.editable === false) return;
  enterEditMode();
  _installAddTileModalContent();
  const modal = document.getElementById('modal');
  if (modal) modal.dataset.locked = 'false';
  document.getElementById('modal-backdrop').style.display = 'flex';
}
function closeModal() {
  if (document.getElementById('modal')?.dataset.locked === 'true') return;
  document.getElementById('modal-backdrop').style.display = 'none';
}
function addTile() {
  const type = document.getElementById('new-tile-type').value;
  const w    = parseInt(document.getElementById('new-tile-w').value);
  const h    = parseInt(document.getElementById('new-tile-h').value);
  dashboard.tiles.push({ id: type + '_' + Date.now(), type, w, h });
  renderGrid();
  closeModal();
}

// ── API Helpers ─────────────────────────────────────────────────────────────
async function runOp(args) {
  try {
    const r = await fetch('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args })
    });
    return await r.json();
  } catch(e) {
    return { stdout: '', stderr: String(e), ok: false };
  }
}

async function startProc(procId, args, opts = {}) {
  return fetch(`/api/start/${encodeURIComponent(procId)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ args, ...opts })
  });
}

async function stopProc(procId) {
  return fetch(`/api/stop/${encodeURIComponent(procId)}`, { method: 'POST' });
}

async function sendProcInput(procId, text) {
  return fetch(`/api/proc/${encodeURIComponent(procId)}/stdin`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });
}

// ── UI Helpers ──────────────────────────────────────────────────────────────
function setDot(id, color) {
  const dot = document.getElementById(`${id}-dot`);
  if (!dot) return;
  dot.style.background  = color === 'green' ? 'var(--green)'  :
                          color === 'amber' ? 'var(--amber)'  :
                          color === 'red'   ? 'var(--red)'    :
                          color === 'blue'  ? 'var(--accent)' :
                          color === 'gray'  ? 'var(--text3)'  : '';
  dot.style.boxShadow   = (color && color !== 'gray') ? `0 0 6px ${dot.style.background}` : '';
}

function toast(msg, type = '') {
  const c  = document.getElementById('toast');
  const el = document.createElement('div');
  el.className   = 'toast-item' + (type ? ' ' + type : '');
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// append a line to a scrollable log container
function appendLog(containerId, text, cls = '') {
  const el = document.getElementById(containerId);
  if (!el) return;
  const row = document.createElement('div');
  row.className   = 'log-entry' + (cls ? ' ' + cls : '');
  row.textContent = text;
  el.appendChild(row);
  while (el.children.length > 80) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

// standard tile header HTML
function _tileHeader(id, label, actions = '') {
  return `<div class="tile-header">
    <div class="tile-label" data-tv-tile-title><span class="status-dot" id="${id}-dot"></span>${label}</div>
    <div class="tile-actions">${actions}<button class="icon-btn tile-remove" onclick="removeTile('${id}')">✕</button></div>
  </div>`;
}

// ── Event Handlers (app-level) ─────────────────────────────────────────────
function _registerAllHandlers() {
  dashboard.tiles.forEach(tile => {
    const def = TILES[tile.type];
    if (def?.events) def.events(tile.id, tile);
  });
}

function _registerWebUserHandlers() {
  onEvent('web.user', evt => {
    if (evt.event === 'revoked' && _currentUser?.name === evt.name) {
      setTimeout(() => location.reload(), 2000);
    }
  });
}

// ── User Context UI ─────────────────────────────────────────────────────────
function _renderUserChip(user) {
  if (!user) return;
  const chip = document.getElementById('user-chip');
  const av   = document.getElementById('user-chip-avatar');
  const nm   = document.getElementById('user-chip-name');
  if (av) av.textContent = user.avatar || '🙂';
  if (nm) nm.textContent = user.name   || '—';
  if (chip) chip.classList.add('visible');
  document.getElementById('user-actions')?.classList.add('visible');
  _syncShareButtonVisibility?.(user);
}

let _userMenuOpen = false;
let _userMenuLoaded = false;
let _userMenuTemplates = [];
let _userMenuActiveName = null;

function toggleUserMenu(e) {
  e?.stopPropagation();
  if (_userMenuOpen) closeUserMenu();
  else openUserMenu();
}
document.getElementById('user-menu')?.addEventListener('click', e => e.stopPropagation());
async function openUserMenu() {
  const menu = document.getElementById('user-menu');
  const chip = document.getElementById('user-chip');
  if (!menu) return;
  if (!_userMenuLoaded) await _loadTemplates();
  _renderTemplates();
  _applyCols();
  menu.classList.add('open');
  chip.classList.add('open');
  _userMenuOpen = true;
  setTimeout(() => document.addEventListener('click', _userMenuOutside, { once: true }), 0);
}
function closeUserMenu() {
  const menu = document.getElementById('user-menu');
  const chip = document.getElementById('user-chip');
  if (menu) menu.classList.remove('open');
  if (chip) chip.classList.remove('open');
  _userMenuOpen = false;
}
function _userMenuOutside(e) {
  const menu = document.getElementById('user-menu');
  const chip = document.getElementById('user-chip');
  if (menu && menu.contains(e.target)) {
    setTimeout(() => document.addEventListener('click', _userMenuOutside, { once: true }), 0);
    return;
  }
  if (chip && chip.contains(e.target)) return;
  closeUserMenu();
}
async function _loadTemplates() {
  try {
    const r = await fetch('/api/dashboard-templates');
    const d = await r.json();
    _userMenuTemplates = d.templates || [];
    const order = {media:0, default:1, browse:2};
    _userMenuTemplates.sort((a, b) => (order[a.name] ?? 3) - (order[b.name] ?? 3));
  } catch (_) { _userMenuTemplates = []; }
  const saved = dashboard.layout || localStorage.getItem('dashboardTemplate');
  _userMenuActiveName = (_userMenuTemplates.some(t => t.name === saved) && saved)
                       || (_userMenuTemplates.find(t => t.name === 'default') ? 'default' : null)
                       || (_userMenuTemplates[0]?.name || null);
  _userMenuLoaded = true;
}
function _renderTemplates() {
  const el = document.getElementById('user-menu-templates');
  if (!el) return;
  if (!_userMenuTemplates.length) {
    el.innerHTML = `<div class="user-menu-tpl" style="color:var(--text3);cursor:default">no templates</div>`;
    return;
  }
  el.innerHTML = _userMenuTemplates.map(t => `
    <button class="user-menu-tpl ${t.name === _userMenuActiveName ? 'active' : ''}" onclick="applyTemplate('${t.name}')">
      <span class="dot"></span>
      <span class="tname">${t.name === 'media' ? 'Media' : t.name === 'default' ? 'System' : t.name === 'browse' ? 'Browse' : t.name}</span>
    </button>`).join('');
}
async function applyTemplate(name) {
  closeUserMenu();
  try {
    const r = await fetch('/api/dashboard-template/apply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, from_layout:dashboard.layout || _userMenuActiveName || 'default', size:dashboard.view?.size ?? 'small', cols:dashboard.view?.cols ?? 5 })
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'apply failed');
    dashboard = await (await fetch('/api/dashboard')).json();
    dashboard.layout = name;
    exitEditMode();
    _applyCols();
    renderGrid();
    _userMenuActiveName = name;
    localStorage.setItem('dashboardTemplate', name);
    toast(`Layout: ${name}`, 'ok');
  } catch (e) { toast(`Apply failed: ${e.message || e}`, 'err'); }
}

async function _refreshSources() {
  try {
    const r = await fetch('/api/cameras');
    const d = await r.json();
    _sourcesCamera = d.cameras || {};
    _sourcesAudio  = d.audio_devices || {};
    _sourcesPushFps = d.push_fps || 2;
  } catch (_) {
    _sourcesCamera = {};
    _sourcesAudio  = {};
    _sourcesPushFps = 2;
  }
}
function _getCameraSources() { return _sourcesCamera; }
function _getAudioSources()  { return _sourcesAudio; }
function _getPushFps()       { return _sourcesPushFps; }

async function _getMyIp() {
  if (_myIpCache) return _myIpCache;
  try {
    const r = await fetch('/api/whoami');
    if (r.ok) {
      const data = await r.json();
      const ip = String(data?.ip || '').trim();
      if (ip && ip !== 'unknown') {
        _myIpCache = ip;
        return _myIpCache;
      }
    }
  } catch (_) {}
  _myIpCache = '';
  return '';
}

function _registerWebLifecycleHandlers() {
  onEvent('web.connected', async () => {
    try {
      await _postUserDevices();
      await _refreshSources();
    } catch (_) {}
  });

  onEvent('web.dashboard-created', async () => {
    try {
      const d = await fetch('/api/dashboard').then(r => r.json());
      dashboard = d;
      _applyCols();
      renderGrid();
      await _refreshSources();
    } catch (_) {}
  });
}

async function _postUserDevices() {
  if (_devicesContributed) return;
  if (!localStorage.getItem('camera_cert_ok') && location.hostname !== 'localhost') _showCertTipSoft();

  // discovery delegated to camera.js, audio.js, and device.js
  const [{ cameras }, { mics, speakers }, { devices }] = await Promise.all([
    discoverCameraDevices(),
    discoverAudioDevices(),
    discoverDeviceDevices(),
  ]);

  const r = await fetch('/api/users/devices', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cameras, mic: mics, speaker: speakers, devices })
  });
  if (r.ok) { _devicesContributed = true; }
}

// ── Cert tip (device onboarding, non-blocking) ────────────────────────────
function _showCertTipSoft() {
  // runs after init() has started — use a short delay so dashboard renders first
  setTimeout(() => {
    _showModal(`
      <h3>📷 Device camera tip</h3>
      <div style="font-size:13px;color:var(--text2);line-height:1.7">
        To use <strong style="color:var(--text)">this device's camera</strong> in the dashboard,
        your browser needs to trust this site's certificate.<br><br>
        If you see a security warning, tap
        <strong style="color:var(--text)">Advanced → Proceed</strong>.
        This only happens once per browser.
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn" onclick="localStorage.setItem('camera_cert_ok','1');closeModal()">skip</button>
        <button class="btn accent" onclick="localStorage.setItem('camera_cert_ok','1');closeModal()">got it</button>
      </div>
    `);
  }, 800);
}

// ── Boot ───────────────────────────────────────────────────────────────────
// TILES registry populated by *.js.
// init() is called from the HTML after both scripts have loaded.
const TILES = {
  _unknown: {
    html(tile) {
      return _tileHeader(tile.id, tile.type) +
        `<div class="tile-body"><div class="muted">unknown type: ${tile.type}</div></div>`;
    },
    bind() {},
    events() {}
  }
};
