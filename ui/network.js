// web/network.js

(function() {
  const style = document.createElement('style');
  style.textContent = `
    #modal:has(.net-form) { max-width:calc(100vw - 32px);max-height:calc(100dvh - 32px);overflow:auto; }
    .net-form { display:flex;flex-direction:column;gap:10px;min-width:0; }
    .net-field { display:flex;align-items:center;gap:8px;min-width:0; }
    .net-field > label,.net-field > dt { flex:0 0 68px;font-size:12px;color:var(--text2); }
    .net-field .media-search-input { min-width:0;width:100%;height:26px;padding:3px 8px; }
    .net-attributes { border-top:1px solid var(--surface2);padding-top:12px;margin:4px 0;display:flex;flex-direction:column;gap:10px; }
    .net-field > dd { margin:0;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11px;color:var(--text2); }
    .net-ports { border-top:1px solid var(--surface2);padding-top:12px;display:flex;flex-direction:column;gap:8px; }
    .net-ports h4 { font-size:12px;font-weight:600;margin:0 0 4px; }
    .net-services-heading { display:flex;align-items:center;flex-wrap:wrap;gap:8px; }
    .net-services-heading h4 { margin:0; }
    .net-discover { margin-left:auto; }
    .net-port-row,.net-port-head { display:grid;grid-template-columns:90px minmax(0,1fr) 22px;align-items:center;gap:8px; }
    .net-ports .media-icon-btn svg,.net-discover svg { width:14px;height:14px; }
    .net-port-head { font-size:11px;color:var(--text2); }
    .net-port-row .media-search-input { min-width:0;width:100%;height:26px;padding:3px 8px; }
    .net-form select.media-search-input { flex:0 1 auto;width:75%;max-width:100%;height:24px;padding:2px 8px;align-self:center; }
    @supports (appearance:base-select) {
      .net-form select.media-search-input,
      .net-form select.media-search-input::picker(select) { appearance:base-select; }
      .net-form select.media-search-input::picker(select) {
        width:anchor-size(width);max-width:calc(100vw - 32px);
        max-height:min(180px,calc(100dvh - 32px));overflow-y:auto;
        margin:4px 0;padding:4px;background:var(--surface2);color:var(--text2);
        border:1px solid var(--border2);border-radius:8px;
        font-family:var(--font);font-size:12px;box-sizing:border-box;
      }
      .net-form select.media-search-input option { padding:4px 8px;border-radius:4px; }
      .net-form select.media-search-input option:hover,
      .net-form select.media-search-input option:checked { background:var(--accent-mid);color:var(--accent); }
    }
    .net-port-row input[readonly] { color:var(--text2); }
    .net-port-list { display:flex;flex-direction:column;gap:8px; }
    .net-actions { display:flex;justify-content:flex-end;gap:8px;margin-top:4px; }
    .net-error { color:var(--red);font-size:12px; }
  `;
  document.head.appendChild(style);
})();

function _netEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

TILES['presence'] = {
  html(tile) {
    const id = tile.id;
    return `
    ${_tileHeader(id, 'NETWORK',
      `<button class="icon-btn" onclick="networkSettings('${id}')" title="Protocols">⚙</button>`
    )}
    <div style="display:flex;flex-direction:column;overflow-y:auto;padding:0 14px 14px" id="${id}-body">
      <div class="muted">No devices found</div>
    </div>`;
  },

  bind(id) {
    // restore last-known presence without scanning
    _networkLoad([id]).catch(() => {});
  },

  events(id) {
    onEvent('network', evt => {
      document.querySelectorAll('.tile-presence').forEach(el => _networkApply(el.dataset.id, evt));
    });
  }
};

const _networkState = {};

async function _networkLoad(ids = Object.keys(_networkState)) {
  const response = await fetch('/api/config?target=presence');
  if (!response.ok) throw new Error('Could not load network devices.');
  const data = await response.json();
  for (const id of ids) {
    _networkState[id] = Object.fromEntries(Object.entries(data.devices || {}).map(([key, s]) =>
      [key, {...s, name: s.known ? (s.label || '') : '', probe: s.probe || {}}]));
    _networkRender(id);
  }
}

// ── Protocol prefs (localStorage) ────────────────────────────────────────────

function _networkPrefs() {
  try { return { wifi: true, ble: true, ...JSON.parse(localStorage.getItem('networkPrefs') || '{}') }; }
  catch (_) { return { wifi: true, ble: true }; }
}

let _networkCfgPop = null;

