// assistant.js

// ── Voice Ball CSS (injected once) ─────────────────────────────────────────
(function() {
  if (document.getElementById('va-ball-css')) return;
  const s = document.createElement('style');
  s.id = 'va-ball-css';
  s.textContent = `
    .va-ball {
      width: 80px; height: 80px; border-radius: 50%;
      background: var(--text3); cursor: pointer;
      transition: background 0.3s, box-shadow 0.3s;
      margin: 0 auto; flex-shrink: 0;
    }
    .va-ball:hover { filter: brightness(1.15); }
    .va-ball.listening   { background: var(--accent); box-shadow: 0 0 20px var(--accent); animation: va-pulse 1.5s infinite; }
    .va-ball.loading     { background: var(--amber);  box-shadow: 0 0 12px var(--amber);  opacity: 0.7; }
    .va-ball.recording   { background: var(--cyan);   box-shadow: 0 0 24px var(--cyan);   animation: va-pulse 0.8s infinite; }
    .va-ball.transcribing { background: var(--cyan);  box-shadow: 0 0 16px var(--cyan);   animation: va-spin 1.5s linear infinite; }
    .va-ball.thinking    { background: var(--amber);  box-shadow: 0 0 20px var(--amber);  animation: va-spin 1s linear infinite; }
    .va-ball.speaking    { background: var(--green);  box-shadow: 0 0 20px var(--green);  animation: va-pulse 1s infinite; }
    .va-ball.error       { background: var(--red);    box-shadow: 0 0 20px var(--red); }
    @keyframes va-pulse { 0%,100%{transform:scale(1)} 50%{transform:scale(1.08)} }
    @keyframes va-spin  { 0%{transform:rotate(0deg)} 100%{transform:rotate(360deg)} }
  `;
  document.head.appendChild(s);
})();

// ── Tile Definition ────────────────────────────────────────────────────────

TILES['assistant'] = {
  html(tile) {
    const id = tile.id;
    const mode = tile.config?.mode || 'text';
    return `
    ${_tileHeader(id, 'ASSISTANT',
      `<button class="icon-btn" onclick="assistantToggleMode('${id}')" title="toggle mode" id="${id}-mode-btn">${mode === 'voice' ? '🎙️' : '💬'}</button>
       <button class="icon-btn" onclick="assistantSettings('${id}')" title="LLM settings">⚙</button>`
    )}
    <div id="${id}-text-view" style="display:${mode==='voice'?'none':'flex'};flex-direction:column;gap:6px;padding:0 10px 10px;height:100%;min-height:0">
      <div style="position:relative;display:flex;flex:1;min-height:0;background:var(--surface2);border-radius:8px;overflow:hidden">
        <div style="flex:1;min-width:0;overflow-y:auto;overflow-x:hidden;font-family:var(--mono);font-size:11px;line-height:1.6;color:var(--text2);padding:8px 36px 8px 10px;scrollbar-width:thin;scrollbar-color:var(--border2) transparent;display:flex;flex-direction:column;gap:4px" id="${id}-output"></div>
        <button class="icon-btn" onclick="assistantClear('${id}')" title="Clear conversation" aria-label="Clear conversation" style="position:absolute;top:4px;right:6px">⌫</button>
      </div>
      <div style="display:flex;align-items:center;gap:6px;background:var(--surface2);border-radius:8px;padding:6px 10px;border:1px solid var(--border)" onfocusin="this.style.borderColor='var(--accent)'" onfocusout="this.style.borderColor='var(--border)'">
        <input style="flex:1;background:transparent;border:none;outline:none;color:var(--text);font-family:var(--mono);font-size:11px"
          id="${id}-input" type="text" placeholder="ask anything..." autocomplete="off" spellcheck="false"
          onkeydown="assistantKey(event,'${id}')">
        <button class="icon-btn" onclick="assistantSend('${id}')" style="color:var(--accent);font-size:13px;flex-shrink:0">▶</button>
      </div>
    </div>
    <div id="${id}-voice-view" style="display:${mode==='voice'?'flex':'none'};flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:10px;height:100%;min-height:0">
      <div class="va-ball" id="${id}-ball" onclick="assistantVoiceToggle('${id}')"></div>
      <div style="font-size:11px;color:var(--text2);text-align:center;min-height:16px" id="${id}-va-status">tap to start</div>
    </div>`;
  },

  bind(id) { _assistantInit(id); },
  events(id) {
    onEvent('llm.voice', evt => _vaEvtVoice(id, evt));
    onEvent('llm.voice.transcribe', evt => _vaEvtTranscribe(id, evt));
    onEvent('llm.voice.think', evt => _vaEvtThink(id, evt));
    onEvent('llm.voice.speak', evt => _vaEvtSpeak(id, evt));
    onEvent('llm.voice.error', evt => _vaEvtError(id, evt));
    onEvent('audio.voice', evt => _vaEvtAudioVoice(id, evt));
    onEvent('audio.wake', evt => _vaEvtWake(id, evt));
    onEvent('audio.stt', evt => _vaEvtStt(id, evt));
    onEvent('audio.tts', evt => _vaEvtTts(id, evt));
  }
};

