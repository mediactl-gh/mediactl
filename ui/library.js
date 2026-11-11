// web/library.js

/* ── Injected CSS ──────────────────────────────────────────────────────────── */
(function() {
  const s = document.createElement('style');
  s.textContent = `
    .lib-active-box {
      overflow:hidden; flex-shrink:0; padding:8px 10px; margin:4px 0 10px;
      background:rgba(79,142,247,0.05); border:1px solid rgba(79,142,247,0.18); border-radius:8px;
    }
    #mlib-rule-wrap .media-search-input {
      background:var(--surface2); border:1px solid var(--border2);
    }
    #mlib-rule-wrap .media-search-input:focus {
      border-color:rgba(79,142,247,0.5);
    }
    .lib-recent-card { display:flex;gap:8px;min-width:0;padding-top:6px; }
    .lib-recent-card img,.lib-recent-poster {
      width:28px;height:42px;object-fit:cover;border-radius:3px;flex-shrink:0;background:var(--surface2);
    }
    .lib-active-box.compact .lib-summary-items {
      display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;
    }
    .lib-active-box.compact .lib-recent-card { gap:6px; }
    .lib-active-box.compact .lib-recent-card img,
    .lib-active-box.compact .lib-recent-poster { width:24px;height:36px; }
    .mlib-row { display:flex;align-items:center;gap:8px;min-width:0; }
    .mlib-row > label { flex:0 0 60px;font-size:12px;color:var(--text3); }
    .mlib-row .media-search-input { min-width:0; }
    .mlib-form .media-search-input { height:20px;padding:3px 8px;box-sizing:border-box; }
    .mlib-form .media-chip { min-height:20px;box-sizing:border-box;display:inline-flex;align-items:center; }
    .mlib-form .media-icon-btn { height:20px;font-size:12px; }
    #modal:has(.mlib-form) { height:460px;max-width:calc(100vw - 32px); }
    .mlib-form { flex:1;min-height:0; }
    #mlib-rule-wrap { flex:1;min-height:0;overflow:auto;border-top:1px solid var(--surface2);padding-top:12px;margin-top:4px; }
    .mlib-source-panel:not([hidden]) { display:flex;flex-direction:column;gap:10px; }
    #mlib-role-description { margin-left:68px;font-size:12px;color:var(--text3); }
    #mlib-type { margin:0 0 0 auto;flex-shrink:0;flex-wrap:nowrap; }
    #mlib-rule-genres { position:relative;min-width:0;flex:1; }
    #mlib-genre-options { display:flex;flex-wrap:wrap;gap:4px;align-items:flex-start; }
    .mlib-more > summary { list-style:none; }
    .mlib-more > summary::-webkit-details-marker { display:none; }
    .mlib-genre-overflow { position:absolute;right:0;top:100%;z-index:1;padding:8px;margin-top:4px;
      background:var(--surface);border:1px solid var(--border2);border-radius:6px;
      box-shadow:0 6px 18px #0004;display:flex;flex-wrap:wrap;gap:4px;max-height:160px;overflow:auto; }
  `;
  document.head.appendChild(s);
})();

const _libraryState = {};
function _ls(id) {
  if (!_libraryState[id]) _libraryState[id] = { libraries: null, active: null, cfg: null, _visible: false, mode: 'libraries', browseLibrary: null, browseEntries: [], browsePath: null, browseTimer: null, browseOffset: 0, browseTotal: 0, browseLoading: false, browseDetail: null, browseView: 'list' };
  return _libraryState[id];
}
const _LIB_BROWSE_INACTIVITY_MS = 60000;

TILES['library'] = {
  html(tile) {
    const id = tile.id;
    return `
    ${_tileHeader(id, `LIBRARY`,
      `<button class="icon-btn" onclick="libraryModal('${id}')" title="Add library" aria-label="Add library"><i data-lucide="folder-plus" style="width:12px;height:12px"></i></button>
       <button class="icon-btn" onclick="libraryModal('${id}', null, 'collection')" title="Add collection" aria-label="Add collection">
         <i data-lucide="list-plus" style="width:12px;height:12px"></i>
       </button>
       <button class="icon-btn" onclick="libraryOpenSettings('${id}')" title="Settings" aria-label="Library settings">⚙</button>`)}
    <div style="display:flex;flex-direction:column;height:calc(100% - 38px);overflow:hidden">
      <div id="${id}-view" style="flex:1;display:flex;flex-direction:column;padding:6px 14px 14px;min-height:0;overflow-x:hidden;overflow-y:auto"></div>
    </div>`;
  },
  bind(id) {
    _libraryState[id] = { libraries: null, active: null, cfg: null, _visible: true, mode: 'libraries', browseLibrary: null, browseEntries: [], browsePath: null, browseTimer: null, browseOffset: 0, browseTotal: 0, browseLoading: false, browseDetail: null, browseView: 'list' };
    libraryShow(id);
  },
  events(id) { libraryBindEvents(id); }
};

let _librarySettingsPop = null;
let _librarySettings = {};

function _libShowSection(key) {
  return _librarySettings[key] !== false;
}

function _librarySettingsHTML() {
  const fields = [
    ['show_active_downloads', 'Show active downloads'],
    ['show_recently_downloaded', 'Show recently downloaded'],
  ];
  return `<div class="dp-meta" style="padding:4px 10px 3px;text-transform:uppercase;letter-spacing:0.08em;font-size:9px;font-weight:700;color:var(--accent);opacity:1">display</div>`
    + fields.map(([key, label]) => `<div class="dp-row"><label class="dp-row-pick" style="display:flex;align-items:center;cursor:pointer">
      <span class="dp-row-dim">${label}</span>
      <input type="checkbox" style="margin-left:auto" ${_libShowSection(key) ? 'checked' : ''}
        onchange="libraryToggleSection('${key}',this)">
    </label></div>`).join('')
    + `<div class="dp-meta" style="padding:8px 10px 3px;text-transform:uppercase;letter-spacing:0.08em;font-size:9px;font-weight:700;color:var(--accent);opacity:1;border-top:1px solid var(--border2);margin-top:4px">refresh</div>`
    + ['refresh_interval', 'lists_refresh_interval', 'meta_refresh_interval']
      .map(key => _mediaRowHTML('library', _librarySettings, key, key)).join('');
}

function libraryOpenSettings(id) {
  if (_librarySettingsPop) { _librarySettingsPop.closePopover(); return; }
  if (typeof closeDevicePicker === 'function') closeDevicePicker();
  const tileEl = tileRefs[id] || document.getElementById(`${id}-view`);
  if (!tileEl) return;
  _librarySettingsPop = _mediaPopover(tileEl, _librarySettingsHTML(), {
    top: tileEl.getBoundingClientRect().top + 44,
    onClose: () => { _librarySettingsPop = null; },
  });
}

async function libraryToggleSection(key, input) {
  input.disabled = true;
  const value = input.checked;
  if (await saveConfigValue('library', key, value)) {
    _librarySettings[key] = value;
    Object.keys(_libraryState).forEach(id => libraryRender(id));
  }
  input.checked = _libShowSection(key);
  input.disabled = false;
}

async function libraryShow(id) {
  const st = _ls(id);
  if (!st._visible) st._visible = true;
  await _libEnsureCfg();
  await _libLoadFromConfig(id);
  libraryRender(id);
  libraryRefreshActive(id);
  _libStartPoll(id);
}