function networkSettings(id) {
  if (_networkCfgPop) { _networkCloseSettings(); return; }
  const tileEl = tileRefs[id] || document.querySelector(`.tile[data-id="${id}"]`);
  if (!tileEl) return;
  const p = _networkPrefs();
  const row = (key, lbl) => `
    <div class="dp-row"><button class="dp-row-pick" onclick="networkToggleProto('${id}','${key}')">
      ${lbl}<span class="dp-row-dim" style="float:right">${p[key] ? '✓' : '○'}</span></button></div>`;

  const pop = document.createElement('div');
  pop.id = 'dp-pop';
  const scroll = document.createElement('div');
  scroll.className = 'dp-scroll';
  scroll.innerHTML = row('wifi', 'WIFI') + row('ble', 'BTE');
  pop.appendChild(scroll);

  const rect = tileEl.getBoundingClientRect();
  let top = rect.top + 44, left = rect.left;
  const popW = Math.max(160, Math.min(rect.width, 220));
  if (left + popW > window.innerWidth - 4) left = window.innerWidth - popW - 4;
  if (left < 4) left = 4;
  pop.style.cssText = `position:fixed;z-index:9000;top:${top}px;left:${left}px;width:${popW}px;
    display:flex;flex-direction:column;background:var(--surface2);
    border:1px solid var(--border2);border-radius:10px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.4)`;
  document.body.appendChild(pop);
  _networkCfgPop = pop;
  setTimeout(() => document.addEventListener('mousedown', _networkCfgOutside, true), 10);
  document.addEventListener('keydown', _networkCfgEsc);
}

function networkToggleProto(id, key) {
  const p = _networkPrefs();
  p[key] = !p[key];
  if (!p.wifi && !p.ble) p[key] = true;   // never both off
  try { localStorage.setItem('networkPrefs', JSON.stringify(p)); } catch (_) {}
  _networkCloseSettings();
  _networkRender(id);
}

function _networkCloseSettings() {
  if (_networkCfgPop) { _networkCfgPop.remove(); _networkCfgPop = null; }
  document.removeEventListener('mousedown', _networkCfgOutside, true);
  document.removeEventListener('keydown', _networkCfgEsc);
}
function _networkCfgOutside(e) {
  if (!_networkCfgPop) return;
  if (e.target.closest?.('[title="Protocols"]')) return;
  if (!_networkCfgPop.contains(e.target)) _networkCloseSettings();
}
function _networkCfgEsc(e) { if (e.key === 'Escape') _networkCloseSettings(); }

function _networkApply(id, evt) {
  if (!_networkState[id]) _networkState[id] = {};
  const dev = _networkState[id];
  switch (evt.event) {
    case 'host': {
      const known = evt.known === 'True';
      const key   = evt.ip || evt.mac;               // BLE has no IP — key by address
      dev[key] = { home: true, ip: evt.ip || '', mac: evt.mac || '', known,
                   name: known ? (evt.label || '') : '', vendor: evt.vendor || '',
                   label: evt.label || '', transport: evt.transport || 'wifi',
                   rssi: evt.rssi != null ? parseInt(evt.rssi, 10) : undefined,
                   last_seen: Date.now() / 1000, since: parseFloat(evt.since) || 0,
                   probe: dev[key]?.probe || {} };
      break;
    }
    case 'away':
      dev[evt.name] = { ...(dev[evt.name] || {}), home: false, known: true, name: evt.name,
                        since: parseFloat(evt.since) || 0 };
      break;
    case 'scan':
      _networkLoad([id]).catch(() => _networkRender(id));
      return;
    default: return;
  }
  _networkRender(id);
}

