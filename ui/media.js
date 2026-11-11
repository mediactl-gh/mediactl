// web/media.js

/* ── Injected CSS ──────────────────────────────────────────────────────────── */
(function() {
  const s = document.createElement('style');
  s.textContent = `
    .media-find-btn {
      width: 100%; padding: 7px; margin-top: 10px;
      background: rgba(79,142,247,0.1); border: 1px solid rgba(79,142,247,0.3);
      color: var(--accent); border-radius: 8px; cursor: pointer;
      font-family: var(--font); font-size: 11px; font-weight: 700;
      letter-spacing: 0.06em; text-transform: uppercase; transition: all 0.15s;
      display: flex; align-items: center; justify-content: center; gap: 6px;
    }
    .media-find-btn:hover { background: rgba(79,142,247,0.2); border-color: var(--accent); }
    .media-find-btn:disabled { opacity: 0.45; cursor: default; }
    .media-search-panel .media-search-input {
      min-width:0; min-height:32px; border-color:var(--text3); color:var(--text);
    }
    .media-search-panel .media-search-input::placeholder { color:var(--text2); opacity:.9; }
    .media-search-panel .media-search-input:focus {
      border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-dim);
    }
    .media-search-panel .media-icon-btn { width:28px;height:28px;color:var(--text2); }
    .media-search-panel .media-icon-btn.accent, .media-icon-btn.accent.media-watchlist-btn {
      color:var(--accent);background:var(--accent-dim);border:1px solid var(--accent-border);
    }
    .media-watchlist-btn:disabled { opacity:.45;cursor:default; }
    .media-search-panel .media-chip { color:var(--text2);border-color:var(--text3); }
    .media-search-panel .media-chip.active { border-color:var(--accent);color:var(--accent); }
    .media-season-chips { position:relative;min-width:0; }
    .media-season-chips .media-chip { height:22px; }
    .media-season-chips [hidden] { display:none; }
  `;
  document.head.appendChild(s);
})();

TILES['media'] = {
  html(tile) {
    const id = tile.id;
    return `
    ${_tileHeader(id, `MEDIA`,
      `<button class="icon-btn media-cfg-btn" onclick="mediaOpenConfig('${id}')" title="Settings">⚙</button>`)}
    <div id="${id}-view" style="height:calc(100% - 38px);overflow-y:auto;padding:0 14px 14px"></div>`;
  },
  bind(id) {
    const previous = _mediaState[id];
    _mediaRecommStop(`${id}-recomm`);
    clearTimeout(previous?._idleT);
    clearTimeout(previous?._retT);
    _mediaState[id] = {
      view: 'entry', query: '',
      types: ['movie', 'tv'],
      info: null, results: [], torrents: [], searching: false,
      filters: { quality: [] },
    };
    try {
      const p = JSON.parse(localStorage.getItem('mediaPrefs') || '{}');
      if (p.quality) _mediaState[id].filters.quality = p.quality;
    } catch(e) {}
    _mediaRenderEntry(id);
  },
  events(id) {
    onEvent('media.search',   evt => _mediaOnSearch(id, evt));
    onEvent('media.info',     evt => _mediaOnInfo(id, evt));
  }
};

const _mediaState = {};
function _ms(id) { return _mediaState[id] || {}; }

function _mediaShowRecommendations(id) {
  return dashboard.tiles.find(tile => tile.id === id)?.config?.show_recommendations !== false;
}

function _mediaUpdateRecommendations(id) {
  const area = document.getElementById(`${id}-rec-area`);
  if (!area) return;
  area.hidden = !_mediaShowRecommendations(id) || !!document.getElementById(`${id}-q`)?.value.trim();
  if (area.hidden) _mediaRecommStop(`${id}-recomm`);
  else mediaRecommBind(`${id}-recomm`);
}

async function mediaToggleRecommendations(id, input) {
  input.disabled = true;
  if (await saveTileConfig(id, {show_recommendations:input.checked})) _mediaUpdateRecommendations(id);
  input.checked = _mediaShowRecommendations(id);
  input.disabled = false;
}

function _mediaArmReturn(id) {
  const st = _ms(id);
  if (st._retT) clearTimeout(st._retT);
  st._retT = setTimeout(() => mediaClearSearch(id), 30000);
}

function _mediaRender(id) {
  const v = _ms(id).view;
  if      (v === 'entry')   _mediaRenderEntry(id);
  else if (v === 'results') _mediaRenderResults(id);
  else if (v === 'pick')    _mediaRenderPick(id);
  else if (v === 'info' || v === 'queued') _mediaRenderInfo(id);
}

function _mediaRenderEntry(id) {
  const st = _ms(id); if (st._retT) { clearTimeout(st._retT); st._retT = null; }
  _mediaRecommStop(`${id}-recomm`);
  const el = document.getElementById(`${id}-view`); if (!el) return;
  el.innerHTML = `
    <div class="media-search-panel" style="display:flex;flex-direction:column;height:100%">
    <div style="display:flex;gap:6px;margin-top:14px;align-items:center;flex-shrink:0">
      <input class="media-search-input" id="${id}-q" placeholder="search media"
        autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
        data-lpignore="true" data-1p-ignore="true" data-form-type="other"
        readonly onfocus="this.removeAttribute('readonly')"
        value="${(st.query||'').replace(/"/g,'&quot;')}"
        oninput="_mediaSearchActivity('${id}')"
        onkeydown="if(event.key==='Enter') mediaSearch('${id}')">
      <button class="media-icon-btn" onclick="mediaClearSearch('${id}')" title="clear" style="flex-shrink:0">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
      <button class="media-icon-btn accent" onclick="mediaSearch('${id}')" title="search" style="flex-shrink:0">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      </button>
    </div>
    <div class="media-chip-group" style="justify-content:flex-end;margin:8px;flex-shrink:0">
      ${['movie','tv'].map(t => `<button class="media-chip ${st.types.includes(t)?'active':''}"
        onclick="mediaToggleType('${id}','${t}')">${t==='tv'?'TV':'Movie'}</button>`).join('')}
    </div>
    <div id="${id}-results-area" style="flex-shrink:0">
      ${st.results.length ? _mediaResultsHTML(id) : ''}
    </div>
    <div id="${id}-rec-area" style="flex:1;min-height:0;overflow:hidden;margin-top:16px;border-top:1px solid var(--border2);padding-top:12px">
      ${mediaRecommHTML(`${id}-recomm`, true)}
    </div>
    </div>`;
  _mediaUpdateRecommendations(id);
}
function mediaClearSearch(id) {
  const st = _ms(id);
  st._searchRequest = null;
  st.results = []; st.info = null; st.torrents = []; st.query = ''; st.searching = false;
  st.view = 'entry';
  _mediaRenderEntry(id);
}
async function mediaSearch(id, query) {
  const st = _ms(id);
  const q = (query ?? document.getElementById(`${id}-q`)?.value)?.trim(); if (!q) return;
  st.query = q;
  st.view = 'entry';
  if (query !== undefined) _mediaRenderEntry(id);
  const request = st._searchRequest = {};
  const area = document.getElementById(`${id}-results-area`);
  if (area) area.innerHTML = `<div class="muted">Searching…</div>`;
  const args = ['media', 'search', q, '--json'];
  if (st.types?.length === 1) args.push('--type', st.types[0]);
  const response = await runOp(args);
  if (_ms(id) !== st || st._searchRequest !== request || st.view !== 'entry' || !area?.isConnected) return;
  try {
    if (!response.ok) throw new Error('Search failed');
    const results = JSON.parse(response.stdout);
    if (!Array.isArray(results)) throw new Error('Invalid search results');
    _mediaSearchResults(id, results);
  } catch (_) {
    area.innerHTML = `<div class="muted"></div>`;
    area.firstElementChild.textContent = response.stdout?.trim().startsWith('ERROR:')
      ? response.stdout.trim() : 'Search failed. Try again.';
    setDot(id, 'red');
  }
}
function mediaToggleType(id, t) {
  const st = _ms(id);
  st.query = document.getElementById(`${id}-q`)?.value ?? st.query;
  const i = st.types.indexOf(t);
  if (i >= 0) st.types.splice(i,1); else st.types.push(t);
  _mediaRenderEntry(id);
}

