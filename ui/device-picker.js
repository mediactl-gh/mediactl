// device-picker.js
//
// Click tile label → popover with device list appears overlaying the tile.
// Fixed-position on <body>, z-index above everything, click outside closes.

function _tileDev(id) {
  const tile = dashboard?.tiles?.find(t => t.id === id);
  return tile?.config?.device || tile?.config?.camera || (tile?.type === 'device' ? tile.config?.name : '') || '';
}

/** Root `.tile` element — prefer `tileRefs` so IDs with `.` etc. never break `querySelector`. */
function _dpTileEl(tileId) {
  if (typeof tileRefs !== 'undefined' && tileRefs[tileId]) return tileRefs[tileId];
  const id = String(tileId);
  if (typeof CSS !== 'undefined' && CSS.escape) {
    try {
      return document.querySelector(`[data-id="${CSS.escape(id)}"]`);
    } catch (_) { /* ignore */ }
  }
  return document.querySelector(`[data-id="${id.replace(/"/g, '\\"')}"]`);
}

let _dpOpen = null;   // tileId
let _dpSnap = null;   // { device, label }
let _dpPop  = null;   // DOM element
let _dpPickerOpts = null;   // optional 3rd-arg opts (e.g. security multi-select)
let _dpModalPickerOpts = null;  // capture for reopen after Add/Edit modal
let _dpSkipDashSave = false;    // multi-select does not touch dashboard tile config

// ── data ────────────────────────────────────────────────────────────────

let _devCache = null, _devCacheTs = 0;

async function _fetchDevices() {
  if (_devCache && Date.now() - _devCacheTs < 5000) return _devCache;
  try { const r = await fetch('/api/config?target=devices'); _devCache = await r.json(); _devCacheTs = Date.now(); }
  catch (_) { _devCache = _devCache || { cameras: [], mic: [], speaker: [], devices: [] }; }
  return _devCache;
}

function _dpEscLine(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}

/** List row: `name - description | source` with the tail grayed (dim). */
function _dpItem(d, current, badge) {
  const nameStr = d.name != null ? String(d.name).trim() : '';
  const descStr = (d.description && String(d.description).trim()) || '';
  const srcStr = (d.source !== undefined && d.source !== null && String(d.source).trim()) || '';

  let primary = nameStr || descStr;
  if (!primary && d.identifier !== undefined && d.identifier !== null && d.identifier !== '')
    primary = String(d.identifier);
  if (!primary) primary = '—';

  const descPart = descStr && descStr !== primary ? descStr : '';
  let dimTail = '';
  if (descPart && srcStr) dimTail = ` - ${descPart} | ${srcStr}`;
  else if (descPart) dimTail = ` - ${descPart}`;
  else if (srcStr) dimTail = ` | ${srcStr}`;

  let lineHtml = _dpEscLine(primary);
  if (dimTail) lineHtml += `<span class="dp-row-dim">${_dpEscLine(dimTail)}</span>`;
  if (badge) lineHtml += ` <span class="dp-meta">${_dpEscLine(badge)}</span>`;

  const sel = (current instanceof Set) ? current.has(d.name) : (d.name === current);
  return { key: d.name, lineHtml, sel };
}

// ── devices.json manual row

function _dpApiKind(pickerType, item) {
  if (item?.kindKey) return item.kindKey;
  if (pickerType === 'camera') return 'cameras';
  if (pickerType === 'audio') return 'mic';
  if (pickerType === 'device') return 'devices';
  if (pickerType === 'tv') return 'tvs';
  if (pickerType === 'lights') return 'lights';
  return 'cameras';
}

function _dpAdvancedSnippet(raw) {
  if (!raw || typeof raw !== 'object') return '';
  const o = { ...raw };
  delete o.name;
  delete o.identifier;
  delete o.source;
  const keys = Object.keys(o);
  if (!keys.length) return '';
  try { return JSON.stringify(o, null, 2); } catch (_) { return ''; }
}