function _netAgo(ts) {
  if (!ts) return 'away';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90)    return 'just now';
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function _networkRender(id) {
  const body = document.getElementById(`${id}-body`);
  if (!body) return;
  const p = _networkPrefs();
  const devs = Object.entries(_networkState[id] || {})
    .filter(([,s]) => (s.transport === 'ble' ? p.ble : p.wifi));
  if (!devs.length) { body.innerHTML = '<div class="muted">No devices found</div>'; setDot(id, ''); return; }
  devs.sort(([,a],[,b]) => {
    const ta = a.transport === 'ble' ? 1 : 0, tb = b.transport === 'ble' ? 1 : 0;
    return ta - tb || (b.since || 0) - (a.since || 0);
  });
  const homeN = devs.filter(([,s]) => s.home).length;
  setDot(id, homeN > 0 ? 'green' : '');
  body.innerHTML = `<div style="display:flex;flex-direction:column;gap:3px">` +
    devs.map(([key,s]) => {
      const label = s.known ? (s.name || '') : (s.label || s.ip || '');
      const ble   = s.transport === 'ble';
      const meta  = !s.home ? _netAgo(s.last_seen)
                    : ble     ? (s.rssi != null ? `${s.rssi} dBm` : '')
                    : label === s.ip ? ((s.probe?.services?.[0]?.service) || s.probe?.kind || '')
                    : s.ip;
      const tag   = `<span style="font-size:9px;border:1px solid currentColor;border-radius:3px;padding:0 3px;margin-right:4px;opacity:.55">${ble ? 'BTE' : 'WIFI'}</span>`;
      return `
      <div class="list-row${s.home?' home':''}">
        <div class="presence-dot ${s.home?'home':'away'}"></div>
        <span class="row-name" style="font-size:11px">${_netEscape(label)}</span>
        ${tag}
        <span class="row-meta">${_netEscape(meta)}</span>
        <button class="icon-btn" style="width:16px;height:16px;flex-shrink:0" onclick="${_netEscape(`networkEditRow(this,${JSON.stringify(id)},${JSON.stringify(key)})`)}" title="Edit device">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
        </button>
      </div>`;}).join('') + '</div>';
}