function _mediaResultsHTML(id) {
  const st = _ms(id);
  if (!st.results.length) return `<div class="muted">no results</div>`;
  const tf = st.types || [];
  const rows = st.results.map((r, i) => ({ r, i }))
    .filter(({r}) => !tf.length || tf.includes(r.type));
  return rows.map(({r, i}) => `
    <div class="media-row" style="cursor:pointer" onclick="mediaSelectResult('${id}',${i})">
      <span style="font-size:11px;font-family:var(--mono);color:var(--text3);min-width:32px">${r.year||'—'}</span>
      <span style="font-size:12px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.title}</span>
      ${r.type==='tv'?`<span style="font-size:8px;font-weight:700;letter-spacing:0.06em;color:var(--accent);flex-shrink:0">TV</span>`:''}
      ${(r.genres||[]).length?`<span style="font-size:9px;color:var(--text3);flex:0 1 auto;min-width:0;max-width:64px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.genres[0]}</span>`:''}
      ${r.rating?`<span style="font-size:10px;font-family:var(--mono);color:var(--amber);flex-shrink:0">★ ${r.rating}</span>`:''}
      <span class="media-icon-btn accent" title="details">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
      </span>
    </div>`).join('') || `<div class="muted">no results for selected type</div>`;
}
function _mediaRenderResults(id) {
  const area = document.getElementById(`${id}-results-area`);
  if (area) { area.innerHTML = _mediaResultsHTML(id); return; }
  const el = document.getElementById(`${id}-view`); if (!el) return;
  el.innerHTML = _mediaResultsHTML(id);
}
async function mediaSelectResult(id, i) {
  const st = _ms(id); const r = st.results[i]; if (!r) return;
  const cfg = await _mediaEnsureCfg();
  if (_ms(id) !== st || st.results[i] !== r) return;
  const provider = cfg.media?.info?.source || 'tmdb';
  const refId = r.ids?.[provider] ?? (provider === (r.provider || 'tmdb') ? r.id : '');
  _mediaRecommStop(`${id}-recomm`);
  const summary = {
    id: refId, provider, type: r.type === 'tv' ? 'tv' : 'movie',
    title: r.title, year: r.year, rating: r.rating || 0, action: r.action || 'find',
    poster: r.poster || null,
    genres: (r.genres || []).join(', '), loading: true,
  };
  delete st._season;
  delete st._seasonsExpanded;
  st.info = summary;
  st.view = 'info';
  try {
    const params = new URLSearchParams({provider, id:String(refId), type:summary.type,
      title:r.title || '', year:r.year || ''});
    const response = await fetch(`/api/media/info?${params}`);
    if (response.ok) {
      const cached = await response.json();
      if (st.info !== summary) return;
      st.info = _mediaCachedInfo(cached, summary);
      _mediaRenderInfo(id);
      if (summary.type !== 'tv' || cached.seasons_complete) return;
    }
  } catch (_) {}
  _mediaRenderInfo(id);
  const response = await runOp(['media', 'info', String(refId || r.title), '--source', provider,
    '--type', summary.type]);
  if (st.view !== 'info' || st.info?.provider !== provider || st.info?.title !== summary.title) return;
  const error = response.stdout?.trim();
  if (!response.ok || error?.startsWith('ERROR:')) {
    st.info.loading = false;
    toast(error?.startsWith('ERROR:') ? error : 'Media info fetch failed', 'err');
    _mediaRenderInfo(id);
    setDot(id, 'red');
  }
}
function mediaBackToResults(id) {
  const st = _ms(id);
  st.view = 'entry';
  _mediaRenderEntry(id);
}
function mediaSelectSeason(id, number) {
  _ms(id)._season = number;
  _mediaRenderInfo(id);
}

async function _mediaLoadSeasonData(id) {
  const st = _ms(id); const d = st.info;
  if (!d || d.type !== 'tv' || !d.id) return;
  const infoId = String(d.id);
  const params = new URLSearchParams({
    provider: d.provider || 'tmdb', id: infoId, type: 'tv', title: d.title || '', year: d.year || '',
  });
  try {
    const response = await fetch(`/api/media/info?${params}`);
    if (!response.ok) return;
    const cached = await response.json();
    if (String(st.info?.id) !== infoId) return;
    st.info = _mediaCachedInfo(cached, st.info);
    _mediaRenderInfo(id);
  } catch (_) {}
}

function _mediaCachedInfo(cached, fallback={}) {
  const provider = cached.provider || 'tmdb';
  return {...fallback, ...cached, provider, id:cached[`${provider}_id`] || fallback.id,
    type:cached.type || fallback.type,
    genres:Array.isArray(cached.genres) ? cached.genres.join(', ') : (cached.genres || fallback.genres || ''),
    votes:cached.vote_count || fallback.votes || 0, loading:false};
}

function _mediaRenderInfo(id) {
  _mediaArmReturn(id);
  const st = _ms(id); const el = document.getElementById(`${id}-view`); if (!el) return;
  const scrollTop = el.scrollTop;
  const d = st.info;
  if (!d) { el.innerHTML = `<div class="muted" style="margin-top:16px">search for a title first</div>`; return; }
  const seasonDetails = Array.isArray(d.season_details) ? d.season_details : [];
  if (d.type === 'tv' && !seasonDetails.some(s => s.number === st._season)) {
    st._season = seasonDetails.reduce((latest, season) => Math.max(latest, season.number), -1);
  }
  el.innerHTML = `
    <button class="media-icon-btn" onclick="mediaBackToResults('${id}')" title="back" style="margin:8px 0 10px">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
    </button>
    ${_renderMediaDetailHTML({...d,
      poster:d.poster || (d.poster_path ? `https://image.tmdb.org/t/p/w185${d.poster_path}` : null),
      actions:_mediaInfoActionHTML(id,d), seasonDetails,
      selectedSeason:st._season, seasonScope:d.type === 'tv' ? 'media' : '', tileId:id})}
    ${d.director?`<div style="font-size:10px;color:var(--text3);margin-bottom:2px">
      <span style="color:var(--text2);font-weight:600">Dir</span>  ${d.director}</div>`:''}
    ${d.cast?`<div style="font-size:10px;color:var(--text3);margin-bottom:8px;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${d.cast}</div>`:''}
    ${d.trailer?`<a href="${d.trailer}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:4px;
      font-size:10px;color:var(--text3);text-decoration:none;margin-bottom:10px;transition:color .15s"
      onmouseover="this.style.color='var(--text2)'" onmouseout="this.style.color='var(--text3)'">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/></svg> trailer</a>`:''}
    `;
  el.scrollTop = scrollTop;
}
function _mediaSearchResults(id, results) {
  const st=_ms(id);
  st.results=results
    .map(r=>({...r,rating:r.rating||0,genres:r.genres||[],type:r.type||'movie'}))
    .sort((a,b)=>b.rating-a.rating);
  st.info=null; st.torrents=[];
  st.view='entry';
  // if search bar is already rendered, just update the results area; else full render
  const area = document.getElementById(`${id}-results-area`);
  if (area) area.innerHTML = _mediaResultsHTML(id);
  else _mediaRenderEntry(id);
  setDot(id,'green');
}
function _mediaOnInfo(id, evt) {
  const st=_ms(id);
  if (!st.info || st.view === 'pick') return;
  if ((st.info.provider || 'tmdb') !== (evt.provider || 'tmdb')) return;
  if (st.info.type && evt.type && st.info.type !== evt.type) return;
  const sameId = st.info.id && String(st.info.id) === String(evt.id);
  const sameTitle = _mediaTitleKey(st.info.title) === _mediaTitleKey(evt.title);
  if (!sameId && !sameTitle) return;
  st.info={...st.info,
    id:evt.id, provider:evt.provider || 'tmdb', loading:false,
    type:evt.type==='tv'?'tv':'movie', lang:evt.lang||'', title:evt.title||'?', year:evt.year||'',
    action: evt.action === 'play' ? 'play' : 'find',
    rating:parseFloat(evt.rating)||0, votes:parseInt(evt.votes)||0, seasons:parseInt(evt.seasons)||0,
    cert:evt.cert||'', runtime:evt.runtime?parseInt(evt.runtime):null,
    genres:evt.genres||'', overview:evt.overview||'',
    director:evt.director||'', cast:evt.cast||'',
    trailer:evt.trailer||'', poster:evt.poster||null, poster_path:evt.poster_path||null,
  };
  st.view='info';
  _mediaRenderInfo(id);
  _mediaLoadSeasonData(id);
  setDot(id,'green');
}

function _mediaTitleKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

// ── Search inactivity ──────────────────────────
function _mediaSearchActivity(id) {
  const st = _ms(id);
  st._searchRequest = null;
  clearTimeout(st._idleT);
  const q = document.getElementById(`${id}-q`)?.value.trim();
  if (q) {                                 // typing → hide recs
    _mediaRecommStop(`${id}-recomm`);
    const area = document.getElementById(`${id}-rec-area`);
    if (area) area.hidden = true;
    st._idleT = setTimeout(() => {          // 1 min idle → clear + resume recs
      const inp = document.getElementById(`${id}-q`);
      if (inp) inp.value = '';
      mediaClearSearch(id);
    }, 60000);
  } else {                                 // cleared → show recs again
    _mediaUpdateRecommendations(id);
  }
}

// Shared media settings, details and actions for media, library and recommendations.

function _mediaActionState(id) {
  return (typeof _mediaRecommState !== 'undefined' && _mediaRecommState[id])
    || (typeof _libraryState !== 'undefined' && _libraryState[id])
    || (typeof _mediaState !== 'undefined' && _mediaState[id]) || {};
}

// ── Config popover (editable view of .out/media.json + .out/services.json) ─
let _mediaCfgPop  = null;
let _mediaCfgData = null;
let _mediaCfgSection = '';
let _mediaCfgTileId = '';

function _mediaPopover(anchor, html, {width, top, onClose}={}) {
  document.getElementById('dp-pop')?.closePopover?.();
  const pop = document.createElement('div'); pop.id = 'dp-pop';
  const scroll = document.createElement('div'); scroll.className = 'dp-scroll';
  scroll.innerHTML = html; pop.appendChild(scroll);
  const rect = anchor.getBoundingClientRect();
  const popW = width || Math.max(200, Math.min(rect.width, 320));
  let popTop = top ?? rect.bottom + 4;
  let left = rect.left;
  if (popTop + 220 > window.innerHeight - 8) popTop = Math.max(4, rect.top - 164);
  if (left + popW > window.innerWidth - 4) left = window.innerWidth - popW - 4;
  pop.style.cssText = `position:fixed;z-index:9000;top:${Math.max(4,popTop)}px;left:${Math.max(4,left)}px;
    width:${popW}px;max-height:220px;display:flex;flex-direction:column;background:var(--surface2);
    border:1px solid var(--border2);border-radius:10px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.4)`;
  document.body.appendChild(pop);
  const close = () => {
    pop.remove();
    document.removeEventListener('mousedown', outside, true);
    document.removeEventListener('keydown', escape);
    onClose?.();
  };
  const outside = e => { if (!pop.contains(e.target) && !anchor.contains(e.target)) close(); };
  const escape = e => { if (e.key === 'Escape') close(); };
  pop.closePopover = close;
  setTimeout(() => document.addEventListener('mousedown', outside, true), 10);
  document.addEventListener('keydown', escape);
  return pop;
}

async function _mediaEnsureCfg() {
  if (_mediaCfgData) return _mediaCfgData;
  try {
    const [media, services] = await Promise.all([
      fetch('/api/config?target=media').then(r => r.json()),
      fetch('/api/config?target=services').then(r => r.json()).catch(() => ({})),
    ]);
    services.indexer = { ...services.indexer,
      prowlarr: { url: '', api_key: '', ...services.indexer?.prowlarr } };
    _mediaCfgData = { media, services };
  } catch(e) { _mediaCfgData = { media: {}, services: {} }; }
  return _mediaCfgData;
}

function _cfgGet(data, path) {
  return path.split('.').reduce((o, k) => (o == null ? o : o[k]), data);
}

function _cfgEnumOpts(data, path) {
  return (data._options || {})[path];
}

function _mediaRowHTML(target, data, path, key) {
  const cur = _cfgGet(data, path);
  const enumOpts = _cfgEnumOpts(data, path);
  if (path === 'recommendations.lists') {
    const selected = Array.isArray(cur) ? cur : ['all'];
    return `<div class="dp-meta">lists</div>` + (enumOpts || ['all']).map(name => {
      const value = name.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
      return `<label class="dp-row-pick" style="display:flex;gap:8px;align-items:center;cursor:pointer">
        <input type="checkbox" value="${value}" ${selected.includes('all') || selected.includes(name) ? 'checked' : ''}
          onchange="mediaCfgListToggle(this)">${name === 'all' ? 'All lists' : value}</label>`;
    }).join('');
  }
  const isObj = cur && typeof cur === 'object' && !Array.isArray(cur);
  const display = Array.isArray(cur) ? cur.join(', ')
                : isObj              ? JSON.stringify(cur)
                : /\.(password|api_key)$/.test(path) && cur ? '•'.repeat(Math.min(String(cur).length, 12))
                : path === 'trakt.client_id' ? (cur ? 'configured' : 'not configured')
                : cur;
  const spanId = 'dp-val-' + path.replace(/\./g, '-');
  return `
    <div class="dp-row"><div class="dp-row-pick" data-target="${target}" data-path="${path}"
      style="display:flex;align-items:center"
      onclick="mediaCfgEdit(this)"
      title="${enumOpts ? 'choose: ' + enumOpts.join(' / ') : 'click to edit'}">
      <span class="dp-row-dim">${path === 'recommendations.refresh_interval' ? 'refresh interval (seconds; 0 = off)' : key}</span>
      <span id="${spanId}" style="margin-left:auto;font-family:var(--mono);font-size:10px;
        border-bottom:1px dotted var(--text3)">${display}</span>
    </div></div>`;
}

function _mediaCfgRowsHTML(cfg) {
  const { media, services } = cfg;
  const sections = Object.keys(media || {}).filter(s => !s.startsWith('_') && !['trakt', 'tmdb'].includes(s)
    && (_mediaCfgSection ? s === _mediaCfgSection : s !== 'recommendations'));
  if (!_mediaCfgSection) sections.push('display');
  if (!sections.length) return `<div class="dp-empty">no media.json</div>`;
  return sections.map((s, si) => {
    const sectionFields = Object.entries(media[s] || {}).filter(([k]) => !k.startsWith('_'));
    let sectionRows = sectionFields.map(([k]) =>
      _mediaRowHTML('media', media, `${s}.${k}`, k)
    ).join('');
    if (s === 'info') {
      sectionRows += _mediaRowHTML('media', media, 'tmdb.api_key', 'tmdb api key');
      sectionRows += _mediaRowHTML('media', media, 'trakt.client_id', 'trakt client id');
    }
    if (s === 'display' && !_mediaCfgSection) {
      sectionRows = `<div class="dp-row"><label class="dp-row-pick" style="display:flex;align-items:center;cursor:pointer">
        <span class="dp-row-dim">show_recommendations</span>
        <input type="checkbox" style="margin-left:auto" ${_mediaShowRecommendations(_mediaCfgTileId) ? 'checked' : ''}
          onchange="mediaToggleRecommendations('${_mediaCfgTileId}',this)">
      </label></div>`;
    }

    const clients = s === 'indexer' ? Object.keys(services[s] || {})
                  : media[s]?.client ? [media[s].client] : [];
    for (const active of clients) {
      const entry = services[s]?.[active];
      if (entry) {
        sectionRows += Object.entries(entry).map(([k]) =>
          _mediaRowHTML('services', services, `${s}.${active}.${k}`, s === 'indexer' ? `${active} ${k.replace(/_/g, ' ')}` : k)
        ).join('');
      }
    }

    return `
      <div class="dp-meta" style="padding:${si ? '8px' : '4px'} 10px 3px;text-transform:uppercase;letter-spacing:0.08em;
        font-size:9px;font-weight:700;color:var(--accent);opacity:1;
        ${si ? 'border-top:1px solid var(--border2);margin-top:4px' : ''}">${s}</div>
      ${sectionRows}`;
  }).join('');
}

async function _mediaCfgRefresh(target = 'media') {
  if (target === 'library') {
    if (!_librarySettingsPop) return;
    _librarySettings = await (await fetch('/api/config?target=library')).json();
    const scroll = _librarySettingsPop?.querySelector('.dp-scroll');
    if (scroll) scroll.innerHTML = _librarySettingsHTML();
    return;
  }
  if (!_mediaCfgPop) return;
  _mediaCfgData = null;
  await _mediaEnsureCfg();
  const scroll = _mediaCfgPop?.querySelector('.dp-scroll');
  if (scroll) scroll.innerHTML = _mediaCfgRowsHTML(_mediaCfgData);
}

async function _mediaCfgPost(target, path, value) {
  const ok = await saveConfigValue(target, path, value);
  if (ok && target === 'media') {
    document.dispatchEvent(new CustomEvent('media.config', {detail:{path}}));
  }
  return ok;
}
async function _mediaCfgSave(target, path, value) {
  await _mediaCfgPost(target, path, value);
  await _mediaCfgRefresh(target);
}

async function mediaCfgListToggle(input) {
  const data = _mediaCfgData.media;
  const current = data.recommendations.lists;
  const all = (data._options['recommendations.lists'] || []).filter(name => name !== 'all');
  const selected = !Array.isArray(current) || current.includes('all') ? all : current;
  const lists = input.value === 'all' ? (input.checked ? ['all'] : [])
    : input.checked ? [...new Set([...selected, input.value])] : selected.filter(name => name !== input.value);
  _mediaCfgPop.querySelectorAll('input').forEach(el => { el.disabled = true; });
  await _mediaCfgSave('media', 'recommendations.lists', lists);
}

function mediaCfgEdit(row) {
  const target = row.dataset.target;
  const path = row.dataset.path;
  const data = target === 'library' ? _librarySettings : _mediaCfgData[target];
  const cur = _cfgGet(data, path);
  const enumOpts = _cfgEnumOpts(data, path);
  const spanId = 'dp-val-' + path.replace(/\./g, '-');
  const span = document.getElementById(spanId);
  if (!span || span.dataset.editing) return;

  // multi-choice: array value + options → chip toggles, saved per click
  if (enumOpts && enumOpts.length && Array.isArray(cur)) {
    const wrap = document.createElement('span');
    wrap.id = spanId; wrap.dataset.editing = '1';
    wrap.style.cssText = 'margin-left:auto;display:flex;gap:3px;flex-wrap:wrap;justify-content:flex-end';
    enumOpts.forEach(o => {
      const b = document.createElement('button');
      b.className = 'media-chip' + (cur.includes(o) ? ' active' : '');
      b.textContent = o;
      b.onclick = async e => {
        e.stopPropagation();
        const ix = cur.indexOf(o);
        if (ix >= 0) cur.splice(ix, 1); else cur.push(o);
        b.classList.toggle('active', cur.includes(o));
        if (!await _mediaCfgPost(target, path, cur)) _mediaCfgRefresh(target);
      };
      wrap.appendChild(b);
    });
    span.replaceWith(wrap);
    return;
  }

  // single-choice: dropdown
  if (enumOpts && enumOpts.length) {
    const sel = document.createElement('select');
    sel.id = spanId; sel.dataset.editing = '1';
    sel.style.cssText = `margin-left:auto;font-family:var(--mono);font-size:10px;
      background:var(--surface2);color:var(--text);border:1px solid var(--border2);
      border-radius:4px;padding:1px 2px;outline:none`;
    enumOpts.forEach(o => {
      const op = document.createElement('option');
      op.value = o; op.textContent = o; op.selected = (o === cur);
      sel.appendChild(op);
    });
    sel.onclick = e => e.stopPropagation();
    sel.onchange = () => _mediaCfgSave(target, path, sel.value);
    sel.onkeydown = e => { e.stopPropagation(); if (e.key === 'Escape') _mediaCfgRefresh(target); };
    span.replaceWith(sel);
    sel.focus();
    return;
  }

  // object value: small textarea for JSON
  const isObj = cur && typeof cur === 'object' && !Array.isArray(cur);
  if (isObj) {
    const ta = document.createElement('textarea');
    ta.id = spanId; ta.dataset.editing = '1';
    ta.value = JSON.stringify(cur, null, 2);
    ta.rows = 4;
    ta.style.cssText = `margin-left:auto;font-family:var(--mono);font-size:10px;width:240px;height:auto;
      background:var(--surface,transparent);border:1px solid var(--border2);color:var(--text);
      border-radius:4px;padding:2px 4px;outline:none;resize:vertical`;
    ta.onclick = e => e.stopPropagation();
    ta.onkeydown = e => {
      e.stopPropagation();
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        try { _mediaCfgSave(target, path, JSON.parse(ta.value)); }
        catch { toast('invalid JSON', 'err'); }
      }
      if (e.key === 'Escape') _mediaCfgRefresh(target);
    };
    span.replaceWith(ta);
    ta.focus();
    return;
  }

  // free value: inline input
  const input = document.createElement('input');
  input.id = spanId; input.dataset.editing = '1';
  input.type = /\.(password|api_key)$/.test(path) ? 'password' : 'text';
  input.value = Array.isArray(cur) ? cur.join(', ') : String(cur);
  input.style.cssText = `margin-left:auto;font-family:var(--mono);font-size:10px;width:120px;
    background:var(--surface,transparent);border:1px solid var(--border2);color:var(--text);
    border-radius:4px;padding:1px 4px;outline:none`;
  input.onclick = e => e.stopPropagation();
  input.onkeydown = e => {
    e.stopPropagation();
    if (e.key === 'Enter') {
      let v = input.value.trim();
      if (Array.isArray(cur)) v = v ? v.split(',').map(x => x.trim()).filter(Boolean) : [];
      else if (typeof cur === 'number') { v = parseFloat(v); if (isNaN(v)) return; }
      _mediaCfgSave(target, path, v);
    }
    if (e.key === 'Escape') _mediaCfgRefresh(target);
  };
  span.replaceWith(input);
  input.focus(); input.select();
}

async function mediaOpenConfig(id, section = '') {
  if (_mediaCfgPop) { _mediaCloseConfig(); return; }
  if (typeof closeDevicePicker === 'function') closeDevicePicker();
  const tileEl = tileRefs[id] || document.getElementById(`${id}-view`);
  if (!tileEl) return;

  _mediaCfgSection = section;
  _mediaCfgTileId = id;
  try { await _mediaEnsureCfg(); }
  catch(e) { _mediaCfgData = {}; }

  const rect = tileEl.getBoundingClientRect();
  const pop = _mediaPopover(tileEl, _mediaCfgRowsHTML(_mediaCfgData), {
    top:rect.top + 44, onClose:() => { _mediaCfgPop = null; _mediaCfgData = null; }
  });
  _mediaCfgPop = pop;
}
function _mediaCloseConfig() {
  _mediaCfgPop?.closePopover();
}


function _chipGroup(id, key, opts) {
  const active = _mediaActionState(id).filters[key] || [];
  return `<div class="media-chip-group">` +
    opts.map(o => `<button class="media-chip ${active.includes(o.v)?'active':''}"
      onclick="mediaToggleFilter('${id}','${key}','${o.v}')">${o.l}</button>`).join('') +
    `</div>`;
}
function mediaToggleFilter(id, key, val) {
  const st = _mediaActionState(id); st.filters[key] = st.filters[key] || [];
  const i = st.filters[key].indexOf(val);
  if (i >= 0) st.filters[key].splice(i,1); else st.filters[key].push(val);
  _mediaSavePrefs(id);
  _mediaRenderPick(id);
}
function _mediaSavePrefs(id) {
  const st = _mediaActionState(id);
  try { localStorage.setItem('mediaPrefs', JSON.stringify({ quality: st.filters.quality })); } catch(e) {}
}


let _mediaActionResources = null;
let _mediaActionsFetching = false;

async function _mediaRefreshActions() {
  if (_mediaActionsFetching || document.hidden) return;
  _mediaActionsFetching = true;
  try {
    const response = await fetch('/api/config?target=media/watchlist,media/existing');
    if (!response.ok) return;
    const data = await response.json();
    _mediaActionResources = {
      watchlist:Object.values(data['media/watchlist'] || {}),
      existing:new Set((data['media/existing']?.titles || []).map(_mediaTitleKey)),
    };
    document.querySelectorAll('[data-media-actions]').forEach(el => {
      const {mediaActions:id, scope, action} = el.dataset;
      const info = _mediaActionInfo(id, scope);
      if (info) el.outerHTML = _mediaInfoActionHTML(id, {...info, action}, scope);
    });
  } catch (_) {} finally { _mediaActionsFetching = false; }
}

function _mediaWatchlistItem(info) {
  const ids = {...(info.ids || {})};
  if (info.tmdb_id) ids.tmdb = String(info.tmdb_id);
  if (info.imdb_id) ids.imdb = String(info.imdb_id);
  if (info.id) ids[info.provider === 'cinemeta' ? 'imdb' : info.provider || 'tmdb'] = String(info.id);
  return {title:info.title, year:info.year,
    type:['tv', 'show', 'series'].includes(info.type) ? 'tv' : 'movie', ids};
}

function _mediaInfoActionHTML(id, d, scope='media') {
  const item = _mediaWatchlistItem(d);
  const existing = d.action === 'play' || _mediaActionResources?.existing.has(_mediaTitleKey(d.title));
  const watchlisted = _mediaActionResources?.watchlist.some(row => row.type === item.type && (
    Object.entries(item.ids).some(([provider, value]) => String(row.ids?.[provider]) === String(value)) ||
    (_mediaTitleKey(row.title) === _mediaTitleKey(item.title) && String(row.year || '') === String(item.year || ''))
  ));
  const play = scope === 'recommendation' ? '_mediaRecPlay' : scope === 'library' ? 'libPlay' : 'mediaPlay';
  let primary;
  if (existing) primary = `<button class="media-action-btn" onclick="${play}('${id}')"
    style="background:rgba(79,142,247,0.18);border-color:rgba(79,142,247,0.55);color:var(--accent);width:auto;display:inline-flex;align-items:center;gap:6px;margin:0;padding:6px 12px;font-size:10px;font-weight:700">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>
    Play
  </button>`;
  const st = _mediaActionState(id);
  const isQueued = st?.view === 'queued';
  if (!existing && isQueued) {
    primary = `<button class="media-action-btn" id="${id}-find-btn" disabled
      style="opacity:1;background:rgba(52,209,122,0.1);border-color:rgba(52,209,122,0.35);color:var(--green);width:auto;display:inline-flex;align-items:center;gap:6px;margin:0;padding:6px 12px;font-size:10px;font-weight:700">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> queued
    </button>`;
  }
  if (!primary) primary = `<button class="media-action-btn" id="${id}-find-btn" onclick="mediaFindTorrent('${id}','${scope}')"
    style="background:rgba(79,142,247,0.18);border-color:rgba(79,142,247,0.55);color:var(--accent);width:auto;display:inline-flex;align-items:center;gap:4px;margin:0;padding:4px 8px;font-size:10px;font-weight:700">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    Torrent
  </button>`;
  const disabled = existing || watchlisted || isQueued;
  const title = existing ? 'Already in library' : watchlisted ? 'Already in watchlist' : 'Add to watchlist';
  return `<span data-media-actions="${id}" data-scope="${scope}" data-action="${d.action || 'find'}" style="display:inline-flex;align-items:center;gap:4px;flex-wrap:nowrap">
  ${primary}
  <button class="media-icon-btn accent media-watchlist-btn" onclick="mediaAddWatchlist(event,'${id}','${scope}')"
    ${disabled ? 'disabled' : ''} title="${title}" aria-label="${title}" style="width:26px;height:26px">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
  </button></span>`;
}

function _mediaActionInfo(id, scope) {
  const state = _mediaActionState(id);
  const entry = scope === 'library' ? state.browseDetail : null;
  if (entry) return {...entry.info,
    title:entry.info?.title || entry.name.replace(/\s*-\s*\d{4}$/, ''),
    year:entry.info?.year || entry.name.match(/\s*-\s*(\d{4})$/)?.[1],
    type:entry.info?.type || state.libraries?.find(l => l.name === state.browseLibrary)?.type};
  return scope === 'recommendation' ? state._recCur : state.info;
}

async function mediaAddWatchlist(event, id, scope) {
  const button = event.currentTarget;
  const info = _mediaActionInfo(id, scope);
  if (!info?.title) return;
  const args = ['library', 'watchlist', 'add', JSON.stringify(_mediaWatchlistItem(info))];
  button.disabled = true;
  const result = await runOp(args);
  if (result?.ok) {
    button.title = 'Added to watchlist';
    button.setAttribute('aria-label', button.title);
    button.style.color = 'var(--green)';
    toast('added to watchlist', 'ok');
  } else {
    button.disabled = false;
    toast(result?.stderr || 'watchlist add failed', 'err');
  }
}

function mediaPickSeason(id, scope, number) {
  if (scope === 'torrent') {
    const st = _mediaActionState(id);
    if (st._torrentScope === 'recommendation') st._recSeason = number;
    else st._season = number;
    mediaFindTorrent(id, st._torrentScope, number);
    return;
  }
  if (scope === 'library') librarySelectSeason(id, number);
  else if (scope === 'recommendation') { _mediaActionState(id)._recSeason = number; _mediaRenderRec(id); }
  else mediaSelectSeason(id, number);
  document.getElementById(`${id}-view`)?.querySelector('.media-chip[aria-pressed="true"]')?.focus({preventScroll:true});
}

const _mediaSeasonGroups = new Set();
const _mediaSeasonResize = new ResizeObserver(entries => entries.forEach(({target}) => _mediaFitSeasons(target)));

function _mediaFitSeasons(group) {
  const buttons = [...group.querySelectorAll('[data-season]')];
  const more = group.querySelector('[data-season-more]');
  buttons.forEach(button => button.hidden = false);
  if (group.dataset.expanded === 'true') { more.hidden = false; return; }
  more.hidden = true;
  if (!group.clientWidth) return;
  const rows = [...new Set(buttons.map(button => button.offsetTop))];
  if (rows.length <= 3) return;
  more.hidden = false;
  for (const button of [...buttons].reverse()) {
    if (more.offsetTop <= rows[2]) break;
    if (button.getAttribute('aria-pressed') !== 'true') button.hidden = true;
  }
}

function mediaToggleSeasons(button, id) {
  const group = button.parentElement;
  const expanded = group.dataset.expanded !== 'true';
  _mediaActionState(id)._seasonsExpanded = expanded;
  group.dataset.expanded = String(expanded);
  button.setAttribute('aria-expanded', String(expanded));
  button.title = expanded ? 'Show fewer seasons' : 'Show all seasons';
  _mediaFitSeasons(group);
  // Keep a collapse control available while expanded.
  button.hidden = false;
}

function _mediaSeasonChipsHTML(seasons, selected, id, scope) {
  if (!seasons.length) return '';
  const expanded = !!_mediaActionState(id)._seasonsExpanded;
  requestAnimationFrame(() => {
    for (const group of _mediaSeasonGroups) {
      if (!group.isConnected) { _mediaSeasonResize.unobserve(group); _mediaSeasonGroups.delete(group); }
    }
    document.querySelectorAll('.media-season-chips').forEach(group => {
      if (!_mediaSeasonGroups.has(group)) {
        _mediaSeasonGroups.add(group);
        _mediaSeasonResize.observe(group);
      }
      _mediaFitSeasons(group);
    });
  });
  return `<div class="media-chip-group media-season-chips" role="group" aria-label="Seasons" data-expanded="${expanded}" style="margin-top:10px">
    ${seasons.map(s => `<button class="media-chip${s.number === selected ? ' active' : ''}" data-season="${s.number}"
      aria-pressed="${s.number === selected}" onclick="mediaPickSeason('${id}','${scope}',${s.number})">Season ${s.number}</button>`).join('')}
    <button class="media-chip" data-season-more aria-label="Toggle all seasons" aria-expanded="${expanded}"
      title="${expanded ? 'Show fewer seasons' : 'Show all seasons'}" onclick="mediaToggleSeasons(this,'${id}')" hidden>…</button>
  </div>`;
}

function _mediaSeasonDetailsHTML(seasons, selected, id, scope) {
  if (!seasons.length) return '';
  const season = seasons.find(s => s.number === selected) || seasons[0];
  const episodes = Array.isArray(season.episodes) ? season.episodes : [];
  return `
    <div style="max-width:520px">
    ${_mediaSeasonChipsHTML(seasons, season.number, id, scope)}
    <div>
      ${episodes.map(ep => `<div class="media-row" style="cursor:default">
        <span style="font-size:9px;color:var(--text3);font-family:var(--mono);flex-shrink:0">E${ep.number}</span>
        <span style="font-size:11px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${ep.name || `Episode ${ep.number}`}</span>
        ${ep.air_date?`<span style="font-size:9px;color:var(--text3);font-family:var(--mono);flex-shrink:0">${ep.air_date}</span>`:''}
      </div>`).join('') || `<div class="muted" style="font-size:10px">no episode details</div>`}
    </div></div>`;
}


function mediaPlay(id) {
  const st = _mediaActionState(id); if (!st.info?.title) return;
  const args = ['player', 'play-video', st.info.title];
  if (st.info.year) args.push('--year', String(st.info.year));
  const player = typeof _activeMediaPlayer === 'function' ? _activeMediaPlayer() : '';
  if (player) args.push('--player', player);
  runOp(args);
}
async function mediaFindTorrent(id, scope = 'media', season) {
  const info = _mediaActionInfo(id, scope); if (!info?.title) return;
  const st = _mediaActionState(id), request = st._torrentRequest = {};
  const view = document.getElementById(`${id}-view`);
  if (st.view !== 'pick') {
    st._torrentScroll = [view?.scrollTop || 0, view?.querySelector('[id$="-browse-detail"]')?.scrollTop || 0];
  }
  st._torrentScope = scope;
  const seasons = Array.isArray(info.season_details) ? info.season_details : [];
  const selected = season ?? (scope === 'recommendation' ? st._recSeason : st._season);
  st._torrentSeason = info.type === 'tv' || info.type === 'show'
    ? seasons.find(s => s.number === selected)?.number ?? seasons[0]?.number : undefined;
  const current = () => _mediaActionState(id) === st && st._torrentRequest === request && st.view === 'pick';
  clearInterval(st._rotT);
  clearTimeout(st._idleT);
  clearTimeout(st._retT);
  clearTimeout(st.browseTimer);
  st._torrentEntry = st.browseDetail;
  if (!st.filters) {
    try { st.filters = JSON.parse(localStorage.getItem('mediaPrefs') || '{}'); } catch (_) { st.filters = {}; }
  }
  st.info = {...info, type:info.type === 'show' || info.type === 'tv' ? 'tv' : 'movie',
    genres:Array.isArray(info.genres) ? info.genres.join(', ') : info.genres || ''};
  st.torrents = []; st.searching = true; st.torrentError = ''; st.view = 'pick';
  _mediaRenderPick(id);
  try {
    const selection = st._torrentSeason == null ? '' : `S${String(st._torrentSeason).padStart(2,'0')}`;
    const params = new URLSearchParams({query:[info.title, info.year, selection].filter(Boolean).join(' ')});
    const response = await fetch(`/api/media/torrents?${params}`);
    if (!response.ok) throw new Error('Torrent search failed');
    const data = await response.json();
    if (!current()) return;
    st.torrents = data.results;
    if (Object.keys(data.errors).length) st.torrentError = 'Some indexers failed; results may be incomplete.';
  } catch (_) {
    if (!current()) return;
    st.torrentError = 'Torrent search failed';
  }
  if (!current()) return;
  st.searching = false;
  _mediaRenderPick(id);
}

function _mediaTorrentResultsHTML(id) {
  const st = _mediaActionState(id);
  const chips = _chipGroup(id,'quality',[{v:'720',l:'720p'},{v:'1080',l:'1080p'},{v:'2160',l:'4K'}])
    + (st.torrentError ? `<div class="media-torrent-status">${st.torrentError}</div>` : '');
  const qf = st.filters.quality || [];
  const rows = qf.length ? st.torrents.filter(r => qf.some(q => r.title.includes(q))) : st.torrents;
  if (!st.torrents.length) return chips + `<div class="media-torrent-status">${st.searching ? 'Searching indexers…' : 'no results found'}</div>`;
  if (!rows.length) return chips + `<div class="media-torrent-status">no results for selected quality</div>`;
  return chips +
    `<div style="font-size:9px;color:var(--text3);font-family:var(--mono);text-transform:uppercase;
       letter-spacing:0.06em;margin-bottom:6px">${rows.length} result${rows.length!==1?'s':''}</div>` +
    rows.map((t,i) => `
    <div class="media-row">
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px">${t.title}</div>
        <div style="display:flex;align-items:center;gap:8px">
          <span style="font-size:10px;font-family:var(--mono);color:var(--green)">${t.seeds}S</span>
          <span style="font-size:10px;font-family:var(--mono);color:var(--text3)">${t.size}</span>
          ${t.quality?`<span style="font-size:9px;color:var(--amber)">★</span>`:''}
          <span style="font-size:9px;color:var(--text3)">${t.indexer}</span>
        </div>
      </div>
      <button class="media-icon-btn accent" title="download" onclick="mediaPick('${id}',${i})">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      </button>
    </div>`).join('');
}