function libraryHide(id) {
  const st = _ls(id);
  if (st) st._visible = false;
  _libStopPoll(id);
}

function libraryBindEvents(id) {
  // global handlers are registered once below; per-tile binding is a no-op
}

(function _libRegisterGlobalHandlers() {
  onEvent('torrent.status', evt => {
    let items;
    try { items = JSON.parse(evt.items || '[]'); } catch (_) { return; }
    Object.keys(_libraryState).forEach(id => {
      const st = _ls(id);
      if (!st?._visible) return;
      st.active = items;
      if (st.renderActive) st.renderActive(items);
      if (st.mode === 'libraries') libraryRender(id);
    });
  });
  onEvent('library.sync', evt => { if (evt.hint === 'library.sync') _libRefreshLibraryAll(evt); });
  onEvent('library.sync-lists', _libRefreshLibraryAll);
  onEvent('library.sync-meta', evt => { if (evt.event === 'done' && Number(evt.fetched) > 0) _libRefreshLibraryAll(); });
  onEvent('library.default-lists', () => _libRefreshLibraryAll());
  onEvent('library.import', _libRefreshLibraryAll);
  onEvent('media.fetch', _libRefreshLibraryAll);
  onEvent('media.info',         evt => _libOnMediaInfo(evt));
  onEvent('library.refresh', _libRefreshLibraryAll);
  onEvent('web.connected', async () => { _libRefreshLibraryAll(); _libRefreshAllActive(); });
})();

function _libRefreshAllActive() {
  Object.keys(_libraryState).forEach(id => libraryRefreshActive(id));
}

function _libRefreshLibraryAll(evt) {
  Object.keys(_libraryState).forEach(id => _libRefreshLibrary(id, evt));
}
async function _libRefreshLibrary(id, evt) {
  const st = _ls(id);
  if (!st?._visible) return;
  if (!document.getElementById(`${id}-view`)) {
    _libStopPoll(id);
    delete _libraryState[id];
    return;
  }
  if (st.refreshView) return st.refreshView(evt);
  if (evt?.hint === 'library.sync' && st.mode === 'browse' && st.browseLibrary === evt.library)
    return _libBrowseFetch(id, false);
  await _libLoadFromConfig(id);
  if (st.mode === 'libraries') libraryRender(id);
}

let _libCfgCache = null;
async function _libEnsureCfg() {
  if (_libCfgCache) return _libCfgCache;
  try { _libCfgCache = await (await fetch('/api/config?target=media')).json(); }
  catch(e) { _libCfgCache = {}; }
  return _libCfgCache;
}
function _libCfgRefreshSec() {
  const v = parseInt(_libCfgCache?.torrent?.refresh_rate, 10);
  return (Number.isFinite(v) && v >= 2 && v <= 300) ? v : 30;
}

function _libBrowseResetTimer(id) {
  const st = _ls(id);
  if (st.browseTimer) clearTimeout(st.browseTimer);
  if (st.renderView) return;
  if (st.view === 'pick') return;
  st.browseTimer = setTimeout(() => libraryBack(id), _LIB_BROWSE_INACTIVITY_MS);
}
function _libBrowseBindIdle(id) {
  const el = document.getElementById(`${id}-view`);
  if (!el || el.dataset.idleBound) return;
  el.dataset.idleBound = '1';
  const reset = () => _libBrowseResetTimer(id);
  el.addEventListener('mousedown', reset);
  el.addEventListener('scroll', reset);
  el.addEventListener('touchstart', reset);
}

async function libraryBrowse(id, name) {
  const st = _ls(id);
  const lib = (st.libraries || []).find(l => l.name === name);
  if (!lib) return;
  st.mode = 'browse';
  st.browseLibrary = name;
  st.browsePath = lib.path;
  st.browseEntries = [];
  st.browseError = null;
  st.browseOffset = 0;
  st.browseTotal = 0;
  st.browseLoading = false;
  st.browseDetail = null;
  st.browseView = 'list';
  _libBrowseResetTimer(id);
  _libBrowseStartPoll(id);
  libraryRender(id);
  _libBrowseBindIdle(id);
  await _libBrowseFetch(id);
}

const _LIB_PAGE_SIZE = 25;