function networkEditRow(btn, id, key) {
  const dev = (_networkState[id] || {})[key];
  const modal = document.getElementById('modal');
  if (!dev || !dev.mac || !modal) return;
  let probe = dev.probe || {};
  const deletedPorts = new Set();
  const kinds = ['unknown', 'tv', 'router', 'media', 'printer', 'computer', 'camera', 'phone'];
  const services = [...new Set(['cast', 'roku', 'lg-webos', 'samsung', 'plex', 'jellyfin',
    'http', 'https', 'smb', 'ssh', 'afp', 'rdp', 'vnc', 'ipp', 'jetdirect', 'rtsp',
    'sip', 'dns', 'ssdp', 'mdns', 'daap', ...(probe.services || []).map(s => s.service)])].sort();
  const ports = [...new Set([...(probe.ports || []), ...(probe.services || []).map(s => s.port)])].sort((a,b) => a-b);
  modal.dataset.locked = 'false';
  modal.innerHTML = `
    <h3>Edit network device</h3>
    <form class="net-form">
      <div class="net-field"><label for="net-name">Name:</label><input id="net-name" class="media-search-input" value="${_netEscape(dev.name || dev.label || '')}" required autocomplete="off"></div>
      <div class="net-field"><label for="net-kind">Type:</label><select id="net-kind" class="media-search-input">${kinds.map(kind => `<option value="${kind}"${kind === (probe.kind || 'unknown') ? ' selected' : ''}>${kind}</option>`).join('')}</select></div>
      <dl class="net-attributes">
        ${[['IP', dev.ip], ['MAC', dev.mac], ['Hostname', probe.hostname]].map(([label, value]) => `<div class="net-field"><dt>${label}:</dt><dd>${_netEscape(value || '—')}</dd></div>`).join('')}
      </dl>
      <section class="net-ports" aria-label="Services">
        <div class="net-services-heading"><h4>Services</h4><button type="button" class="btn net-discover" title="Refresh services discovered on this device" aria-label="Refresh services for this device" ${dev.ip ? '' : 'disabled'}><i data-lucide="refresh-cw" aria-hidden="true"></i>refresh</button></div>
        <div class="net-port-head" aria-hidden="true"><span>Port</span><span>Service</span></div>
        <div class="net-port-list"></div>
        <button type="button" class="btn net-add-service" style="align-self:flex-start">+ add service</button>
      </section>
      <div class="net-error" role="alert" hidden></div>
      <div class="net-actions"><button type="button" class="btn net-cancel">cancel</button><button type="submit" class="btn accent">save</button></div>
    </form>`;
  const form = modal.querySelector('form');
  const list = form.querySelector('.net-port-list');
  const discover = form.querySelector('.net-discover');
  function setBusy(busy) {
    modal.dataset.locked = String(busy);
    form.querySelectorAll('button,input,select').forEach(el => el.disabled = busy);
    discover.disabled = busy || !dev.ip;
  }
  function addService(port = '', service = '') {
    const row = document.createElement('div');
    row.className = 'net-port-row';
    row.innerHTML = `<input class="media-search-input" type="number" min="1" max="65535" step="1" required aria-label="Port" placeholder="port" value="${_netEscape(port)}" ${port !== '' ? 'readonly' : ''}>
      <select class="media-search-input" aria-label="Service for port ${_netEscape(port || 'new port')}"><option value="">no service</option>${services.map(s => `<option value="${_netEscape(s)}"${s === service ? ' selected' : ''}>${_netEscape(s)}</option>`).join('')}</select>
      <button type="button" class="media-icon-btn" title="Delete service" aria-label="Delete service"><i data-lucide="trash-2" aria-hidden="true"></i></button>`;
    row.querySelector('button').onclick = () => {
      const value = row.querySelector('input').value;
      if (value) deletedPorts.add(Number(value));
      row.remove();
    };
    list.appendChild(row);
    if (typeof lucide !== 'undefined') lucide.createIcons();
    if (port === '') row.querySelector('input').focus();
  }
  ports.forEach(port => addService(port, (probe.services || []).find(s => s.port === port)?.service || ''));
  form.querySelector('.net-add-service').onclick = () => addService();
  discover.onclick = async () => {
    const error = form.querySelector('.net-error');
    error.hidden = true;
    setBusy(true);
    discover.setAttribute('aria-busy', 'true');
    try {
      const result = await runOp(['device', 'discover', '--refresh', '--ip', dev.ip]);
      if (!result.ok || /^ERROR:/m.test(result.stdout || '')) {
        throw new Error(result.stderr || result.stdout || 'Discovery failed.');
      }
      await _networkLoad();
      const entry = Object.entries(_networkState[id] || {}).find(([, s]) => s.mac === dev.mac);
      if (!entry) throw new Error('Device no longer available.');
      key = entry[0];
      const updated = entry[1];
      const fresh = updated.probe || {};
      const kind = form.querySelector('#net-kind');
      if (kind.value === (probe.kind || 'unknown')) kind.value = fresh.kind || 'unknown';
      const details = form.querySelectorAll('.net-attributes dd');
      [updated.ip, updated.mac, fresh.hostname].forEach((value, i) => details[i].textContent = value || '—');
      const rows = new Map([...list.children].filter(row => row.querySelector('input').value)
        .map(row => [Number(row.querySelector('input').value), row]));
      for (const service of fresh.services || []) {
        if (!services.includes(service.service)) services.push(service.service);
      }
      const freshPorts = [...new Set([...(fresh.ports || []), ...(fresh.services || []).map(s => s.port)])].sort((a,b) => a-b);
      const previousPorts = new Set([...(probe.ports || []), ...(probe.services || []).map(s => s.port)]);
      for (const [port, row] of rows) {
        const original = (probe.services || []).find(s => s.port === port)?.service || '';
        if (previousPorts.has(port) && !freshPorts.includes(port) && row.querySelector('select').value === original) row.remove();
      }
      for (const port of freshPorts) {
        const service = (fresh.services || []).find(s => s.port === port)?.service || '';
        const row = rows.get(port);
        if (!row) {
          if (!deletedPorts.has(port)) addService(port, service);
        } else {
          const select = row.querySelector('select');
          const original = (probe.services || []).find(s => s.port === port)?.service || '';
          if (select.value === original) {
            if (![...select.options].some(option => option.value === service)) select.add(new Option(service, service));
            select.value = service;
          }
        }
      }
      probe = fresh;
    } catch (err) {
      error.textContent = err.message || 'Discovery failed.';
      error.hidden = false;
    } finally {
      discover.removeAttribute('aria-busy');
      setBusy(false);
    }
  };
  form.querySelector('.net-cancel').onclick = closeModal;
  form.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !e.target.matches('select:open')) { e.preventDefault(); closeModal(); }
  });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const error = form.querySelector('.net-error');
    error.hidden = true;
    const rows = [...list.children].map(row => ({port: Number(row.querySelector('input').value), service: row.querySelector('select').value}));
    const name = form.querySelector('#net-name').value.trim();
    if (!name || new Set(rows.map(r => r.port)).size !== rows.length) {
      error.textContent = !name ? 'Name is required.' : 'Each port must be unique.';
      error.hidden = false;
      return;
    }
    const payload = {mac: dev.mac, name, kind: form.querySelector('#net-kind').value,
      ports: rows.map(r => r.port), services: rows.filter(r => r.service)};
    setBusy(true);
    try {
      if (!await saveConfigValue('presence', `devices.${key}`, payload)) return;
      await _networkLoad();
      modal.dataset.locked = 'false';
      closeModal();
    } catch (err) {
      error.textContent = err.message || 'Could not save device.';
      error.hidden = false;
    } finally {
      setBusy(false);
    }
  });
  document.getElementById('modal-backdrop').style.display = 'flex';
  if (typeof lucide !== 'undefined') lucide.createIcons();
  form.querySelector('#net-name').focus();
}