function _mediaTorrentTitleHTML(id) {
  const st = _mediaActionState(id), d = st.info || {};
  const esc = value => String(value || '').replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  const title = esc(d.title || 'Selected title');
  const poster = d.poster || (d.poster_path ? `https://image.tmdb.org/t/p/w185${d.poster_path}` : '');
  const type = d.type === 'tv' || d.type === 'show' ? 'TV' : d.type === 'movie' ? 'Movie' : '';
  const meta = [d.year, type, d.rating ? `★ ${d.rating}` : ''].filter(Boolean).join(' · ');
  const selection = st._torrentSeason == null ? '' : `Season ${st._torrentSeason}`;
  return `<div style="display:flex;gap:10px;align-items:center;padding:2px 0 10px;margin-bottom:8px;border-bottom:1px solid var(--border2);min-width:0">
    ${poster
      ? `<img src="${esc(poster)}" loading="lazy" style="width:40px;height:60px;object-fit:cover;border-radius:4px;flex-shrink:0;background:var(--surface2)">`
      : `<div style="width:40px;height:60px;border-radius:4px;flex-shrink:0;background:var(--surface2)"></div>`}
    <div style="min-width:0;flex:1">
      <div style="font-size:12px;font-weight:600;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${title}</div>
      ${meta ? `<div style="font-size:9px;color:var(--text3);font-family:var(--mono);margin-top:3px">${esc(meta)}</div>` : ''}
      ${selection ? `<div style="font-size:11px;color:var(--accent);margin-top:4px;overflow-wrap:anywhere">${esc(selection)}</div>` : ''}
    </div>
  </div>`;
}