// ── State ──────────────────────────────────────────────────────────────────

const _assistantSending = {};
const _vaState = {};        // { [id]: 'idle'|'listening'|'recording'|'thinking'|'speaking'|'error' }
const _vaActive = {};       // { [id]: true while process running }
const _vaPrevState = {};    // for sound transition detection

// -- Sounds ------------------------------------------------------------------

function _vaPlaySound(type) {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const freqs = {start:880, stop:440, error:220, transcribe:660, think:1100, speak:550, recording:770};
    osc.frequency.value = freqs[type] || 440;
    osc.connect(gain); gain.connect(ctx.destination);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.15);
    osc.start(); osc.stop(ctx.currentTime + 0.15);
  } catch(_) {}
}


// ── Init ───────────────────────────────────────────────────────────────────

function _assistantInit(id) {
  _vaState[id] = 'idle';
  const tile = dashboard?.tiles?.find(t => t.id === id);
  if (tile?.config?.mode === 'voice') {
    _assistantStatus(id);
  } else {
    _assistantStatus(id);
  }
}

// ── Text Mode Functions ────────────────────────────────────────────────────

async function _assistantStatus(id) {
  try {
    const cfg = await fetch('/api/config?target=llm').then(r => r.json()).catch(() => ({}));
    const provider = cfg.provider || '';
    const out = document.getElementById(`${id}-output`);
    if (!out) return;
    if (!provider) {
      setDot(id, 'gray');
      out.innerHTML = `<div class="muted">not configured — <a onclick="assistantSettings('${id}')" style="color:var(--accent);cursor:pointer">set up</a></div>`;
    } else {
      setDot(id, 'green');
      const model = cfg.model || '(default)';
      const display = model.startsWith(provider + '/') ? model : `${provider}/${model}`;
      _assistantAppend(id, 'system', display);
    }
  } catch(_) {}
}

function assistantKey(e, id) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    assistantSend(id);
  }
}

async function assistantSend(id) {
  if (_assistantSending[id]) return;
  const inp = document.getElementById(`${id}-input`);
  if (!inp) return;
  const prompt = inp.value.trim();
  if (!prompt) return;
  inp.value = '';
  _assistantAppend(id, 'user', prompt);
  _assistantSending[id] = true;
  setDot(id, 'blue');
  try {
    const r = await runOp(['llm', 'assist', prompt]);
    if (r.ok && r.stdout && r.stdout.trim()) {
      const lines = r.stdout.trim().split('\n').filter(l => !/^\[[\d:]+\]\s*🤖\s*llm\.assist/.test(l));
      const text = lines.join('\n').trim();
      if (text) _assistantAppend(id, 'assistant', text);
      setDot(id, 'green');
    } else {
      const err = r.stderr || 'no response';
      if (err.includes('API key not set')) {
        _assistantAppendError(id, 'API key not set —', 'open settings', () => assistantSettings(id));
      } else {
        _assistantAppend(id, 'error', err);
      }
      setDot(id, 'red');
    }
  } catch(e) {
    _assistantAppend(id, 'error', String(e));
    setDot(id, 'red');
  }
  _assistantSending[id] = false;
}