function _dpDetailMsg(detail) {
  if (detail == null) return 'Request failed';
  if (Array.isArray(detail)) return detail.map(x => (x && x.msg) || '').filter(Boolean).join(' ') || 'Request failed';
  return String(detail);
}

async function _dpUnauthorizedModal() {
  closeModal();
  const tip = 'Sign in to change devices.';
  if (typeof _showModal === 'function') {
    _showModal(`<p style="font-size:13px;color:var(--text2)">${tip}</p>
      <div style="display:flex;justify-content:flex-end;margin-top:12px">
        <button class="btn accent" onclick="closeModal()">OK</button></div>`);
  } else alert(tip);
}

function _dpSyncTileRenameDevice(tileId, pickerType, oldName, newName) {
  if (pickerType === 'lights') {
    let changed = false;
    dashboard?.tiles?.forEach(tile => {
      if (tile.type !== 'lights-control' || !Array.isArray(tile.config?.devices)) return;
      if (!tile.config.devices.includes(oldName)) return;
      tile.config.devices = tile.config.devices.map(name => name === oldName ? newName : name);
      changed = true;
    });
    if (typeof lightsDeviceRenamed === 'function') lightsDeviceRenamed(oldName, newName);
    if (changed && typeof saveDashboard === 'function') saveDashboard();
    return;
  }
  const tile = dashboard?.tiles?.find(t => t.id === tileId);
  if (!tile?.config) return;
  if (_tileDev(tileId) !== oldName) return;
  tile.config.device = newName;
  if (_dpPickerOpts?.syncLabel !== false && (!tile.config.label || tile.config.label === oldName)) tile.config.label = newName;
  const el = document.getElementById(`${tileId}-label`);
  if (el) el.textContent = tile.config.label || newName;
  if (typeof saveDashboard === 'function') saveDashboard();
}

function _dpSyncTileRemovedDevice(tileId, devName) {
  const tile = dashboard?.tiles?.find(t => t.id === tileId);
  if (!tile?.config) return;
  if (_tileDev(tileId) !== devName) return;
  tile.config.device = '';
  if (tile.type === 'device') tile.config.name = '';
  if (tile.config.label === devName) tile.config.label = '';
  const el = document.getElementById(`${tileId}-label`);
  if (el) el.textContent = tile.config.label || '—';
  if (typeof saveDashboard === 'function') saveDashboard();
}

async function _dpManualApi(body) {
  return fetch('/api/devices/manual', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
  });
}

async function _dpSubmitDeviceForm(tileId, pickerType, item, nameEl, identEl, sourceEl, advEl, origEl, errEl, goBtn) {
  errEl.textContent = '';

  let advanced = {};
  const rawAdv = advEl.value.trim();
  if (rawAdv) {
    try {
      advanced = JSON.parse(rawAdv);
      if (advanced === null || typeof advanced !== 'object' || Array.isArray(advanced)) {
        errEl.textContent = 'Advanced field must be a JSON object {}.';
        return;
      }
    } catch (_) {
      errEl.textContent = 'Invalid JSON in advanced.';
      return;
    }
  }

  const schema = Array.isArray(_dpModalPickerOpts?.schema) ? _dpModalPickerOpts.schema : [];
  for (const s of schema) {
    if (!s?.key) continue;
    const v = advanced[s.key];
    if (v === undefined || v === null || String(v).trim() === '') {
      errEl.textContent = `Advanced "${s.key}" is required.`;
      return;
    }
    if (Array.isArray(s.values) && s.values.length && !s.values.includes(v)) {
      errEl.textContent = `Advanced "${s.key}" must be one of: ${s.values.join(', ')}.`;
      return;
    }
  }

  const originalName = (origEl.value || '').trim() || null;
  const payload = {
    action: 'upsert',
    kind: _dpApiKind(pickerType, item),
    originalName,
    name: nameEl.value.trim(),
    identifier: String(identEl.value ?? '').trim(),
    source: sourceEl.value.trim(),
    advanced,
  };

  goBtn.disabled = true;
  try {
    const r = await _dpManualApi(payload);
    if (r.status === 401) { await _dpUnauthorizedModal(); return; }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { errEl.textContent = _dpDetailMsg(data.detail); return; }
    const finalName = data.name || nameEl.value.trim();
    if (originalName && finalName && originalName !== finalName)
      _dpSyncTileRenameDevice(tileId, pickerType, originalName, finalName);
    const onUpsert = (!originalName && typeof _dpModalPickerOpts?.onUpsert === 'function')
      ? _dpModalPickerOpts.onUpsert : null;
    closeModal();
    _devCacheTs = 0;
    if (onUpsert) { onUpsert(finalName, payload.kind); return; }
    await openDevicePicker(tileId, pickerType, _dpModalPickerOpts || undefined);
  } catch (e) {
    errEl.textContent = e?.message ? String(e.message) : 'Network error';
  } finally {
    goBtn.disabled = false;
  }
}