function _mediaRenderPick(id) {
  const st = _mediaActionState(id);
  clearTimeout(st._retT);
  const el = document.getElementById(`${id}-view`); if (!el) return;
  const back = `<button class="media-icon-btn" onclick="mediaTorrentBack('${id}')" title="back" style="margin:8px 0 10px">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
  </button>`;
  const seasons = st._torrentSeason == null ? [] : st.info?.season_details || [];
  el.innerHTML = `<div class="media-torrent-view">${back}${_mediaTorrentTitleHTML(id)}${_mediaSeasonChipsHTML(seasons, st._torrentSeason, id, 'torrent')}${_mediaTorrentResultsHTML(id)}</div>`;
}
function mediaTorrentBack(id, queued = false) {
  const st = _mediaActionState(id);
  clearTimeout(st._retT);
  st.view = queued ? 'queued' : 'info';
  if (st.onTorrentBack) st.onTorrentBack(queued);
  else if (typeof _libraryState !== 'undefined' && _libraryState[id]) { _libBrowseResetTimer(id); libraryRender(id); }
  else _mediaRenderInfo(id);
  const view = document.getElementById(`${id}-view`);
  if (view && st._torrentScroll) {
    view.scrollTop = st._torrentScroll[0];
    const detail = view.querySelector('[id$="-browse-detail"]');
    if (detail) detail.scrollTop = st._torrentScroll[1];
  }
}
async function mediaPick(id, i) {
  const st=_mediaActionState(id); const qf=st.filters.quality||[];
  const rows=qf.length?st.torrents.filter(r=>qf.some(q=>r.title.includes(q))):st.torrents;
  const t=rows[i]; if (!t?.url) return;
  const inf=st.info||{};
  const result = await runOp(['torrent','add',t.url,'--type',inf.type||'movie','--title',inf.title||'',
         '--label',(inf.title||'') + (inf.year ? ` - ${inf.year}` : ''),
         '--lang',inf.lang||'','--genres',inf.genres||'','--year',String(inf.year||'')]);
  if (_mediaActionState(id) !== st || st.info !== inf || st.view !== 'pick') return;
  if (result.ok) mediaTorrentBack(id, true);
  else toast(result.stderr || 'Torrent add failed', 'err');
}