async function _libBrowseFetch(id, append) {
  const st = _ls(id);
  if (st.mode !== 'browse' || !st.browsePath) return;
  if (st.browseLoading) return;
  st.browseLoading = true;
  const offset = append ? st.browseEntries.length : 0;
  const lib = (st.libraries || []).find(item => item.name === st.browseLibrary);
  const files = lib?.role === 'download' ? '&files=true' : '';
  try {
    const r = await fetch(`/api/media/browse?path=${encodeURIComponent(st.browsePath)}&offset=${offset}&limit=${_LIB_PAGE_SIZE}${files}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const newItems = data.items || [];
    st.browseTotal = data.total || (st.browseEntries.length + newItems.length);
    st.browseError = null;
    if (append && newItems.length) {
      st.browseEntries = st.browseEntries.concat(newItems);
      st.browseLoading = false;
      libraryRender(id);
    } else {
      st.browseEntries = append ? st.browseEntries : newItems;
      st.browseLoading = false;
      libraryRender(id);
    }
  } catch (e) {
    st.browseError = e.message;
    if (!append) {
      st.browseLoading = false;
      libraryRender(id);
    }
  }
  st.browseLoading = false;
  _libBrowseResetTimer(id);
}

function _libBrowseStartPoll(id) {
  const st = _ls(id);
  if (st.browsePoll) clearInterval(st.browsePoll);
  st.browsePoll = setInterval(() => { if (!st.browseDetail) _libBrowseFetch(id, false); }, 15000);
}

function libraryBack(id) {
  const st = _ls(id);
  st.mode = 'libraries';
  st.browseLibrary = null;
  st.browseEntries = [];
  st.browsePath = null;
  st.browseError = null;
  st.browseOffset = 0;
  st.browseTotal = 0;
  st.browseLoading = false;
  st.browseDetail = null;
  if (st.browseTimer) { clearTimeout(st.browseTimer); st.browseTimer = null; }
  if (st.browsePoll) { clearInterval(st.browsePoll); st.browsePoll = null; }
  if (st._observer) { st._observer.disconnect(); st._observer = null; }
  const el = document.getElementById(`${id}-view`);
  if (el) { delete el.dataset.idleBound; }
  libraryRender(id);
}

function _libRuleStr(rule) {
  if (!rule) return '*';
  const parts = [];
  if (rule.genres?.length) parts.push(rule.genres.join(','));
  if (rule.quality) parts.push(`quality=${rule.quality}`);
  if (rule.min_rating) parts.push(`rating>=${rule.min_rating}`);
  if (rule.year_from) parts.push(`year>=${rule.year_from}`);
  if (rule.year_to) parts.push(`year<=${rule.year_to}`);
  if (rule.language) parts.push(`lang=${rule.language}`);
  return parts.length ? parts.join(', ') : '*';
}

async function _libLoadFromConfig(id) {
  try {
    const [data, settings] = await Promise.all([
      fetch('/api/library?enrich=true').then(r => {
        if (!r.ok) throw new Error('Could not load libraries');
        return r.json();
      }),
      fetch('/api/config?target=library').then(r => {
        if (!r.ok) throw new Error('Could not load library settings');
        return r.json();
      }).catch(() => null),
    ]);
    if (!Array.isArray(data)) throw new Error('Invalid library response');
    if (settings) _librarySettings = settings;
    _ls(id).libraries = data;
    _ls(id).loadError = false;
  } catch(e) {
    _ls(id).loadError = true;
  }
}

function _libFmtSize(n) {
  for (const u of ['B','KB','MB','GB','TB']) { if (n < 1024) return `${n < 10 ? n.toFixed(1) : Math.round(n)} ${u}`; n /= 1024; }
  return `${n.toFixed(1)} PB`;
}

const _LIB_TYPE_ICON = { movie: 'film', tv: 'tv', artist: 'music' };
const _LIB_ROLE_ICON = { collection: 'list', download: 'download' };
function _libTypeIcon(type, role) {
  let icon;
  if (role && role !== 'library') icon = _LIB_ROLE_ICON[role] || 'folder';
  else icon = _LIB_TYPE_ICON[type] || 'folder';
  return `<i data-lucide="${icon}" style="width:12px;height:12px;flex-shrink:0"></i>`;
}

function libraryRender(id) {
  const st = _ls(id);
  if (!st._visible) return;
  if (st.browseDetail !== st._torrentEntry) { st.view = null; clearTimeout(st._retT); }
  if (st.renderView) { st.renderView(); return; }
  if (st.view === 'pick') { _mediaRenderPick(id); return; }
  const el = document.getElementById(`${id}-view`); if (!el) return;
  const libs = st.libraries || [];
  const isRecent = info => (info.name || '').toLowerCase() === 'recently added';
  const recent = libs.find(isRecent);
  const regularLibs = libs.filter(info => !isRecent(info));
  const activeLoaded = Array.isArray(st.active);
  const active = st.active || [];
  const activeShown = active.slice(0, 2);
  const showActive = _libShowSection('show_active_downloads');
  const showRecent = _libShowSection('show_recently_downloaded');
  const compact = el.clientHeight < 520 && ((showActive && activeShown.length > 1) || (showRecent && (recent?.preview || []).length > 1));

  const savedScrollTop = st.mode === 'browse' && !st.browseDetail
    ? (document.getElementById(`${id}-browse-items`)?.scrollTop ?? 0)
    : 0;
  const detailScrollTop = document.getElementById(`${id}-browse-detail`)?.scrollTop ?? 0;

  const activeHTML = `
    <div class="lib-active-box${compact ? ' compact' : ''}" style="cursor:pointer" onclick="_libGoDownload('${id}')">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:${activeShown.length ? '6px' : '0'}">
        <span style="font-weight:700;font-size:12px">Active downloads</span>
        <span style="font-size:9px;color:var(--text3);font-family:var(--mono);margin-left:auto">${activeLoaded ? active.length : '…'}</span>
      </div>
      <div class="lib-summary-items">${activeShown.length ? activeShown.map(t => `
        <div style="overflow:hidden">
          ${_libActiveRowHTML(t)}
        </div>`).join('') : `<div style="font-size:10px;color:var(--text3);font-family:var(--mono)">${activeLoaded ? 'none' : 'loading…'}</div>`}</div>
      ${active.length > activeShown.length
        ? `<div style="font-size:9px;color:var(--text3);font-family:var(--mono);padding-top:5px">+${active.length - activeShown.length} more</div>` : ''}
    </div>`;

  const recentItems = (recent?.preview || []).slice(0, compact ? 2 : 3);
  const recentHTML = recent ? `
    <div class="lib-active-box${compact ? ' compact' : ''}" style="cursor:pointer" onclick="libraryBrowse('${id}', '${(recent.name || '').replace(/'/g, "\\'")}')">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-weight:700;font-size:12px">${recent.name}</span>
        <span style="font-size:9px;color:var(--text3);font-family:var(--mono);margin-left:auto">${recent.items || 0}</span>
      </div>
      <div class="lib-summary-items">${recentItems.length
        ? recentItems.map(item => {
            const label = String(item.title || item.name || '').replace(/\s*-\s*\d{4}$/, '')
              .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            const stats = item.size ? _libFmtSize(item.size) : 'downloaded';
            return `<div class="lib-recent-card">
              ${item.poster ? `<img src="${item.poster}" loading="lazy">` : '<div class="lib-recent-poster"></div>'}
              <div style="min-width:0;align-self:center">
                <div style="font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${label}</div>
                <div style="font-size:9px;color:var(--text3);font-family:var(--mono);margin-top:3px">${stats}</div>
              </div>
            </div>`;
          }).join('')
        : '<div style="font-size:10px;color:var(--text3);font-family:var(--mono)">none</div>'}</div>
    </div>` : '';
  const errorHTML = st.loadError ? '<div class="muted" role="alert">Could not load libraries. Retrying…</div>' : '';
  const summaryHTML = errorHTML + (st.mode === 'browse' ? '' : (showActive ? activeHTML : '') + (showRecent ? recentHTML : ''));

  if (!regularLibs.length && !recent) {
    el.innerHTML = summaryHTML + (st.loadError ? '' : `<div class="muted" style="margin-top:16px">${st.libraries ? 'no libraries configured' : 'loading libraries…'}</div>`);
    return;
  }
  if (st.mode === 'browse') {
    if (st.browseDetail) {
      el.innerHTML = summaryHTML + _libBrowseDetailHTML(id);
      const detail = document.getElementById(`${id}-browse-detail`);
      if (detail) detail.scrollTop = detailScrollTop;
    } else {
      el.innerHTML = summaryHTML + _libBrowseHeaderHTML(id) + `<div id="${id}-browse-items" style="flex:1;overflow-y:auto;min-height:0">${_libBrowseItemsHTML(id)}</div>`;
      _libObserveSentinel(id);
      if (savedScrollTop > 0) {
        const nb = document.getElementById(`${id}-browse-items`);
        if (nb) nb.scrollTop = savedScrollTop;
      }
    }
    if (typeof lucide !== 'undefined') lucide.createIcons();
    return;
  }
  el.innerHTML = summaryHTML + regularLibs.map(info => {
    const name = info.name || '?';
    const safeName = name.replace(/'/g, "\\'");
    const syncCls = _libSyncColor(info);
    const syncTitle = _libSyncTitle(info);
    return `
    <div class="media-row" style="flex-direction:column;align-items:flex-start;gap:2px;padding:6px 0;cursor:pointer" onclick="libraryBrowse('${id}', '${safeName}')">
      <div style="display:flex;align-items:center;gap:8px;width:100%">
        <span class="status-dot${syncCls}" title="${syncTitle}"></span>
        ${_libTypeIcon(info.type, info.role)}
        <span style="font-weight:700;font-size:12px">${name}</span>
        <span style="margin-left:auto;font-size:10px;color:var(--text3);font-family:var(--mono)">${info.items ?? 0}</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;width:100%">
        <span style="font-size:10px;color:var(--text3);font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1">${info.path}</span>
        <button class="media-icon-btn" onclick="event.stopPropagation();libraryModal('${id}', '${safeName}')" title="Edit"><i data-lucide="pencil" style="width:12px;height:12px"></i></button>
      </div>
    </div>`;
  }).join('');
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function _libBrowseHeaderHTML(id) {
  const st = _ls(id);
  const name = st.browseLibrary || '?';
  return `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-shrink:0">
      <button class="media-icon-btn" onclick="libraryBack('${id}')" title="back">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
      </button>
      <span style="font-weight:700;font-size:12px;cursor:pointer" onclick="libraryBack('${id}')">${name}</span>
      <div style="margin-left:auto;display:flex;align-items:center;gap:4px">
        <button class="media-icon-btn" onclick="librarySyncBrowse('${id}',undefined,this)" data-sync-until="${st.syncUntil?.[name] || 0}" ${Date.now() < (st.syncUntil?.[name] || 0) ? 'disabled' : ''} title="Sync library"><i data-lucide="refresh-cw" style="width:12px;height:12px"></i></button>
        <button class="media-icon-btn" style="${st.browseView === 'short' ? 'color:var(--accent)' : ''}" onclick="libraryToggleView('${id}')" title="${st.browseView === 'short' ? 'List view' : 'Short view'}">
          ${st.browseView === 'short'
            ? `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/></svg>`
            : `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>`}
        </button>
        <span style="font-size:10px;color:var(--text3);font-family:var(--mono)">${st.browseTotal || ''}</span>
      </div>
    </div>`;
}

function librarySyncBrowse(id, name = _ls(id).browseLibrary, button) {
  const st = _ls(id);
  st.syncUntil ??= Object.create(null);
  if (!name || Date.now() < (st.syncUntil[name] || 0)) return;
  st.syncUntil[name] = Date.now() + 5000;
  if (button) {
    button.disabled = true;
    button.dataset.syncUntil = st.syncUntil[name];
    setTimeout(() => tileRefs[id]?.querySelectorAll('[data-sync-until]').forEach(btn => {
      if (Number(btn.dataset.syncUntil) <= Date.now()) btn.disabled = false;
    }), 5000);
  }
  runOp(['library', 'sync', name, '--refresh']).then(result => {
    if (!result.ok) toast(result.stderr || 'Library sync failed', 'err');
  });
}

function libraryToggleView(id) {
  const st = _ls(id);
  st.browseView = st.browseView === 'short' ? 'list' : 'short';
  libraryRender(id);
}

function _libBrowseRowHTML(e, id, idx) {
  const info = e.info || {};
  const rawName = e.name || '';
  const safeName = rawName.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const displayTitle = (info.title || safeName.replace(/\s*-\s*\d{4}$/, ''))
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const year = info.year;
  const rating = info.rating;
  const genres = (info.genres || []).slice(0, 2).join(', ');
  const poster = info.poster;
  const duration = info.runtime ? `${info.runtime}m` : '';
  const meta = [genres, duration].filter(Boolean).join(' · ');
  return `
    <div class="media-row" style="display:flex;gap:10px;padding:6px 0;align-items:flex-start;cursor:pointer" onclick="libraryBrowseDetail('${id}',${idx})">
      ${poster
        ? `<img src="${poster}" loading="lazy" style="width:40px;height:60px;object-fit:cover;border-radius:4px;flex-shrink:0;background:var(--surface2)">`
        : `<div style="width:40px;height:60px;border-radius:4px;flex-shrink:0;background:var(--surface2)"></div>`}
      <div style="flex:1;min-width:0;display:flex;flex-direction:column">
        <div style="font-weight:600;font-size:12px;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${displayTitle}</div>
        ${year ? `<div style="font-size:10px;color:var(--text3);font-family:var(--mono);margin-top:1px">${year}</div>` : ''}
        ${meta ? `<div style="display:flex;align-items:flex-start;gap:6px;font-size:10px;color:var(--text3);margin-top:3px">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${meta}</span>
          ${rating ? `<span style="color:var(--amber);font-family:var(--mono);flex-shrink:0">★ ${rating}</span>` : ''}
        </div>` : ''}
      </div>
    </div>`;
}

async function libraryBrowseDetail(id, idx) {
  const st = _ls(id);
  st.browseDetail = st.browseEntries[idx] || null;
  _libBrowseResetTimer(id);
  libraryRender(id);
  const entry = st.browseDetail; const info = entry?.info || {};
  const lib = (st.libraries || []).find(l => l.name === st.browseLibrary);
  const type = info.type || (lib?.role === 'collection' ? 'both' : (lib?.type || 'both'));
  if (!info.title || !info.poster || (type === 'tv' && !info.seasons_complete)) {
    const provider = (await _mediaEnsureCfg()).media?.info?.source || 'tmdb';
    if (st.browseDetail !== entry) return;
    const refId = info[`${provider}_id`];
    const query = refId ? String(refId) : (entry?.name || info.title || '');
    if (query) runOp(['media', 'info', query, '--refresh', '--source', provider,
      '--type', type === 'tv' ? 'tv' : type === 'movie' ? 'movie' : 'both']);
  }
}

function _libBrowseShortHTML(e, id, idx) {
  const info = e.info || {};
  const title = String(info.title || e.name || '').replace(/\s*-\s*\d{4}$/, '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return `<div class="lib-recent-card" style="cursor:pointer;padding:4px;min-width:0" onclick="libraryBrowseDetail('${id}',${idx})">
    ${info.poster ? `<img src="${info.poster}" loading="lazy">` : '<div class="lib-recent-poster"></div>'}
    <div style="min-width:0;align-self:center">
      <div style="font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${title}</div>
      <div style="font-size:9px;color:var(--text3);font-family:var(--mono);margin-top:3px">${info.year || ''}${info.rating ? ` · ★ ${info.rating}` : ''}</div>
    </div>
  </div>`;
}

async function _libOnMediaInfo(evt) {
  for (const id of Object.keys(_libraryState)) {
    const st = _ls(id); const entry = st.browseDetail;
    if (!entry) continue;
    const info = entry.info || {};
    const provider = evt.provider || 'tmdb';
    const sameId = (info.provider || 'tmdb') === provider
      && info[`${provider}_id`] && String(info[`${provider}_id`]) === String(evt.id);
    const sameTitle = _mediaKey(info.title || entry.name) === _mediaKey(evt.title);
    if (!sameId && !sameTitle) continue;
    try {
      const params = new URLSearchParams({provider, id:String(evt.id), type:evt.type || '',
        title:evt.title || '', year:evt.year || ''});
      const response = await fetch(`/api/media/info?${params}`);
      if (response.ok) entry.info = await response.json();
      else entry.info = {...info, title:evt.title, year:evt.year, type:evt.type,
        rating:parseFloat(evt.rating)||0, seasons:parseInt(evt.seasons)||0};
      libraryRender(id);
    } catch (_) {}
  }
}

function _mediaKey(value) {
  return String(value || '').toLowerCase().replace(/\s+-\s+(?:19|20)\d{2}$/, '').replace(/[^a-z0-9]+/g, '');
}

function libraryBrowseDetailBack(id) {
  const st = _ls(id);
  st.browseDetail = null;
  _libBrowseResetTimer(id);
  libraryRender(id);
}

function librarySelectSeason(id, number) {
  _ls(id)._season = number;
  libraryRender(id);
}

function _libBrowseDetailHTML(id, expanded = false) {
  const st = _ls(id);
  const e = st.browseDetail;
  if (!e) return '';
  const info = e.info || {};
  const rawName = e.name || '';
  const safeName = rawName.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const displayTitle = safeName.replace(/\s*-\s*\d{4}$/, '');
  const lib = (st.libraries || []).find(l => l.name === st.browseLibrary);
  const libType = lib?.type || 'movie';
  const role = lib?.role || 'library';
  const actions = _libBrowseActions(id, e, role);
  const seasons = Array.isArray(info.season_details) ? info.season_details : [];
  const selectedSeason = seasons.some(s => s.number === st._season) ? st._season : seasons[0]?.number;
  st._season = selectedSeason;
  const deleteAction = role === 'file' ? '' : `
        <button class="media-icon-btn" onclick="libraryDeleteEntry('${id}','${e.name.replace(/'/g, "\\'")}')" title="delete" style="opacity:0.5">
          <i data-lucide="trash-2" style="width:11px;height:11px"></i>
        </button>`;
  return `
    ${expanded ? '' : `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-shrink:0">
      <button class="media-icon-btn" onclick="libraryBrowseDetailBack('${id}')" title="back">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
      </button>
      <span style="font-weight:700;font-size:12px;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
        onclick="libraryBrowseDetailBack('${id}')">${st.browseLibrary || displayTitle}</span>
      <span style="margin-left:auto">
        ${deleteAction}
      </span>
    </div>`}
    <div id="${id}-browse-detail" style="flex:1;overflow-y:auto;min-height:0">
      ${_renderMediaDetailHTML({
        title: info.title || displayTitle,
        year: info.year,
        type: info.type || (libType === 'tv' ? 'tv' : 'movie'),
        rating: info.rating,
        genres: info.genres,
        poster: info.poster,
        overview: info.overview,
        actions: expanded ? `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">${actions}${deleteAction}</div>` : actions,
        seasons: info.seasons,
        seasonDetails: seasons,
        selectedSeason,
        seasonScope: 'library',
        tileId: id,
        expanded
      })}
    </div>`;
}

function _libBrowseActions(id, entry, role) {
  const action = role === 'collection' ? 'find' : (entry.action || 'play');
  return _mediaInfoActionHTML(id, {..._mediaActionInfo(id, 'library'), action}, 'library');
}

function libPlay(id) {
  const st = _ls(id);
  const e = st.browseDetail;
  if (!e) return;
  const info = e.info || {};
  const title = info.title || e.name.replace(/\s*-\s*\d{4}$/, '');
  const year = info.year || e.name.match(/\s*-\s*(\d{4})$/)?.[1];
  const args = ['player', 'play-video', title];
  if (year) args.push('--year', String(year));
  const player = typeof _activeMediaPlayer === 'function' ? _activeMediaPlayer() : '';
  if (player) args.push('--player', player);
  runOp(args);
}

function libraryDeleteEntry(id, name) {
  const st = _ls(id);
  const lib = (st.libraries || []).find(l => l.name === st.browseLibrary);
  if (!lib) return;
  const path = lib.path + '/' + name;
  _showModal(`
    <h3>Delete media</h3>
    <div id="lib-delete-msg" style="font-size:13px;color:var(--text2);line-height:1.55"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn accent" id="lib-delete-confirm">Delete</button>
    </div>
  `);
  document.getElementById('lib-delete-msg').textContent = `Delete "${name}"?`;
  const button = document.getElementById('lib-delete-confirm');
  button.onclick = async () => {
    button.disabled = true;
    closeModal();
    await runOp(['library', 'delete-entry', path]);
    st.browseDetail = null;
    _libBrowseFetch(id, false);
  };
}

function _libBrowseItemsHTML(id) {
  const st = _ls(id);
  const entries = st.browseEntries || [];
  const hasMore = entries.length < st.browseTotal;
  if (!entries.length) {
    if (st.browseError) {
      if (st.browseError.includes('404')) return `<div class="muted" style="margin-top:16px">not synced yet — run sync first</div>`;
      return `<div class="muted" style="margin-top:16px">${st.browseError}</div>`;
    }
    return `<div class="muted" style="margin-top:16px">${st.browseLoading ? 'loading…' : 'empty'}</div>`;
  }
  const items = st.browseView === 'short'
    ? `<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px">${st.browseEntries.map((e, idx) => _libBrowseShortHTML(e, id, idx)).join('')}</div>`
    : st.browseEntries.map((e, idx) => _libBrowseRowHTML(e, id, idx)).join('');
  return items + (hasMore ? `<div id="${id}-sentinel" style="height:1px"></div>` : '');
}