function _assistantAppend(id, role, text) {
  const out = document.getElementById(`${id}-output`);
  if (!out) return;
  const row = document.createElement('div');
  if (role === 'user') {
    row.style.cssText = 'color:var(--text);padding:2px 0';
    row.textContent = text;
  } else if (role === 'assistant') {
    row.style.cssText = 'color:var(--accent);padding:2px 0';
    row.textContent = text;
  } else if (role === 'error') {
    row.style.cssText = 'color:var(--red);padding:2px 0';
    row.textContent = text;
  } else {
    row.style.cssText = 'color:var(--text3);padding:2px 0;font-size:10px';
    row.textContent = text;
  }
  out.appendChild(row);
  out.scrollTop = out.scrollHeight;
  while (out.children.length > 200) out.removeChild(out.firstChild);
}

function _assistantAppendError(id, prefix, linkText, onClick) {
  const out = document.getElementById(`${id}-output`);
  if (!out) return;
  const row = document.createElement('div');
  row.style.cssText = 'color:var(--red);padding:2px 0';
  row.textContent = prefix + ' ';
  const a = document.createElement('a');
  a.textContent = linkText;
  a.style.cssText = 'color:var(--accent);cursor:pointer';
  a.onclick = onClick;
  row.appendChild(a);
  out.appendChild(row);
  out.scrollTop = out.scrollHeight;
}

function assistantClear(id) {
  const out = document.getElementById(`${id}-output`);
  if (out) out.innerHTML = '';
  _assistantStatus(id);
}

// ── Mode Toggle ────────────────────────────────────────────────────────────

function assistantToggleMode(id) {
  const tile = dashboard?.tiles?.find(t => t.id === id);
  if (!tile) return;
  if (!tile.config) tile.config = {};
  const newMode = tile.config.mode === 'voice' ? 'text' : 'voice';
  tile.config.mode = newMode;
  saveDashboard();

  const btn = document.getElementById(`${id}-mode-btn`);
  if (btn) btn.textContent = newMode === 'voice' ? '🎙️' : '💬';

  const tv = document.getElementById(`${id}-text-view`);
  const vv = document.getElementById(`${id}-voice-view`);
  if (tv) tv.style.display = newMode === 'voice' ? 'none' : 'flex';
  if (vv) vv.style.display = newMode === 'voice' ? 'flex' : 'none';

  if (newMode === 'voice') {
    _vaSetBall(id, 'idle');
    _vaSetStatus(id, 'tap to start');
  } else {
    _vaStop(id);
  }
}

// ── Voice Mode: Ball & Status ──────────────────────────────────────────────

function _vaSetBall(id, state) {
  const ball = document.getElementById(`${id}-ball`);
  if (!ball) return;
  ball.className = 'va-ball' + (state !== 'idle' ? ' ' + state : '');
  _vaState[id] = state;
}

function _vaSetStatus(id, text) {
  const el = document.getElementById(`${id}-va-status`);
  if (el) el.textContent = text;
}

// ── Voice Mode: Start / Stop ───────────────────────────────────────────────

function assistantVoiceToggle(id) {
  if (_vaActive[id]) {
    _vaStop(id);
  } else {
    _vaStart(id);
  }
}