function _dpDeleteDeviceConfirm(tileId, pickerType, item, raw, name) {
  if (!name || typeof _showModal !== 'function') return;
  _showModal(`
    <h3>Remove from list</h3>
    <p style="font-size:13px;color:var(--text2);line-height:1.55;margin:0">
      Remove <strong>${_dpEscAttr(name)}</strong> from shared devices? This does not stop remote streams.
    </p>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
      <button type="button" class="btn" id="dp-del-cancel">Cancel</button>
      <button type="button" class="btn accent" id="dp-del-ok" style="background:rgba(180,60,60,.25);border-color:rgba(230,100,100,.4);color:#f88">Remove</button>
    </div>
  `);
  document.getElementById('dp-del-cancel').onclick = () => {
    _dpOpenDeviceFormModal(tileId, pickerType, item, raw);
  };
  document.getElementById('dp-del-ok').onclick = async () => {
    const okBtn = document.getElementById('dp-del-ok');
    okBtn.disabled = true;
    try {
      const r = await _dpManualApi({ action: 'delete', kind: _dpApiKind(pickerType, item), name });
      if (r.status === 401) { await _dpUnauthorizedModal(); return; }
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        _showModal(`<p style="font-size:13px;color:var(--text2)">${_dpEscAttr(_dpDetailMsg(data.detail))}</p>
          <div style="display:flex;justify-content:flex-end;margin-top:12px">
            <button class="btn accent" onclick="closeModal()">OK</button></div>`);
        return;
      }
      _dpSyncTileRemovedDevice(tileId, name);
      closeModal();
      _devCacheTs = 0;
      closeDevicePicker();
      await openDevicePicker(tileId, pickerType, _dpModalPickerOpts || undefined);
    } finally {
      okBtn.disabled = false;
    }
  };
}