function _renderMediaDetailHTML({title, year, type, rating, votes, genres, poster, overview, actions, loading,
                                 seasons, seasonDetails, selectedSeason, seasonScope, tileId, expanded = false}) {
  const posterSize = expanded ? 'width:180px;height:270px' : 'width:96px;height:144px';
  const posterHTML = poster
    ? `<img src="${poster}" alt="" style="${posterSize};object-fit:cover;border-radius:8px;flex-shrink:0;border:1px solid var(--border)">`
    : `<div style="${posterSize};border-radius:8px;flex-shrink:0;border:1px solid var(--border);background:var(--surface2)"></div>`;
  const yearType = [year, type ? (type === 'show' || type === 'tv' ? 'TV' : 'Movie') : null].filter(Boolean).join('  ·  ');
  const genreStr = Array.isArray(genres) ? genres.join(', ') : (genres || '');
  const overviewHTML = overview ? `<div class="media-overview" style="font-size:${expanded ? 12 : 11}px;color:var(--text2);line-height:1.55;margin-bottom:6px;${expanded ? 'margin-top:14px;max-width:60ch' : ''}">${overview}</div>`
    : loading ? `<div class="muted" style="font-size:10px;margin-bottom:10px">fetching details…</div>` : '';
  return `
    <div class="media-detail-hero" style="display:flex;gap:${expanded ? 20 : 12}px;margin-bottom:10px">
      ${posterHTML}
      <div style="min-width:0;flex:1">
        <div style="font-size:${expanded ? 24 : 14}px;font-weight:700;line-height:1.2;margin-bottom:3px;word-break:break-word">${title}</div>
        ${yearType ? `<div style="font-size:10px;color:var(--text3);font-family:var(--mono);margin-bottom:5px">${yearType}</div>` : ''}
        ${seasons ? `<div style="font-size:10px;color:var(--text3);font-family:var(--mono);margin-top:-3px;margin-bottom:5px">${seasons} season${seasons===1?'':'s'}</div>` : ''}
        ${rating ? `<div style="display:flex;align-items:center;gap:8px;font-size:11px;margin-bottom:3px">
          <span style="color:var(--amber)">★ ${rating}</span>
          ${votes ? `<span style="color:var(--text3);font-family:var(--mono);font-size:10px">${Number(votes).toLocaleString()}</span>` : ''}</div>` : ''}
        ${genreStr ? `<div style="font-size:10px;color:var(--text3);margin-bottom:10px">${genreStr}</div>` : ''}
        ${actions || ''}
        ${expanded ? overviewHTML : ''}
      </div>
    </div>
    ${expanded ? '' : overviewHTML}
    ${seasonScope ? _mediaSeasonDetailsHTML(seasonDetails || [], selectedSeason, tileId, seasonScope) : ''}`;
}