async function _vaStart(id) {
  if (_vaActive[id]) return;
  const tile = dashboard?.tiles?.find(t => t.id === id);
  const cfg = await fetch('/api/config?target=llm').then(r => r.json()).catch(() => ({}));
  if (!cfg.provider) {
    _vaSetBall(id, 'error');
    _vaSetStatus(id, 'not configured — open settings');
    setTimeout(() => { _vaSetBall(id, 'idle'); _vaSetStatus(id, 'tap to start'); }, 3000);
    return;
  }
  const wakeWord = tile?.config?.wake_word ?? 'hey computer';
  const args = ['llm', 'assist', '--voice'];
  if (wakeWord !== '') args.push('--wake', wakeWord);
  _vaActive[id] = true;
  _vaSetBall(id, 'listening');
  _vaSetStatus(id, 'starting...');
  setDot(id, 'blue');
  _vaPlaySound('start');
  await startProc('va_' + id, args, { mirror: false });
}

function _vaStop(id) {
  if (!_vaActive[id]) return;
  _vaActive[id] = false;
  stopProc('va_' + id);
  _vaSetBall(id, 'idle');
  _vaSetStatus(id, 'tap to start');
  setDot(id, '');
  _vaPlaySound('stop');
}

// ── Voice Mode: SSE Event Handlers ─────────────────────────────────────────

function _vaEvtVoice(id, evt) {
  if (!_vaActive[id] && evt.state !== 'start') return;
  const prev = _vaPrevState[id];
  switch (evt.state) {
    case 'start':
      _vaActive[id] = true;
      _vaSetBall(id, 'listening');
      _vaSetStatus(id, 'starting...');
      setDot(id, 'blue');
      break;
    case 'wake':
      break;
    case 'idle':
      _vaSetBall(id, 'idle');
      _vaSetStatus(id, 'tap to start');
      _vaActive[id] = false;
      setDot(id, '');
      if (prev && prev !== 'idle') _vaPlaySound('stop');
      break;
    case 'stop':
      _vaSetBall(id, 'idle');
      _vaSetStatus(id, 'tap to start');
      _vaActive[id] = false;
      setDot(id, '');
      if (prev && prev !== 'idle') _vaPlaySound('stop');
      break;
  }
  _vaPrevState[id] = evt.state;
}

function _vaEvtTranscribe(id, evt) {
  if (!_vaActive[id]) return;
  _vaSetBall(id, 'transcribing');
  _vaSetStatus(id, 'transcribing...');
}

function _vaEvtThink(id, evt) {
  if (!_vaActive[id]) return;
  _vaSetBall(id, 'thinking');
  _vaSetStatus(id, evt.heard ? `"${evt.heard}"` : 'thinking...');
  _vaPlaySound('think');
}

function _vaEvtSpeak(id, evt) {
  if (!_vaActive[id]) return;
  _vaSetBall(id, 'speaking');
  _vaSetStatus(id, evt.reply || 'speaking...');
  _vaPlaySound('speak');
}

function _vaEvtError(id, evt) {
  _vaSetBall(id, 'error');
  _vaSetStatus(id, `${evt.stage}: ${evt.msg || 'error'}`);
  _vaPlaySound('error');
  setDot(id, 'red');
  setTimeout(() => {
    if (_vaState[id] === 'error') {
      _vaSetBall(id, 'idle');
      _vaSetStatus(id, 'tap to start');
      setDot(id, '');
    }
  }, 4000);
}

function _vaEvtAudioVoice(id, evt) {
  if (!_vaActive[id]) return;
  if (evt.state === 'recording') {
    _vaSetBall(id, 'recording');
    _vaSetStatus(id, 'recording...');
    _vaPlaySound('recording');
    const ball = document.getElementById(`${id}-ball`);
    if (ball) { ball.style.transform = 'scale(1.05)'; setTimeout(() => { ball.style.transform = ''; }, 150); }
  } else if (evt.state === 'done') {
    _vaSetBall(id, 'transcribing');
    _vaSetStatus(id, 'transcribing...');
    _vaPlaySound('transcribe');
  }
}