function _dpEscAttr(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function _dpEscTa(s) {
  return String(s ?? '').replace(/<\/textarea/gi, '<\\/textarea');
}

function _dpOpenDeviceFormModal(tileId, pickerType, item, raw) {
  if (typeof _showModal !== 'function') {
    alert('Open the dashboard in the main app to edit devices.');
    return;
  }
  _dpModalPickerOpts = _dpPickerOpts;
  // Popover is z-index 9000; modal was 8000 — clicks never reached Add/Save. Close pop first; backdrop is now >9000 too.
  closeDevicePicker();
  const edit = !!raw;
  const title = edit
    ? (pickerType === 'camera' ? 'Edit camera' : pickerType === 'audio' ? 'Edit microphone' : pickerType === 'tv' ? 'Edit TV' : 'Edit device')
    : (pickerType === 'camera' ? 'Add camera' : pickerType === 'audio' ? 'Add microphone' : pickerType === 'tv' ? 'Add TV' : 'Add device');

  const identPh =
    pickerType === 'camera' ? 'index, rtsp://, https://, push://…' :
    pickerType === 'audio'  ? 'index, push://…, or other id' :
    pickerType === 'lights' ? 'Tuya device id' :
    'IP or hostname';

  const nameVal = raw ? _dpEscAttr(raw.name) : '';
  const identVal = raw && raw.identifier !== undefined && raw.identifier !== null
    ? _dpEscAttr(raw.identifier) : '';
  const srcVal = raw && raw.source !== undefined && raw.source !== null
    ? _dpEscAttr(raw.source) : '';
  const schema = Array.isArray(_dpModalPickerOpts?.schema) ? _dpModalPickerOpts.schema : [];
  let advTxt;
  if (raw) {
    advTxt = _dpEscTa(_dpAdvancedSnippet(raw));
  } else if (schema.length) {
    const seed = {};
    schema.forEach(s => { if (s?.key) seed[s.key] = s.default ?? (s.values && s.values[0]) ?? ''; });
    advTxt = _dpEscTa(JSON.stringify(seed, null, 2));
  } else {
    advTxt = '';
  }
  const schemaNote = schema.map(s => {
    if (!s?.key) return '';
    const tail = Array.isArray(s.values) && s.values.length
      ? `${s.values.join(' | ')}` : ' (required)';
    return `<div style="font-size:10px;color:var(--text3);margin-top:3px;font-family:var(--mono)">${_dpEscAttr(s.key + tail)}</div>`;
  }).join('');
  const origVal = edit ? _dpEscAttr(raw.name) : '';

  _showModal(`
    <h3>${title}</h3>
    <input type="hidden" id="dp-add-orig" value="${origVal}" />
    <div class="form-field">
      <label class="form-label" for="dp-add-name">Name</label>
      <input type="text" id="dp-add-name" class="form-input" autocomplete="off" placeholder="e.g. camera-front" value="${nameVal}" />
    </div>
    <div class="form-field">
      <label class="form-label" for="dp-add-ident">Identifier</label>
      <input type="text" id="dp-add-ident" class="form-input" autocomplete="off" placeholder="${_dpEscAttr(identPh)}" value="${identVal}" />
    </div>
    <div class="form-field">
      <label class="form-label" for="dp-add-source">Source</label>
      <input type="text" id="dp-add-source" class="form-input" autocomplete="off" placeholder="e.g. LAN IP or host" value="${srcVal}" />
    </div>
    <div class="form-field">
      <label class="form-label" for="dp-add-adv">Advanced <span style="opacity:.5;font-weight:400">(JSON object, optional)</span></label>
      <textarea id="dp-add-adv" class="form-input" rows="4" style="resize:vertical;font-family:var(--mono);font-size:10px"
        placeholder="{ &quot;description&quot;: &quot;…&quot; }">${advTxt}</textarea>
      ${schemaNote}
    </div>
    <div id="dp-add-err" style="font-size:11px;color:#c44;min-height:18px;margin-top:-4px"></div>
    <div style="display:flex;gap:8px;justify-content:space-between;margin-top:8px;flex-wrap:wrap">
      <button type="button" class="btn" id="dp-add-del" style="color:#c44;${edit ? '' : 'display:none'}">Delete</button>
      <div style="display:flex;gap:8px;margin-left:auto">
        <button type="button" class="btn" id="dp-add-cancel">Cancel</button>
        <button type="button" class="btn accent" id="dp-add-go">${edit ? 'Save' : 'Add'}</button>
      </div>
    </div>
  `);

  const nameEl = document.getElementById('dp-add-name');
  const identEl = document.getElementById('dp-add-ident');
  const sourceEl = document.getElementById('dp-add-source');
  const advEl = document.getElementById('dp-add-adv');
  const origEl = document.getElementById('dp-add-orig');
  const errEl = document.getElementById('dp-add-err');
  const goBtn = document.getElementById('dp-add-go');
  const delBtn = document.getElementById('dp-add-del');

  document.getElementById('dp-add-cancel').onclick = () => closeModal();

  const run = () => _dpSubmitDeviceForm(tileId, pickerType, item, nameEl, identEl, sourceEl, advEl, origEl, errEl, goBtn);
  goBtn.onclick = () => run();
  identEl.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); run(); } };

  if (edit)
    delBtn.onclick = () => _dpDeleteDeviceConfirm(tileId, pickerType, item, raw, (origEl.value || '').trim());

  setTimeout(() => (edit ? nameEl : identEl).focus(), 0);
}