// Recommendations component: embedded by Media Manager or added as its own tile.
const _mediaRecommState = {};

TILES['media-recomm'] = {
  html: tile => mediaRecommHTML(tile.id),
  bind: id => mediaRecommBind(id),
};

function mediaRecommHTML(id, embedded = false) {
  const title = `recommended<span id="${id}-list"></span>`;
  const actions = `<button class="media-icon-btn" onclick="mediaOpenConfig('${id}','recommendations')" title="Settings" aria-label="Recommendation settings">⚙</button>
    <button class="media-icon-btn" onclick="_mediaRecStep('${id}',-1)" title="prev">‹</button>
    <button class="media-icon-btn" onclick="_mediaRecStep('${id}',1)" title="next">›</button>`;
  const header = embedded
    ? `<div style="display:flex;align-items:center;justify-content:space-between;height:28px;margin-bottom:8px">
        <span style="font-size:8px;text-transform:uppercase;letter-spacing:.08em;color:var(--text3)">${title}</span>
        <span style="display:flex;gap:4px">${actions}</span></div>`
    : _tileHeader(id, "media", actions);
  return `${header}<div id="${id}-view" style="height:calc(100% - 38px);overflow-y:auto;${embedded ? '' : 'padding:0 14px 14px;'}"></div>`;
}

