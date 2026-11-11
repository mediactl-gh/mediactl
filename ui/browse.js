// Full-page library shelves; details and actions are provided by library.js/media.js.
(function() {
  const style = document.createElement('style');
  style.textContent = `
    body.browse-layout #tile-grid { display:block;height:100%;min-height:0;zoom:1 !important; }
    .tile-browse { --browse-scale:1; }
    #tile-grid[data-size="small"] .tile-browse { --browse-scale:.9; }
    #tile-grid[data-size="big"] .tile-browse { --browse-scale:1.111111; }
    body.browse-layout .tile-browse { height:100%;border:0;background:transparent; }
    #browse-actions { display:none;align-items:center;gap:6px;margin-left:8px;flex-shrink:0; }
    body.browse-layout #browse-actions { display:flex; }
    #browse-actions .icon-btn { width:32px;height:32px;padding:0; }
    #browse-actions svg { width:20px;height:20px;flex-shrink:0; }
    .browse-shell { display:flex;height:100%;min-height:0; }
    .browse-main { flex:1;min-width:0;overflow-y:auto;padding:calc(8px * var(--browse-scale)) 4px 16px calc(20px * var(--browse-scale));
      display:flex;flex-direction:column;gap:calc(40.04px * var(--browse-scale)); }
    .browse-section { flex-shrink:0; }
    .browse-section h2 { display:flex;align-items:center;gap:8px;font-size:calc(16.8px * var(--browse-scale));font-weight:700;margin:0 0 calc(10px * var(--browse-scale)); }
    .browse-row-actions { display:flex;gap:4px;margin-left:calc(16px * var(--browse-scale)); }
    .media-icon-btn[data-sync-until]:disabled { opacity:.4;cursor:default; }
    .browse-row { display:flex;gap:calc(12px * var(--browse-scale));overflow-x:auto;overscroll-behavior-x:contain;
      scroll-snap-type:x proximity;padding:0 0 calc(8px * var(--browse-scale)); }
    .browse-card { flex:0 0 calc(165.24px * var(--browse-scale));min-width:0;scroll-snap-align:start;border:0;padding:0;
      background:transparent;color:var(--text);font:inherit;text-align:left;cursor:pointer; }
    .browse-thumbnail { display:block;width:100%;aspect-ratio:2/3;object-fit:cover;
      border-radius:var(--radius-lg);background:var(--surface2); }
    .browse-title { display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
      overflow:hidden;font-size:calc(14.4px * var(--browse-scale));font-weight:600;line-height:1.4;margin-top:calc(7px * var(--browse-scale)); }
    .browse-meta { display:block;font-size:calc(12px * var(--browse-scale));color:var(--text3);margin-top:calc(3px * var(--browse-scale)); }
    .browse-card:hover .browse-title, .browse-card[aria-expanded="true"] .browse-title { color:var(--accent); }
    .browse-card[aria-expanded="true"] .browse-thumbnail { outline:2px solid var(--accent);outline-offset:-2px; }
    .browse-detail { position:relative;margin-top:calc(16px * var(--browse-scale));padding:calc(20px * var(--browse-scale));
      background:var(--surface);border:1px solid var(--border2);border-radius:var(--radius-lg); }
    .browse-detail[hidden] { display:none; }
    .browse-detail-close { position:absolute;left:calc(20px * var(--browse-scale));top:calc(12px * var(--browse-scale));
      width:calc(32px * var(--browse-scale));height:calc(32px * var(--browse-scale));z-index:1; }
    .browse-detail-close svg { width:20px;height:20px; }
    .browse-detail-view { min-width:0;padding-top:24px;zoom:calc(1.2 * var(--browse-scale)); }
    .browse-main .muted { font-size:calc(14.4px * var(--browse-scale)); }
    @media(max-width:600px) {
      .browse-card { flex-basis:calc(151.47px * var(--browse-scale)); }
      .browse-detail .media-detail-hero { flex-direction:column; }
    }
  `;
  document.head.appendChild(style);
})();

const _browseState = {};
const _browseEscape = value => String(value ?? '').replace(/&/g, '&amp;')
  .replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