// ── open ─────────────────────────────────────────────────────────────────

async function openDevicePicker(tileId, type, opts) {
  opts = opts || {};
  const multi = !!opts.multi;

  if (_dpOpen === tileId) { closeDevicePicker(); return; }
  closeDevicePicker();

  const tileEl = _dpTileEl(tileId);
  if (!tileEl) return;

  const tile = dashboard?.tiles?.find(t => t.id === tileId);
  const cfg  = tile?.config || {};
  let current;
  if (multi) {
    current = new Set(opts.selectedKeys || []);
    _dpSnap = null;
    _dpSkipDashSave = true;
    _dpPickerOpts = opts;
  } else {
    current = _tileDev(tileId);
    _dpSnap = { device: current, label: cfg.label || current || '' };
    _dpSkipDashSave = false;
    _dpPickerOpts = opts;   // keep schema / onUpsert for the Add/Edit modal
  }

  _dpOpen = tileId;

  const devs = await _fetchDevices();
  let items = [];
  if (type === 'camera') {
    items = (devs.cameras || []).map(d => ({ ..._dpItem(d, current), raw: d }));
  } else if (type === 'audio') {
    items = (devs.mic || []).map(d => ({ ..._dpItem(d, current), raw: d }));
  } else if (type === 'device') {
    items = (devs.devices || []).map(d => ({ ..._dpItem(d, current), raw: d }));
  } else if (type === 'tv') {
    items = (devs.tvs || []).map(d => ({ ..._dpItem(d, current), raw: d }));
  } else if (type === 'lights') {
    items = (devs.lights || []).map(d => ({ ..._dpItem(d, current), raw: d }));
    (devs.ac || []).forEach(d => items.push({ ..._dpItem(d, current, 'AC'), raw: d, kindKey: 'ac' }));
    (devs.ir || []).filter(d => d.category === 'infrared_ac' || d.kind === 'ac')
      .forEach(d => items.push({ ..._dpItem(d, current, 'AC'), raw: d, kindKey: 'ir' }));
  } else {
    (devs.cameras || []).forEach(d => items.push({ ..._dpItem(d, current, 'cam'), raw: d, kindKey: 'cameras' }));
    (devs.mic     || []).forEach(d => items.push({ ..._dpItem(d, current, 'mic'), raw: d, kindKey: 'mic' }));
    (devs.speaker || []).forEach(d => items.push({ ..._dpItem(d, current, 'spk'), raw: d, kindKey: 'speaker' }));
  }

  const selected = multi ? current : null;

  // build
  const pop = document.createElement('div');
  pop.id = 'dp-pop';

  const scroll = document.createElement('div');
  scroll.className = 'dp-scroll';

  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'dp-empty';
    empty.textContent = 'no devices';
    scroll.appendChild(empty);
  } else {
    items.forEach(item => {
      const row = document.createElement('div');
      row.className = 'dp-row';

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'dp-row-pick';
      if (item.sel) btn.style.color = 'var(--accent)';
      btn.innerHTML = item.lineHtml;
      btn.onmousedown = e => {
        e.preventDefault();
        e.stopPropagation();
        if (multi && selected) {
          if (selected.has(item.key)) selected.delete(item.key);
          else selected.add(item.key);
          btn.style.color = selected.has(item.key) ? 'var(--accent)' : 'var(--text2)';
        } else {
          _dpPick(tileId, type, item.key);
        }
      };
      row.appendChild(btn);

      if (item.raw && (type === 'camera' || type === 'audio' || type === 'device' || type === 'tv' || type === 'lights' || item.kindKey)) {
        const ed = document.createElement('button');
        ed.type = 'button';
        ed.className = 'dp-row-edit';
        ed.title = 'Edit';
        ed.setAttribute('aria-label', 'Edit');
        ed.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
        ed.onmousedown = e => { e.preventDefault(); e.stopPropagation(); };
        ed.onclick = e => {
          e.preventDefault();
          e.stopPropagation();
          _dpOpenDeviceFormModal(tileId, type, item, item.raw);
        };
        row.appendChild(ed);
      }

      scroll.appendChild(row);
    });
  }

  pop.appendChild(scroll);
  if (type === 'camera' || type === 'audio' || type === 'device' || type === 'tv' || type === 'lights') {
    const foot = document.createElement('div');
    foot.className = 'dp-footer';
    if (multi && typeof opts.onApply === 'function') {
      const doneBtn = document.createElement('button');
      doneBtn.type = 'button';
      doneBtn.textContent = 'Done';
      // Do not call preventDefault on mousedown here — it suppresses the click on some
      // browsers, so Done would never run onApply and selections would not persist.
      doneBtn.onclick = async e => {
        e.preventDefault();
        e.stopPropagation();
        await opts.onApply(Array.from(selected || []));
        closeDevicePicker();
      };
      foot.appendChild(doneBtn);
    }
    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.textContent = 'Add…';
    addBtn.onmousedown = e => { e.preventDefault(); e.stopPropagation(); };
    addBtn.onclick = e => {
      e.preventDefault();
      e.stopPropagation();
      _dpOpenDeviceFormModal(tileId, type, null, null);
    };
    foot.appendChild(addBtn);
    pop.appendChild(foot);
  }

  // position: overlay the tile, below its header
  const rect = tileEl.getBoundingClientRect();
  const hdrH = 44;
  let top  = rect.top + hdrH;
  let left = rect.left;
  const popW = Math.max(200, Math.min(rect.width, 320));

  // flip above if no room below
  if (top + 220 > window.innerHeight - 8) {
    top = rect.top - 4;
  }
  if (top < 4) top = 4;
  if (left + popW > window.innerWidth - 4) left = window.innerWidth - popW - 4;
  if (left < 4) left = 4;

  pop.style.cssText = `
    position:fixed; z-index:9000;
    top:${top}px; left:${left}px; width:${popW}px;
    max-height:220px;
    display:flex; flex-direction:column;
    background:var(--surface2); border:1px solid var(--border2);
    border-radius:10px; padding:4px;
    box-shadow:0 8px 24px rgba(0,0,0,.4);
  `;

  document.body.appendChild(pop);
  _dpPop = pop;

  // close handlers — use mousedown so we catch before the label's onclick re-fires
  setTimeout(() => document.addEventListener('mousedown', _dpOutside, true), 10);
  document.addEventListener('keydown', _dpEsc);
}