function mediaRecommBind(id) {
  _mediaRecommStop(id);
  clearTimeout(_mediaRecommState[id]?._retT);
  _mediaRecommState[id] = {
    view:'entry',
    onTorrentBack(queued) {
      const st = _mediaRecommState[id];
      st.view = queued ? 'queued' : 'entry';
      _mediaStartRotation(id);
    },
  };
  _mediaStartRotation(id);
}

function _mediaRecommStop(id) {
  const st = _mediaRecommState[id];
  if (!st) return;
  clearInterval(st._rotT);
  clearTimeout(st._retT);
  st._request = null;
}

function _mediaRecommReload(id) {
  const st = _mediaRecommState[id];
  if (!st) return;
  st._rec = null;
  st._recOrder = null;
  if (st.view !== 'pick') _mediaStartRotation(id);
}

async function _mediaStartRotation(id) {
  const st = _mediaRecommState[id];
  const el = document.getElementById(`${id}-view`);
  if (!st || !el || st.view === 'pick' || el.closest('[hidden]')) return;
  const request = st._request = {};
  const current = () => _mediaRecommState[id] === st && st._request === request
    && el.isConnected && !el.closest('[hidden]') && st.view !== 'pick';
  if (!st._rec?.length) {
    try {
      const response = await fetch('/api/media/recommendations');
      if (!response.ok) throw new Error('Could not load recommendations');
      const data = await response.json();
      if (!current()) return;
      st._rec = data.items || [];
    } catch (_) {
      if (!current()) return;
      el.innerHTML = '<div class="muted">Could not load recommendations</div>';
      return;
    }
    st._recOrder = null;
  }
  if (!current()) return;
  if (!st._recOrder) {
    st._recOrder = st._rec
      .map((it, i) => ({it, k:i + Math.random() * st._rec.length * 0.5}))
      .sort((a, b) => a.k - b.k).map(x => x.it);
    st._recIdx = 0;
  }
  _mediaRenderRec(id);
  clearInterval(st._rotT);
  st._rotT = setInterval(() => {
    if (!el.isConnected || el.closest('[hidden]')) { _mediaRecommStop(id); return; }
    _mediaRecStep(id, 1);
  }, 60000);
}