TILES['browse'] = {
  html(tile) {
    const id = tile.id;
    return `<div class="browse-shell">
      <main id="${id}-shelves" class="browse-main"><div class="muted">Loading libraries…</div></main>
    </div>
    <section id="${id}-detail-panel" class="browse-detail" aria-label="Media details" hidden>
      <button class="icon-btn browse-detail-close" onclick="browseClose('${id}')" title="Close details" aria-label="Close details"><i data-lucide="x"></i></button>
      <div id="${id}-view" class="browse-detail-view"></div>
    </section>`;
  },
  bind(id) {
    const st = _ls(id);
    st._visible = true;
    st.mode = 'browse';
    st.keepOpen = true;
    st.renderView = () => browseRenderDetail(id);
    st.refreshView = evt => browseRefresh(id, evt);
    st.renderActive = items => browseUpdateActive(id, items);
    _browseState[id] = {rows:[], loading:false, returnFocus:null, returnScroll:0,
      panel:document.getElementById(`${id}-detail-panel`)};
    document.getElementById('browse-actions').innerHTML = `
      <button class="icon-btn active" onclick="browseHome('${id}')" title="Home" aria-label="Home"><i data-lucide="house"></i></button>
      <button class="icon-btn" onclick="libraryModal('${id}')" title="Add library" aria-label="Add library"><i data-lucide="folder-plus"></i></button>
      <button class="icon-btn" onclick="libraryModal('${id}',null,'collection')" title="Add collection" aria-label="Add collection"><i data-lucide="list-plus"></i></button>`;
    _browseState[id].panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.stopPropagation(); browseClose(id); }
    });
    lucide.createIcons();
    browseLoad(id);
    libraryRefreshActive(id);
  },
  unbind(id) {
    const st = _libraryState[id];
    clearTimeout(st?._retT);
    clearTimeout(st?.browseTimer);
    delete _libraryState[id];
    delete _browseState[id];
    const actions = document.getElementById('browse-actions');
    actions.replaceChildren();
  },
};

async function browseLoad(id) {
  const st = _browseState[id];
  if (!st || st.loading) return;
  st.loading = true;
  try {
    const response = await fetch('/api/library');
    if (!response.ok) throw new Error('Could not load libraries');
    const libraries = await response.json();
    if (_browseState[id] !== st) return;
    const activeLibrary = {name:'Active downloads', role:'file', path:''};
    const recentLibrary = libraries.find(lib => lib.name.toLowerCase() === 'recently added');
    _ls(id).libraries = [activeLibrary, ...libraries];
    const previous = st.rows;
    st.rows = [activeLibrary, ...(recentLibrary ? [recentLibrary] : []),
      ...libraries.filter(lib => lib !== recentLibrary && ((lib.role || 'library') === 'library' || lib.role === 'collection'))]
      .map(lib => {
        const old = previous.find(row => row.library.name === lib.name && row.library.path === lib.path);
        return {library:lib, items:[], total:0, loading:false, error:'',
          active:lib === activeLibrary, hideEmpty:lib === activeLibrary || lib === recentLibrary,
          limit:Math.max(_LIB_PAGE_SIZE, old?.items.length || 0)};
      });
    const main = document.getElementById(`${id}-shelves`), scrollTop = main.scrollTop;
    browseRenderRows(id);
    browseUpdateActive(id, _ls(id).active || []);
    await Promise.all(st.rows.map((_, index) => browseLoadRow(id, index)));
    if (_browseState[id] === st) main.scrollTop = scrollTop;
  } catch (error) {
    if (_browseState[id] !== st) return;
    browseClose(id);
    document.getElementById(`${id}-shelves`).innerHTML = `<div class="muted">${_browseEscape(error.message)}</div>`;
  } finally {
    st.loading = false;
  }
}

function browseRenderRows(id) {
  const st = _browseState[id], main = document.getElementById(`${id}-shelves`);
  if (!st || !main) return;
  const positions = new Map([...main.querySelectorAll('.browse-row')]
    .map(row => [row.dataset.library, row.scrollLeft]));
  main.after(st.panel);
  main.innerHTML = st.rows.length ? st.rows.map((row, index) => `<section class="browse-section" ${row.hideEmpty && !row.items.length ? 'hidden' : ''}>
    <h2>${_browseEscape(row.library.name)}${!row.hideEmpty ? `<span class="browse-row-actions">
      <button class="media-icon-btn" onclick="libraryModal('${id}',_browseState['${id}'].rows[${index}].library.name)" title="Edit" aria-label="Edit ${_browseEscape(row.library.name)}"><i data-lucide="pencil" style="width:14px;height:14px"></i></button>
      <button class="media-icon-btn" onclick="librarySyncBrowse('${id}',_browseState['${id}'].rows[${index}].library.name,this)" data-sync-until="${_ls(id).syncUntil?.[row.library.name] || 0}" ${Date.now() < (_ls(id).syncUntil?.[row.library.name] || 0) ? 'disabled' : ''} title="Sync library" aria-label="Sync ${_browseEscape(row.library.name)}"><i data-lucide="refresh-cw" style="width:14px;height:14px"></i></button>
    </span>` : ''}</h2>
    <div id="${id}-row-${index}" class="browse-row" data-library="${_browseEscape(row.library.name)}"
      aria-label="${_browseEscape(row.library.name)}"><div class="muted">Loading…</div></div>
  </section>`).join('') : '<div class="muted">No libraries configured</div>';
  st.rows.forEach((row, index) => {
    row.scrollLeft = positions.get(row.library.name) || 0;
    const track = document.getElementById(`${id}-row-${index}`);
    track.addEventListener('scroll', () => {
      if (track.scrollLeft + track.clientWidth >= track.scrollWidth - 300 && row.items.length < row.total)
        browseLoadRow(id, index);
    });
  });
  browseRenderDetail(id);
  lucide.createIcons();
}