// ── close ────────────────────────────────────────────────────────────────

function closeDevicePicker() {
  if (_dpPop) { _dpPop.remove(); _dpPop = null; }
  if (_dpOpen && _dpSnap && !_dpSkipDashSave) {
    const tile = dashboard?.tiles?.find(t => t.id === _dpOpen);
    const cfg  = tile?.config || {};
    if (_tileDev(_dpOpen) !== _dpSnap.device || (cfg.label || '') !== _dpSnap.label) {
      if (typeof saveDashboard === 'function') saveDashboard();
    }
  }
  _dpOpen = null;
  _dpSnap = null;
  _dpPickerOpts = null;
  _dpSkipDashSave = false;
  document.removeEventListener('mousedown', _dpOutside, true);
  document.removeEventListener('keydown', _dpEsc);
}

function _dpPick(tileId, type, key) {
  const tile = dashboard?.tiles?.find(t => t.id === tileId);
  if (!tile) return;
  if (!tile.config) tile.config = {};
  const autoLabel = _dpPickerOpts?.syncLabel !== false
    && (!tile.config.label || tile.config.label === _dpSnap?.device);
  tile.config.device = key;
  if (autoLabel) tile.config.label = key;
  const el = document.getElementById(`${tileId}-label`);
  if (el) el.textContent = tile.config.label || '—';
  closeDevicePicker();
}

