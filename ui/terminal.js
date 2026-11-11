// terminal.js

TILES['terminal'] = {
  html(tile) {
    const id = tile.id;
    return `
    ${_tileHeader(id, 'TERMINAL',
      `<button class="icon-btn" onclick="clearTerminal('${id}')">⌫</button>`
    )}
    <div style="display:flex;flex-direction:column;gap:6px;padding:0 10px 10px;height:100%;min-height:0">
      <div style="flex:1;overflow-y:auto;overflow-x:hidden;font-family:var(--mono);font-size:11px;line-height:1.6;color:var(--text2);background:var(--surface2);border-radius:8px;padding:8px 10px;scrollbar-width:thin;scrollbar-color:var(--border2) transparent" id="${id}-output"></div>
      <div style="display:flex;align-items:center;gap:6px;background:var(--surface2);border-radius:8px;padding:6px 10px;border:1px solid var(--border)" onfocusin="this.style.borderColor='var(--accent)'" onfocusout="this.style.borderColor='var(--border)'">
        <span style="color:var(--accent);font-family:var(--mono);font-size:11px;flex-shrink:0">›</span>
        <input style="flex:1;background:transparent;border:none;outline:none;color:var(--text);font-family:var(--mono);font-size:11px"
          id="${id}-input" type="text" placeholder="security status" autocomplete="off" spellcheck="false"
          onkeydown="termKey(event,'${id}')">
      </div>
    </div>`;
  },

  bind(id) { _termConnect(id); },
  events() {}
};

const _termHistory = {};
const _termHistIdx = {};

function _termConnect(id, replay = 120) {
  if (_termTailES[id]) _termTailES[id].close();
  const out = document.getElementById(`${id}-output`);
  if (!out) return;
  const es = new EventSource(`/api/tail?lines=${replay}`);
  _termTailES[id] = es;
  es.onmessage = e => {
    try {
      const d = JSON.parse(e.data);
      if (d.type === 'log' && d.line) _termAppend(out, d.line, _termLineCls(d.line));
    } catch(_) {}
  };
  es.onerror = () => { delete _termTailES[id]; setTimeout(() => _termConnect(id, 0), 3000); };
}

function _termLineCls(line) {
  const hint = line.match(/\] (\S+)/)?.[1] || '';
  if (hint === 'run.op') return 'cmd';
  if (hint === 'run.err' || line.includes(' !!! ') || line.includes(' !! ')) return 'err';
  if (hint.includes('alert')) return 'err';
  if (hint.includes('arm'))   return 'ok';
  return '';
}

function _termAppend(out, text, cls = '') {
  const row = document.createElement('div');
  row.className   = 't-line' + (cls ? ' ' + cls : '');
  row.textContent = text;
  out.appendChild(row);
  out.scrollTop = out.scrollHeight;
  while (out.children.length > 400) out.removeChild(out.firstChild);
}

function clearTerminal(id) {
  const out = document.getElementById(`${id}-output`);
  if (out) out.innerHTML = '';
}

const _longOps = ['security watch','security listen','audio listen','camera detect-move','run '];

function termKey(e, id) {
  if (!_termHistory[id]) _termHistory[id] = [];
  const inp = document.getElementById(`${id}-input`);
  if (e.key === 'Enter') {
    const val = inp.value.trim(); if (!val) return;
    _termHistory[id].push(val); _termHistIdx[id] = _termHistory[id].length;
    inp.value = '';
    _termRun(id, val);
  }
  if (e.key === 'ArrowUp') {
    const h = _termHistory[id]; if (!h.length) return;
    _termHistIdx[id] = Math.max(0, (_termHistIdx[id] ?? h.length) - 1);
    inp.value = h[_termHistIdx[id]] || ''; e.preventDefault();
  }
  if (e.key === 'ArrowDown') {
    const h = _termHistory[id];
    _termHistIdx[id] = Math.min(h.length, (_termHistIdx[id] ?? 0) + 1);
    inp.value = h[_termHistIdx[id]] || ''; e.preventDefault();
  }
}

async function _termRun(id, cmd) {
  const args   = cmd.split(/\s+/).filter(Boolean);
  const isLong = _longOps.some(op => cmd.startsWith(op));
  if      (args[0] === 'stop') await stopProc('term_' + id);
  else if (isLong)             await startProc('term_' + id, args);
  else                         runOp(args);
}