function _libObserveSentinel(id) {
  const st = _ls(id);
  if (st._observer) { st._observer.disconnect(); st._observer = null; }
  const sentinel = document.getElementById(`${id}-sentinel`);
  if (!sentinel) return;
  const root = document.getElementById(`${id}-browse-items`);
  st._observer = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting && !st.browseLoading) {
      _libBrowseFetch(id, true);
    }
  }, { root, rootMargin: '200px' });
  st._observer.observe(sentinel);
}

async function libraryRefreshActive(id) {
  const st = _ls(id);
  if (!st?._visible) return;
  let arr;
  try {
    const state = await (await fetch('/api/config?target=torrent')).json();
    arr = Array.isArray(state) ? state : state.items;
  } catch (_) { return; }
  if (!Array.isArray(arr)) return;
  st.active = arr;
  if (st.renderActive) st.renderActive(arr);
  if (st.mode === 'libraries') libraryRender(id);
}

function _libGoDownload(id) {
  const st = _ls(id);
  const lib = (st.libraries || []).find(l => l.role === 'download');
  if (lib) libraryBrowse(id, lib.name);
}

function _libStartPoll(id) {
  const st = _ls(id);
  if (st._poll) clearInterval(st._poll);
  st._poll = setInterval(async () => {
    if (!document.getElementById(`${id}-view`)) {
      _libStopPoll(id);
      delete _libraryState[id];
      return;
    }
    if (st.mode === 'libraries') {
      await _libLoadFromConfig(id);
      libraryRender(id);
      await libraryRefreshActive(id);
    }
  }, _libCfgRefreshSec() * 1000);
}
function _libStopPoll(id) {
  const st = _ls(id);
  if (st?._poll) { clearInterval(st._poll); st._poll = null; }
}