function browseRefresh(id, evt) {
  if (!evt) return browseLoad(id);
  if (evt.hint === 'media.fetch' || (evt.hint === 'library.sync-lists' && evt.event !== 'done')) return;
  _browseState[id]?.rows.forEach((row, index) => {
    if (evt.library && row.library.name !== evt.library) return;
    browseLoadRow(id, index, true);
  });
}

async function browseLoadRow(id, index, refresh = false) {
  const st = _browseState[id], row = st?.rows[index];
  if (!row || row.active) return;
  if (row.loading) { if (refresh) row.refreshPending = true; return; }
  row.loading = true;
  let changed = !refresh;
  try {
    const params = new URLSearchParams({path:row.library.path, offset:refresh ? 0 : row.items.length,
      limit:refresh ? Math.max(row.limit, row.items.length) : row.limit});
    const response = await fetch(`/api/media/browse?${params}`);
    if (!response.ok) throw new Error(response.status === 404 ? 'Not synced yet' : 'Could not load media');
    const data = await response.json();
    if (_browseState[id] !== st || st.rows[index] !== row) return;
    const items = refresh ? (data.items || []) : row.items.concat(data.items || []);
    changed ||= JSON.stringify(items) !== JSON.stringify(row.items);
    row.items = items;
    row.total = data.total || row.items.length;
    row.error = '';
  } catch (error) {
    row.error = error.message;
  } finally {
    row.loading = false;
  }
  if (_browseState[id] !== st || st.rows[index] !== row) return;
  if (changed) browseRenderRow(id, index, refresh);
  if (row.refreshPending) { row.refreshPending = false; browseLoadRow(id, index, true); }
}

function browseUpdateActive(id, items) {
  const st = _browseState[id], index = st?.rows.findIndex(row => row.active);
  if (index === undefined || index < 0) return;
  const row = st.rows[index], previous = row.items;
  row.items = items.filter(item => item.progress < 100 && item.state !== 'error').map(item => {
    const name = item.label || item.name || '';
    const old = previous.find(entry => entry.name === name && entry.path === item.path);
    return {name, path:item.path, info:old?.info || {poster:item.poster}, action:'find', download:item};
  });
  row.total = row.items.length;
  browseRenderRow(id, index);
}

function browseRenderRow(id, index, keepDetails = false) {
  const row = _browseState[id]?.rows[index];
  const track = document.getElementById(`${id}-row-${index}`);
  if (!row || !track) return;
  const main = document.getElementById(`${id}-shelves`), scrollTop = main.scrollTop;
  track.closest('.browse-section').hidden = row.hideEmpty && !row.items.length;
  const position = row.scrollLeft || track.scrollLeft;
  row.scrollLeft = 0;
  track.innerHTML = row.items.length ? row.items.map((entry, itemIndex) => {
    const info = entry.info || {}, title = info.title || entry.name.replace(/\s*-\s*\d{4}$/, '');
    const image = info.poster || info.backdrop;
    const meta = (entry.download
      ? [`${Math.max(0, entry.download.progress).toFixed(1)}%`, _libFmtSpeed(entry.download.dl_speed)]
      : [info.year, info.rating ? `★ ${info.rating}` : '']).filter(Boolean).join(' · ');
    return `<button id="${id}-card-${index}-${itemIndex}" class="browse-card" aria-expanded="false" aria-controls="${id}-detail-panel" onclick="browseOpen('${id}',${index},${itemIndex},this)">
      ${image ? `<img class="browse-thumbnail" src="${_browseEscape(image)}" loading="lazy" alt="">`
        : '<span class="browse-thumbnail"></span>'}
      <span class="browse-title">${_browseEscape(title)}</span>
      <span class="browse-meta">${_browseEscape(meta)}</span>
    </button>`;
  }).join('') : `<div class="muted">${_browseEscape(row.error || 'Empty')}</div>`;
  track.scrollLeft = position;
  main.scrollTop = scrollTop;
  const st = _ls(id);
  if (st.browseDetail && st.browseLibrary === row.library.name && st.browsePath === row.library.path) {
    const selected = row.items.findIndex(entry => entry.name === st.browseDetail.name && entry.path === st.browseDetail.path);
    if (selected < 0 && !row.error && !keepDetails) browseClose(id);
    else if (selected >= 0) {
      const card = document.getElementById(`${id}-card-${index}-${selected}`);
      card.setAttribute('aria-expanded', 'true');
      _browseState[id].returnFocus = card.id;
    } else if (keepDetails) _browseState[id].returnFocus = null;
  }
}