function _dpOutside(e) {
  if (!_dpPop) return;
  if (_dpPop.contains(e.target)) return;
  // also ignore clicks on the label that opened it (toggle handled by openDevicePicker)
  const labelEl = _dpOpen ? document.getElementById(`${_dpOpen}-label`) : null;
  if (labelEl && labelEl.contains(e.target)) return;
  const secCam = _dpOpen ? document.getElementById(`${_dpOpen}-sec-cam`) : null;
  const secMic = _dpOpen ? document.getElementById(`${_dpOpen}-sec-mic`) : null;
  if (secCam && secCam.contains(e.target)) return;
  if (secMic && secMic.contains(e.target)) return;
  closeDevicePicker();
}

function _dpEsc(e) { if (e.key === 'Escape') closeDevicePicker(); }

// ── override editTileLabel ──────────────────────────────────────────────

const _origEditTileLabel = typeof editTileLabel === 'function' ? editTileLabel : null;

function editTileLabel(tileId) {
  const tile = dashboard?.tiles?.find(t => t.id === tileId);
  if (!tile) { _origEditTileLabel?.(tileId); return; }
  switch (tile.type) {
    case 'camera-feed':     openDevicePicker(tileId, 'camera'); break;
    case 'audio':           openDevicePicker(tileId, 'audio');  break;
    case 'device':          openDevicePicker(tileId, 'device'); break;
    case 'tv-player':       tvOpenPicker(tileId);               break;
    default:                _origEditTileLabel?.(tileId);       break;
  }
}

// ── CSS ──────────────────────────────────────────────────────────────────

(function() {
  if (document.getElementById('dp-css')) return;
  const s = document.createElement('style');
  s.id = 'dp-css';
  s.textContent = `
#dp-pop .dp-row { display:flex; align-items:stretch; gap:2px; width:100%; }
#dp-pop .dp-row-pick {
  flex:1; min-width:0; text-align:left; padding:6px 10px;
  background:none; border:none; border-radius:6px;
  font-family:var(--font); font-size:11px; color:var(--text2);
  cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  transition:background .1s;
}
#dp-pop .dp-row-pick:hover { background:var(--accent-mid); color:var(--accent); }
#dp-pop .dp-row-dim { color:var(--text3); opacity:0.72; font-weight:400; }
#dp-pop .dp-row-pick:hover .dp-row-dim { opacity:0.85; }
#dp-pop .dp-row-edit {
  flex-shrink:0; width:28px; padding:0; border:none; border-radius:6px;
  background:none; color:var(--text3); cursor:pointer; opacity:0.55;
  display:flex; align-items:center; justify-content:center;
  transition:opacity .1s, background .1s, color .1s;
}
#dp-pop .dp-row-edit:hover { opacity:1; background:var(--accent-mid); color:var(--accent); }
#dp-pop .dp-footer > button {
  display:block; width:100%; text-align:left; padding:6px 10px;
  background:none; border:none; border-radius:6px;
  font-family:var(--font); font-size:11px; color:var(--text2);
  cursor:pointer; transition:background .1s;
}
#dp-pop .dp-footer > button:hover { background:var(--accent-mid); color:var(--accent); }
.dp-meta { font-size:9px; opacity:.45; font-family:var(--mono); }
#dp-pop .dp-scroll { flex:1; min-height:0; max-height:156px; overflow-y:auto; }
#dp-pop .dp-empty { padding:8px 10px; font-size:11px; color:var(--text2); opacity:.55; }
#dp-pop .dp-footer { flex-shrink:0; border-top:1px solid var(--border2); margin-top:4px; padding-top:4px; }
  `;
  document.head.appendChild(s);
})();