function _mediaRecStep(id, dir) {
  const st = _mediaRecommState[id];
  const n = st?._recOrder?.length;
  if (!n || st.view === 'pick') return;
  st.view = 'entry';
  st._recIdx = ((st._recIdx || 0) + dir + n) % n;
  _mediaRenderRec(id);
}

function _mediaRenderRec(id) {
  const st = _mediaRecommState[id];
  const el = document.getElementById(`${id}-view`);
  if (!el || !st) return;
  const r = st._recOrder?.[st._recIdx || 0];
  const changed = st._recCur !== r;
  if (changed) delete st._seasonsExpanded;
  st._recCur = r;
  st.info = r;
  const label = document.getElementById(`${id}-list`);
  if (label) label.textContent = r?.list ? ` · ${r.list}` : '';
  if (!r) { el.innerHTML = '<div class="muted">no recommendations yet</div>'; return; }
  const seasons = Array.isArray(r.season_details) ? r.season_details : [];
  if (changed || !seasons.some(s => s.number === st._recSeason)) {
    st._recSeason = seasons.reduce((latest, season) => Math.max(latest, season.number), -1);
  }
  el.innerHTML = _renderMediaDetailHTML({...r, actions:_mediaInfoActionHTML(id, r, 'recommendation'),
    seasonDetails:seasons, selectedSeason:st._recSeason,
    seasonScope:r.type === 'tv' || r.type === 'show' ? 'recommendation' : '', tileId:id});
}

function _mediaRecPlay(id) { mediaPlay(id); }

document.addEventListener('media.config', event => {
  if (event.detail.path.startsWith('recommendations.')) {
    Object.keys(_mediaRecommState).forEach(_mediaRecommReload);
  }
});

['library.import', 'media.recommendations'].forEach(event => {
  onEvent(event, () => Object.keys(_mediaRecommState).forEach(_mediaRecommReload));
});

_mediaRefreshActions();
setInterval(_mediaRefreshActions, 15000);
['web.connected', 'library.watchlist', 'library.sync-watchlist', 'library.import'].forEach(event => {
  onEvent(event, _mediaRefreshActions);
});