function browseOpen(id, rowIndex, itemIndex, button) {
  const browse = _browseState[id], row = browse?.rows[rowIndex];
  if (!row) return;
  if (button.getAttribute('aria-expanded') === 'true') { browseClose(id); return; }
  const main = document.getElementById(`${id}-shelves`), top = button.getBoundingClientRect().top;
  browse.panel.hidden = true;
  main.scrollTop += button.getBoundingClientRect().top - top;
  browse.returnScroll = main.scrollTop;
  browse.returnFocus = button.id;
  const st = _ls(id);
  st._torrentRequest = null;
  st.view = null;
  st.browseLibrary = row.library.name;
  st.browsePath = row.library.path;
  st.browseEntries = row.items;
  st._season = null;
  if (row.active) { st._torrentEntry = row.items[itemIndex]; st.view = 'queued'; }
  libraryBrowseDetail(id, itemIndex);
  browse.panel.querySelector('.browse-detail-close').focus({preventScroll:true});
  browse.panel.scrollIntoView({block:'nearest', behavior:'smooth'});
}

function browseRenderDetail(id) {
  const st = _ls(id), browse = _browseState[id], panel = browse?.panel;
  if (!panel) return;
  const wasOpen = !panel.hidden, open = !!st.browseDetail;
  const main = document.getElementById(`${id}-shelves`);
  main.querySelectorAll('.browse-card[aria-expanded="true"]').forEach(card => card.setAttribute('aria-expanded', 'false'));
  panel.hidden = !open;
  if (!open) {
    main.after(panel);
    if (wasOpen) {
      main.scrollTop = browse.returnScroll;
      document.getElementById(browse.returnFocus)?.focus({preventScroll:true});
    }
    return;
  }
  const rowIndex = browse.rows.findIndex(row => row.library.name === st.browseLibrary && row.library.path === st.browsePath);
  const track = document.getElementById(`${id}-row-${rowIndex}`);
  if (!track) { browseClose(id); return; }
  if (track.nextElementSibling !== panel) track.after(panel);
  const itemIndex = browse.rows[rowIndex].items.findIndex(entry => entry.name === st.browseDetail.name && entry.path === st.browseDetail.path);
  const card = document.getElementById(`${id}-card-${rowIndex}-${itemIndex}`);
  if (card) {
    card.setAttribute('aria-expanded', 'true');
    browse.returnFocus = card.id;
  }
  const view = document.getElementById(`${id}-view`);
  if (st.view === 'pick') _mediaRenderPick(id);
  else view.innerHTML = _libBrowseDetailHTML(id, true);
  lucide.createIcons();
}

function browseClose(id) {
  const st = _ls(id);
  st._torrentRequest = null;
  clearTimeout(st._retT);
  libraryBrowseDetailBack(id);
}

function browseHome(id) {
  browseClose(id);
  document.getElementById(`${id}-shelves`).scrollTop = 0;
}

for (const event of ['library.add', 'library.remove', 'library.delete-entry']) {
  onEvent(event, () => Object.keys(_browseState).forEach(id => browseLoad(id)));
}

onEvent('media.info', evt => {
  Object.keys(_browseState).forEach(id => _browseState[id].rows.forEach((row, index) => {
    if (row.items.some(entry => _mediaKey(entry.info?.title || entry.name) === _mediaKey(evt.title)))
      browseLoadRow(id, index, true);
  }));
});