function _libSyncColor(info) {
  const role = info.role || 'library';
  if (role === 'download') {
    if (info.pending == null) return '';
    return info.pending ? ' amber' : ' green';
  }
  const ts = info.last_sync;
  if (!ts) return '';
  const age = (Date.now() - new Date(ts).getTime()) / 1000;
  if (age < 3600) return ' green';
  if (age < 86400) return ' amber';
  return '';
}

function _libSyncTitle(info) {
  const role = info.role || 'library';
  if (role === 'download') {
    if (info.pending == null) return 'torrent client unavailable';
    return info.pending ? `${info.pending} completed download${info.pending > 1 ? 's' : ''} pending import`
                        : 'all downloads imported';
  }
  if (!info.last_sync) return 'never synced';
  const age = Math.round((Date.now() - new Date(info.last_sync).getTime()) / 1000);
  const h = Math.floor(age / 3600);
  if (h < 1) return `synced ${Math.floor(age / 60)}m ago`;
  if (h < 24) return `synced ${h}h ago`;
  return `synced ${Math.floor(h / 24)}d ago`;
}

function _libFmtSpeed(bps) {
  if (!bps) return '';
  const units = ['B','KB','MB','GB'];
  let i = 0, n = bps;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 ? 1 : 0)} ${units[i]}/s`;
}
function _libActiveRowHTML(t) {
  const name = String(t.label || t.name || '?')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const pct = Math.min(100, Math.max(0, t.progress || 0));
  const speed = _libFmtSpeed(t.dl_speed);
  const sz = t.size ? `${_libFmtSize(t.size)}` : '';
  return `
    <div class="lib-recent-card">
      ${t.poster ? `<img src="${t.poster}" loading="lazy">` : '<div class="lib-recent-poster"></div>'}
      <div style="min-width:0;flex:1;align-self:center">
    <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;font-weight:600">${name}</div>
    <div style="height:3px;background:var(--border2);border-radius:2px;overflow:hidden;margin:4px 0">
      <div style="height:100%;background:var(--accent);width:${pct}%;transition:width .3s"></div>
    </div>
    <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;color:var(--text3)">
      ${pct >= 100 ? '100' : pct.toFixed(1)}%${speed?` · ${speed}`:''}${sz?` · ${sz}`:''}
    </div>
      </div>
    </div>`;
}

const _LIB_QUALITY_CHIPS = [{v:'720',l:'720p'},{v:'1080',l:'1080p'},{v:'2160',l:'4K'}];
const _LIB_ROLE_DESCRIPTIONS = {library:'Organized local media.', collection:'Media gathered from source lists.', download:'Incoming downloaded media.'};
const _LIB_SOURCE_PREFIX = {tmdb:'https://api.themoviedb.org/3/', trakt:'https://api.trakt.tv/', cinemeta:'https://v3-cinemeta.strem.io/'};
const _LIB_PRESETS = [
  ['Popular', 'tmdb', '{type}/popular'],
  ['Trending', 'tmdb', 'trending/{type}/week'],
  ['Academy Awards', 'tmdb', 'list/28'],
  ['Best Anime', 'tmdb', 'list/5779'],
  ['Best Fighting Movies', 'tmdb', 'list/136714'],
  ['IMDb: Top Rated Movies', 'trakt', 'users/justin/lists/imdb-top-rated-movies/items'],
  ['IMDb: Top Rated TV Shows', 'trakt', 'users/justin/lists/imdb-top-rated-tv-shows/items'],
  ['BFI 100 Greatest', 'trakt', 'users/benstanley/lists/bfi-the-100-greatest-films-of-all-time/items'],
  ['The Best Movie You Never Saw', 'trakt', 'users/giladg/lists/the-best-movie-you-never-saw/items'],
  ['Best Mindfucks', 'trakt', 'users/benfranklin/lists/best-mindfucks/items'],
];
const _LIB_GENRES = ['Action','Adventure','Animation','Comedy','Crime','Documentary','Drama','Family','Fantasy','History','Horror','Music','Mystery','Romance','Sci-Fi','Thriller','War','Western'];
const _LIB_GENRE_IDS = [28,12,16,35,80,99,18,10751,14,36,27,10402,9648,10749,878,53,10752,37];
const _LIB_TV_GENRE_IDS = _LIB_GENRE_IDS.map(id => ({28:10759,12:10759,14:10765,878:10765,10752:10768})[id] || id);

function _libGenreChipsHTML(rule) {
  const active = new Set(((rule || {}).genres || []).map(g => g.toLowerCase()));
  return _LIB_GENRES.map(g => {
    const v = g.toLowerCase();
    return `<button class="media-chip genre-chip${active.has(v) ? ' active' : ''}" data-genre="${v.replace(/"/g, '&quot;')}" onclick="libraryToggleGenre(this)">${g}</button>`;
  }).join('');
}