function _vaEvtWake(id, evt) {
  if (!_vaActive[id]) return;
  if (evt.state === 'waiting') {
    _vaSetBall(id, 'listening');
    _vaSetStatus(id, evt.word ? `say "${evt.word}"` : 'listening...');
  } else if (evt.heard) {
    _vaSetStatus(id, `"${evt.heard}"`);
    const ball = document.getElementById(`${id}-ball`);
    if (ball) { ball.style.transform = 'scale(1.15)'; setTimeout(() => { ball.style.transform = ''; }, 200); }
  }
}

function _vaEvtStt(id, evt) {
  if (!_vaActive[id]) return;
  if (evt.msg && evt.msg.includes('loading')) {
    _vaSetBall(id, 'loading');
    _vaSetStatus(id, 'loading model...');
  }
}

function _vaEvtTts(id, evt) {
  if (!_vaActive[id]) return;
  if (evt.state === 'done') {
    _vaSetBall(id, 'listening');
    _vaSetStatus(id, 'listening...');
    setDot(id, 'blue');
  }
}

// ── Settings Popover ───────────────────────────────────────────────────────

let _asstCfgPop = null;

async function assistantSettings(id) {
  if (_asstCfgPop) { _asstCloseSettings(); return; }
  const tileEl = tileRefs[id] || document.querySelector(`.tile[data-id="${id}"]`);
  if (!tileEl) return;
  const cfg = await fetch('/api/config?target=llm').then(r => r.json()).catch(() => ({}));
  const tile = dashboard?.tiles?.find(t => t.id === id);

  const pop = document.createElement('div');
  pop.id = 'dp-pop';
  const scroll = document.createElement('div');
  scroll.className = 'dp-scroll';
  scroll.innerHTML = _asstSettingsHTML(id, cfg, tile);
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
  _asstCfgPop = pop;
  setTimeout(() => document.addEventListener('mousedown', _asstCfgOutside, true), 10);
  document.addEventListener('keydown', _asstCfgEsc);
}

function _asstCloseSettings() {
  if (_asstCfgPop) { _asstCfgPop.remove(); _asstCfgPop = null; }
  document.removeEventListener('mousedown', _asstCfgOutside, true);
  document.removeEventListener('keydown', _asstCfgEsc);
}
function _asstCfgOutside(e) {
  if (!_asstCfgPop) return;
  if (e.target.closest?.('[title="LLM settings"]')) return;
  if (!_asstCfgPop.contains(e.target)) _asstCloseSettings();
}
function _asstCfgEsc(e) { if (e.key === 'Escape') _asstCloseSettings(); }

const _ASST_META = `padding:8px 10px 3px;text-transform:uppercase;letter-spacing:.08em;
  font-size:9px;font-weight:700;color:var(--accent)`;
const _ASST_INPUT = `margin-left:auto;width:60%;font-family:var(--mono);font-size:10px;background:var(--surface);
  border:1px solid var(--border2);color:var(--text);border-radius:4px;padding:3px 5px;outline:none;box-sizing:border-box`;