function _libFiltersHeader(onclear) {
  return `<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px">
    <label style="font-size:12px;color:var(--text3);font-weight:600">Filters</label>
    <button class="media-chip" style="display:flex;align-items:center;gap:4px;font-size:9px;padding:2px 6px" onclick="${onclear}()" title="Clear filters"><i data-lucide="eraser" style="width:12px;height:12px"></i>clear</button>
  </div>`;
}

function _libRuleRowHTML(existing, role) {
  const lists = existing?.lists || [];
  const custom = lists.length && lists.every(l => l.url.startsWith(_LIB_SOURCE_PREFIX.tmdb + 'discover/'));
  const presetIndexes = lists.map(list => _LIB_PRESETS.findIndex(([, source, path]) => source === list.source &&
    ['movie','tv'].some(type => _LIB_SOURCE_PREFIX[source] + path.replace('{type}', type) === list.url)));
  const mode = custom ? 'filter' : !lists.length || (presetIndexes.every(i => i >= 0) && new Set(presetIndexes).size === 1) ? 'presets' : 'url';
  const params = new URLSearchParams(custom ? lists[0].url.split('?')[1] : '');
  const genreIds = custom && lists[0].url.includes('/discover/tv') ? _LIB_TV_GENRE_IDS : _LIB_GENRE_IDS;
  const rule = custom ? {
    min_rating:params.get('vote_average.gte'), language:params.get('with_original_language'),
    year_from:(params.get('primary_release_date.gte') || params.get('first_air_date.gte') || '').slice(0,4),
    year_to:(params.get('primary_release_date.lte') || params.get('first_air_date.lte') || '').slice(0,4),
    genres:_LIB_GENRES.filter((g, i) => (params.get('with_genres') || '').split('|').includes(String(genreIds[i])))
  } : existing?.rule || {};
  const genresBlock = `
    <div class="mlib-row" style="align-items:flex-start;margin-top:4px">
      <label style="padding-top:2px">Genres</label>
      <div id="mlib-rule-genres"><div id="mlib-genre-options">${_libGenreChipsHTML(rule)}
        <details class="mlib-more" hidden><summary class="media-chip">more…</summary><div class="mlib-genre-overflow"></div></details>
      </div></div>
    </div>`;
  if (role === 'collection') {
    return `
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <label style="font-size:12px;color:var(--text3)">Source</label>
          <div id="mlib-source-mode" class="media-chip-group" data-mode="${mode}" style="margin:10px 0;gap:8px">
            ${['URL','Presets','Filter'].map(mode => `<button class="media-chip" data-mode="${mode.toLowerCase()}" onclick="librarySourceMode(this.dataset.mode)">${mode}</button>`).join('')}
          </div>
          <button id="mlib-add-source" class="media-chip" style="margin-left:auto" onclick="libraryAddListRow()">+ source</button>
        </div>
      <div id="mlib-lists" class="mlib-source-panel" data-mode="url">
        ${(lists.length ? lists : [{source:'tmdb',url:''}]).map((list, i) => _libListRowHTML(list, i)).join('')}
      </div>
      <div class="mlib-source-panel" data-mode="presets" hidden>
        <div class="media-chip-group" style="margin:0;gap:8px">
          ${_LIB_PRESETS.map(([name, source], i) => `<button class="media-chip${mode === 'presets' && presetIndexes[0] === i ? ' active' : ''}" data-preset="${i}" title="${source}" onclick="this.parentElement.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === this))">${name}</button>`).join('')}
        </div>
      </div>
      <div class="mlib-source-panel" data-mode="filter" hidden>
        <div class="mlib-row">
          <label>Rating ≥</label>
          <input id="mlib-rule-rating" class="media-search-input" type="number" min="0" max="10" step="0.5" placeholder="0"
            value="${rule.min_rating ?? ''}" style="width:56px;flex:none">
          <label>Lang</label>
          <input id="mlib-rule-language" class="media-search-input" type="text" placeholder="en"
            value="${rule.language ?? ''}" style="width:56px;flex:none">
        </div>
        <div class="mlib-row">
          <label>Year</label>
          <input id="mlib-rule-year-from" class="media-search-input" type="number" placeholder="from"
            value="${rule.year_from ?? ''}" style="width:72px;flex:none">
          <span style="font-size:10px;color:var(--text3)">—</span>
          <input id="mlib-rule-year-to" class="media-search-input" type="number" placeholder="to"
            value="${rule.year_to ?? ''}" style="width:72px;flex:none">
        </div>
        ${genresBlock}
      </div>`;
  }
  const activeQuality = (rule.quality || '').toLowerCase();
  return `
    ${_libFiltersHeader('libraryClearRule')}
    <div style="display:flex;flex-direction:column;gap:10px">
      <div class="mlib-row">
        <label>Quality</label>
        <div id="mlib-rule-quality" class="media-chip-group" style="margin:0">${_LIB_QUALITY_CHIPS.map(q => `
          <button class="media-chip${activeQuality === q.v ? ' active' : ''}" data-q="${q.v}"
            onclick="libraryPickQuality(this)">${q.l}</button>`).join('')}</div>
      </div>
      ${genresBlock}
    </div>`;
}

function _libListRowHTML(list={source:'tmdb', url:''}, index=0) {
  return `<div class="mlib-list-row" style="display:flex;gap:6px">
    <select class="mlib-list-source media-search-input" style="flex:0 0 70px;background:var(--surface2)" onchange="this.nextElementSibling.value=_LIB_SOURCE_PREFIX[this.value];libraryUpdateListControls()">
      ${Object.keys(_LIB_SOURCE_PREFIX).map(source => `<option value="${source}"${list.source===source?' selected':''}>${source}</option>`).join('')}
    </select>
    <input class="mlib-list-url media-search-input" style="min-width:0" aria-label="Source URL" oninput="libraryUpdateListControls()" value="${String(list.url || _LIB_SOURCE_PREFIX[list.source]).replace(/"/g,'&quot;')}">
    <button class="media-chip" title="Delete source" ${index === 0 ? 'disabled' : ''} onclick="this.parentElement.remove();libraryUpdateListControls()">delete</button>
  </div>`;
}

function libraryAddListRow() {
  if (document.getElementById('mlib-add-source').disabled) return;
  const rows = document.getElementById('mlib-lists');
  rows.insertAdjacentHTML('beforeend', _libListRowHTML(undefined, rows.children.length));
  libraryUpdateListControls();
}

function libraryUpdateListControls() {
  const row = document.getElementById('mlib-lists').lastElementChild;
  const url = row.querySelector('.mlib-list-url').value.trim();
  document.getElementById('mlib-add-source').disabled = !url || url === _LIB_SOURCE_PREFIX[row.querySelector('select').value];
}

function librarySourceMode(mode) {
  const switcher = document.getElementById('mlib-source-mode');
  if (!switcher) return;
  switcher.dataset.mode = mode;
  switcher.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  document.querySelectorAll('.mlib-source-panel').forEach(panel => panel.hidden = panel.dataset.mode !== mode);
  document.getElementById('mlib-add-source').style.display = mode === 'url' ? '' : 'none';
  libraryUpdateListControls();
  libraryLayoutGenres();
}

function libraryToggleType(btn) {
  btn.classList.toggle('active');
}

function libraryToggleGenre(btn) {
  btn.classList.toggle('active');
  libraryLayoutGenres();
}

function libraryLayoutGenres() {
  const wrap = document.getElementById('mlib-rule-genres');
  if (!wrap?.clientWidth) return;
  const chips = wrap.querySelector('#mlib-genre-options'), more = wrap.querySelector('details');
  const overflow = more.querySelector('div');
  const buttons = Array.from(wrap.querySelectorAll('.genre-chip')).sort((a, b) =>
    Number(b.classList.contains('active')) - Number(a.classList.contains('active')) ||
    _LIB_GENRES.indexOf(a.textContent) - _LIB_GENRES.indexOf(b.textContent));
  more.open = false;
  more.hidden = true;
  buttons.forEach(b => chips.insertBefore(b, more));
  if (new Set(buttons.map(b => b.offsetTop)).size <= 3) return;
  more.hidden = false;
  while (new Set([...chips.children].map(b => b.offsetTop)).size > 3 && more.previousElementSibling)
    overflow.prepend(more.previousElementSibling);
}

let _libGenreObserver;
function libraryInitGenres() {
  _libGenreObserver?.disconnect();
  librarySourceMode(document.getElementById('mlib-source-mode')?.dataset.mode);
  const wrap = document.getElementById('mlib-rule-genres');
  let width;
  _libGenreObserver = new ResizeObserver(([entry]) => {
    if (entry.contentRect.width !== width) { width = entry.contentRect.width; libraryLayoutGenres(); }
  });
  _libGenreObserver.observe(wrap);
  libraryLayoutGenres();
}

async function libraryBrowseFolder(button) {
  const input = document.getElementById('mlib-path');
  button.disabled = true;
  try {
    const response = await fetch('/api/library/folder?' + new URLSearchParams({path:input.value}), {method:'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not open folder picker');
    if (data.path && input.isConnected) input.value = data.path;
  } catch (error) {
    toast(error.message, 'err');
  } finally {
    button.disabled = false;
  }
}

function libraryPickQuality(btn) {
  const wrap = btn.parentElement;
  wrap.querySelectorAll('[data-q]').forEach(b => b.classList.toggle('active', b === btn && !b.classList.contains('active')));
  if (!btn.classList.contains('active')) return;
  wrap.querySelectorAll('[data-q]').forEach(b => { if (b !== btn) b.classList.remove('active'); });
}

function libraryClearRule() {
  document.querySelectorAll('#mlib-rule-genres .genre-chip').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('#mlib-rule-quality .media-chip').forEach(b => b.classList.remove('active'));
  libraryLayoutGenres();
}

function libraryClearFilters() {
  ['mlib-rule-rating', 'mlib-rule-year-from', 'mlib-rule-year-to', 'mlib-rule-language'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  document.querySelectorAll('#mlib-rule-genres .genre-chip').forEach(b => b.classList.remove('active'));
  libraryLayoutGenres();
}

function libraryModalRuleRefresh(id, role) {
  const roles = document.getElementById('mlib-role');
  roles.dataset.role = role;
  roles.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.role === role));
  document.getElementById('mlib-role-description').textContent = _LIB_ROLE_DESCRIPTIONS[role];
  const ruleWrap = document.getElementById('mlib-rule-wrap');
  if (!ruleWrap) return;
  const st = _ls(id);
  const editName = document.getElementById('mlib-name')?.disabled ? document.getElementById('mlib-name')?.value : null;
  const existing = editName ? (st.libraries || []).find(l => l.name === editName) : null;
  ruleWrap.innerHTML = _libRuleRowHTML(existing, role);
  libraryInitGenres();
  if (typeof lucide !== 'undefined') lucide.createIcons();
  const h3 = document.querySelector('#modal h3');
  if (h3) h3.textContent = `${editName ? 'Edit' : 'Add'} ${role}`;
}