function _asstSettingsHTML(id, cfg, tile) {
  const provider = cfg.provider || 'openrouter';
  const model    = cfg.model || '';
  const api_key  = cfg.api_key || '';
  const api_url  = cfg.api_url || '';
  const providers = ['anthropic','openai','openrouter','ollama'];
  const wakeWord = tile?.config?.wake_word ?? 'hey computer';
  return `
    <div class="dp-meta" style="${_ASST_META};padding-top:4px">llm provider</div>
    <div class="dp-row"><div class="dp-row-pick" style="display:flex;align-items:center" onclick="event.stopPropagation()">
      <span class="dp-row-dim">provider</span>
      <select style="${_ASST_INPUT}" id="asst-provider" onchange="_asstProviderChange()">
        ${providers.map(p => `<option value="${p}" ${p===provider?'selected':''}>${p}</option>`).join('')}
      </select>
    </div></div>
    <div class="dp-row"><div class="dp-row-pick" style="display:flex;align-items:center" onclick="event.stopPropagation()">
      <span class="dp-row-dim">model</span>
      <input style="${_ASST_INPUT}" id="asst-model" value="${_asstEsc(model)}" placeholder="openrouter/free"
        onchange="saveConfigValue('llm','model',this.value)">
    </div></div>
    <div class="dp-row"><div class="dp-row-pick" style="display:flex;align-items:center" onclick="event.stopPropagation()">
      <span class="dp-row-dim">api key</span>
      <input style="${_ASST_INPUT}" id="asst-apikey" type="password" value="${_asstEsc(api_key)}" placeholder="(optional for openrouter free)"
        onchange="saveConfigValue('llm','api_key',this.value)">
    </div></div>
    <div class="dp-row"><div class="dp-row-pick" style="display:flex;align-items:center" onclick="event.stopPropagation()">
      <span class="dp-row-dim">api url</span>
      <input style="${_ASST_INPUT}" id="asst-apiurl" value="${_asstEsc(api_url)}" placeholder="(default)"
        onchange="saveConfigValue('llm','api_url',this.value)">
    </div></div>
    <div class="dp-meta" style="${_ASST_META};border-top:1px solid var(--border2);margin-top:4px">voice</div>
    <div class="dp-row"><div class="dp-row-pick" style="display:flex;align-items:center" onclick="event.stopPropagation()">
      <span class="dp-row-dim">wake word</span>
      <input style="${_ASST_INPUT}" id="asst-wakeword" value="${_asstEsc(wakeWord)}" placeholder="hey computer (empty=always listen)"
        onchange="saveTileConfig('${id}',{wake_word:this.value})">
    </div></div>
    <div style="padding:6px 10px 8px;display:flex;gap:6px;justify-content:flex-end">
      <button class="btn" onclick="_asstCloseSettings()">close</button>
      <button class="btn accent" onclick="_asstSave('${id}')">save</button>
    </div>`;
}

function _asstEsc(s) { return String(s||'').replace(/"/g, '&quot;'); }

async function _asstProviderChange() {
  const sel = document.getElementById('asst-provider');
  const urlEl = document.getElementById('asst-apiurl');
  if (!sel || !urlEl) return;
  const defaults = {
    anthropic: 'https://api.anthropic.com/v1/messages',
    openai: 'https://api.openai.com/v1/chat/completions',
    openrouter: 'https://openrouter.ai/api/v1/chat/completions',
    ollama: 'http://localhost:11434/api/chat'
  };
  if (!await saveConfigValue('llm', 'provider', sel.value)) return false;
  if (!urlEl.value || Object.values(defaults).includes(urlEl.value)) {
    urlEl.value = defaults[sel.value] || '';
    return saveConfigValue('llm', 'api_url', urlEl.value);
  }
  return true;
}

async function _asstSave(id) {
  const provider  = document.getElementById('asst-provider')?.value || '';
  const model     = document.getElementById('asst-model')?.value || '';
  const api_key   = document.getElementById('asst-apikey')?.value || '';
  const api_url   = document.getElementById('asst-apiurl')?.value || '';
  const wakeWord  = document.getElementById('asst-wakeword')?.value ?? 'hey computer';
  const fields = { provider, model, api_key, api_url };
  for (const [path, value] of Object.entries(fields)) {
    if (!await saveConfigValue('llm', path, value)) return;
  }
  const tileSaved = await saveTileConfig(id, { wake_word: wakeWord });
  if (!tileSaved) return;

  _asstCloseSettings();
  const out = document.getElementById(`${id}-output`);
  if (out) out.innerHTML = '';
  _assistantStatus(id);
}