function libraryModal(id, editName, addRole = 'library') {
  const st = _ls(id);
  const existing = editName ? (st.libraries || []).find(l => l.name === editName) : null;
  const modal = document.getElementById('modal');
  if (!modal) return;
  modal.dataset.locked = 'false';

  const role = existing?.role || addRole;
  const roles = existing ? ['library','collection','download']
    : addRole === 'collection' ? ['collection'] : ['library','download'];
  const title = existing ? 'Edit' : 'Add';
  const activeTypes = new Set((existing?.type || '').split(',').map(t => t.trim()).filter(Boolean));
  modal.innerHTML = `
    <h3>${title} ${role}</h3>
    <div class="mlib-form" style="display:flex;flex-direction:column;gap:10px">
      <div class="mlib-row">
        <label>Role:</label>
        <div id="mlib-role" class="media-chip-group" data-role="${role}" style="margin:0">
          ${roles.map(r => `<button class="media-chip${role === r ? ' active' : ''}" data-role="${r}" onclick="libraryModalRuleRefresh('${id}', '${r}')">${r}</button>`).join('')}
        </div>
      </div>
      <div id="mlib-role-description" style="margin-bottom:6px">${_LIB_ROLE_DESCRIPTIONS[role]}</div>
      <div class="mlib-row">
        <label>Name:</label>
        <input id="mlib-name" class="media-search-input" placeholder="Movies" value="${(existing?.name || '').replace(/"/g, '&quot;')}" style="flex:1" ${editName ? 'disabled' : ''}>
        <div id="mlib-type" class="media-chip-group" aria-label="Type">
          ${['movie','tv','artist'].map(t => `<button class="media-chip type-chip${activeTypes.has(t) ? ' active' : ''}" data-type="${t}" onclick="libraryToggleType(this)">${t}</button>`).join('')}
        </div>
      </div>
      <div class="mlib-row">
        <label>Path:</label>
        <input id="mlib-path" name="library-path-${Date.now()}" autocomplete="off" spellcheck="false" class="media-search-input" placeholder="D:\\Media\\Movies" value="${(existing?.path || '').replace(/"/g, '&quot;')}" style="flex:1">
        <button class="media-icon-btn" style="width:auto;gap:4px;padding:0 4px" onclick="libraryBrowseFolder(this)" title="Browse folder"><i data-lucide="folder-open" style="width:14px;height:14px"></i>browse</button>
      </div>
      <div id="mlib-rule-wrap">${_libRuleRowHTML(existing, role)}</div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:4px">
        ${editName ? `<button class="btn" style="background:rgba(204,68,68,0.12);border-color:rgba(204,68,68,0.4);color:#c44" onclick="libraryModalDelete('${id}', '${editName.replace(/'/g, "\\'")}')">Delete</button>` : ''}
        <button class="btn" onclick="closeModal()">cancel</button>
        <button class="btn accent" onclick="libraryModalSubmit('${id}', '${editName ? editName.replace(/'/g, "\\'") : ''}')">${editName ? 'save' : 'add'}</button>
      </div>
    </div>`;
  document.getElementById('modal-backdrop').style.display = 'flex';
  libraryInitGenres();
  if (typeof lucide !== 'undefined') lucide.createIcons();
  const _esc = e => { if (e.key === 'Escape') { document.removeEventListener('keydown', _esc); closeModal(); } };
  document.addEventListener('keydown', _esc);
}

async function libraryModalSubmit(id, oldName) {
  const st = _ls(id);
  const name = document.getElementById('mlib-name')?.value.trim();
  const type = Array.from(document.querySelectorAll('#mlib-type .type-chip.active')).map(b => b.dataset.type).join(',');
  const role = document.getElementById('mlib-role')?.dataset.role || 'library';
  const path = document.getElementById('mlib-path')?.value.trim();
  if (!name || !path) { toast('name and path required', 'err'); return; }
  if (role === 'library' && (!type || type.includes(','))) { toast('library role needs a single type (movie/tv/artist)', 'err'); return; }
  const genres = Array.from(document.querySelectorAll('#mlib-rule-genres .genre-chip.active')).map(b => b.dataset.genre);
  const quality = document.querySelector('#mlib-rule-quality .media-chip.active')?.dataset.q || '';
  const minRating = document.getElementById('mlib-rule-rating')?.value || '';
  const yearFrom = document.getElementById('mlib-rule-year-from')?.value || '';
  const yearTo = document.getElementById('mlib-rule-year-to')?.value || '';
  const language = document.getElementById('mlib-rule-language')?.value || '';
  const existing = oldName ? (st.libraries || []).find(l => l.name === oldName) : null;
  const mode = document.getElementById('mlib-source-mode')?.dataset.mode;
  const types = type.split(',').filter(t => t === 'movie' || t === 'tv');
  let lists = role === 'collection' ? Array.from(document.querySelectorAll('.mlib-list-row')).map(row => ({
    source: row.querySelector('.mlib-list-source')?.value || 'tmdb',
    url: row.querySelector('.mlib-list-url')?.value.trim() || '',
  })).filter(list => list.url && list.url !== _LIB_SOURCE_PREFIX[list.source]) : (existing?.lists || []);
  if (mode === 'presets') {
    lists = Array.from(document.querySelectorAll('[data-preset].active')).flatMap(button => {
      const [, source, path] = _LIB_PRESETS[Number(button.dataset.preset)];
      return (path.includes('{type}') ? types.map(t => path.replace('{type}', t)) : [path])
        .map(path => ({source, url:_LIB_SOURCE_PREFIX[source] + path}));
    });
  }
  if (mode === 'filter') {
    lists = types.map(t => {
      const query = new URLSearchParams();
      const date = t === 'tv' ? 'first_air_date' : 'primary_release_date';
      if (minRating) query.set('vote_average.gte', minRating);
      if (language) query.set('with_original_language', language.trim());
      if (yearFrom) query.set(date + '.gte', yearFrom + '-01-01');
      if (yearTo) query.set(date + '.lte', yearTo + '-12-31');
      const genreIds = t === 'tv' ? _LIB_TV_GENRE_IDS : _LIB_GENRE_IDS;
      if (genres.length) query.set('with_genres', [...new Set(genres.map(g => genreIds[_LIB_GENRES.findIndex(name => name.toLowerCase() === g)]))].join('|'));
      return {source:'tmdb', url:_LIB_SOURCE_PREFIX.tmdb + 'discover/' + t + '?' + query};
    });
  }
  if (role === 'collection' && !lists.length) { toast('Choose a source and its movie or TV type', 'err'); return; }
  const args = ['library', 'add', name, type, path, '--role', role];
  if (lists.length) args.push('--lists', JSON.stringify(lists));
  if (role !== 'collection') {
    if (genres.length) args.push('--rule-genres', genres.join(','));
    if (quality) args.push('--rule-quality', quality);
  }
  if (oldName && oldName !== name) {
    await runOp(['library', 'delete', oldName]);
  }
  await runOp(args);
  closeModal();
  runOp(['library', 'list']);
}

async function libraryModalDelete(id, name) {
  if (!confirm(`Delete library "${name}"?`)) return;
  await runOp(['library', 'delete', name]);
  closeModal();
  runOp(['library', 'list']);
}
