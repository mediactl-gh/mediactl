// audio.js

// ══════════════════════════════════════════════════════════════════════════
// DEVICE DISCOVERY
// ══════════════════════════════════════════════════════════════════════════

// Returns { mics: [...], speakers: [...] } with real IP and push:// identifier.
// Called by web.js _postUserDevices().
async function discoverAudioDevices() {
  const ip  = await _getMyIp();
  const raw = await navigator.mediaDevices.enumerateDevices();
  const mics = [], speakers = [];
  raw.forEach(d => {
    const deviceId   = d.deviceId;
    const identifier = `push://${ip}`;
    if (d.kind === 'audioinput') {
      mics.push({
        identifier,
        deviceId,
        description: d.label,
        source:      ip,
      });
    }
    if (d.kind === 'audiooutput') {
      speakers.push({
        identifier,
        deviceId,
        description: d.label,
        source:      ip,
      });
    }
  });
  return { mics, speakers };
}


// ══════════════════════════════════════════════════════════════════════════
// AUDIO TILE
// ══════════════════════════════════════════════════════════════════════════
TILES['audio'] = {
  html(tile) {
    const id    = tile.id;
    const cfg   = tile.config || {};
    const dev   = cfg.device || '';
    const label = cfg.label  || dev || 'default';
    return `
    ${_tileHeader(id,
      `<span onclick="openDevicePicker('${id}', 'audio')" title="Select audio device" style="cursor:pointer">AUDIO</span> <span id="${id}-label" style="font-size:10px;color:var(--text2)">${label}</span>
       <span class="badge red" id="${id}-rec">REC</span>`,
      `<button class="icon-btn" id="${id}-wake-btn" onclick="audioWake('${id}',_tileDev('${id}'))" title="Wake word">
         <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg>
       </button>
       <button class="icon-btn" id="${id}-listen-btn" onclick="audioListenToggle('${id}',_tileDev('${id}'))" title="Listen">
         <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2H3v2a9 9 0 0 0 8 8.94V23h2v-2.06A9 9 0 0 0 21 12v-2z"/></svg>
       </button>`
    )}
    <div style="display:flex;flex-direction:column;gap:8px;padding:0 14px 14px;height:100%;min-height:0">
      <div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0">
        <div class="spark-row" style="padding:0 2px;display:flex;align-items:center;gap:4px">
          <span class="spark-label">NOISE</span>
          ${sparkSVG(id,'noise','amber')}
          <span class="spark-val" id="${id}-sv-noise">—</span>
          <button class="icon-btn" id="${id}-noise-btn" onclick="audioDetectToggle('${id}',_tileDev('${id}'))" title="Noise detect" style="margin-left:auto">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.93 4.93l2.12 2.12M16.95 16.95l2.12 2.12M4.93 19.07l2.12-2.12M16.95 7.05l2.12-2.12"/></svg>
          </button>
        </div>
      </div>
      <div style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:2px" id="${id}-transcript"></div>
    </div>`;
  },

  bind(id) { setDot(id, 'gray'); document.getElementById(`${id}-listen-btn`)?.style.setProperty('color','var(--text3)'); },

  events(id, tile) {
    onEvent('audio', evt => {
      const dev = _tileDev(id);
      // filter by device if configured — match name, index, or label substring
      if (dev && evt.device) {
        const d = evt.device.toLowerCase();
        if (d !== dev.toLowerCase() && !d.includes(dev.toLowerCase())) return;
      }
      if (!document.getElementById(`${id}-transcript`)) return;
      switch (evt.event) {
        case 'listen-start':  _onEvtListenStart(id, evt);      break;
        case 'listen-stop':   _onEvtListenStop(id, evt);       break;
        case 'listen-error':  _audioAddLine(id, '', evt.msg);  break;
        case 'noise-request':  _onEvtNoiseRequest(id, dev);    break;
        case 'noise-started':  _onEvtNoiseStarted(id);         break;
        case 'noise':          _onEvtNoise(id, evt);           break;
        case 'noise-off':      _onEvtNoiseOff(id, dev);        break;
        case 'record-request': _onEvtRecordRequest(id, dev);   break;
        case 'record':         _onEvtRecord(id, evt);          break;
        case 'voice':          _onEvtVoice(id, evt);           break;
        case 'transcript':     _onEvtTranscript(id, evt);      break;
        case 'wake':           _onEvtWake(id, evt);            break;
        case 'stt':            _audioSetState(id, 'transcribing'); break;
      }
    });
  }
};


// ══════════════════════════════════════════════════════════════════════════
// EVENT HANDLERS
// ══════════════════════════════════════════════════════════════════════════

function _onEvtNoise(id, evt) {
  const score = parseFloat(evt.score || evt.rms || 0);
  if (!_audioNoiseLive[id]) {
    _startNoiseTick(id);
    _setNoiseState(id, true);
  }
  _audioNoiseLive[id]?.feed(score);
}

function _onEvtNoiseStarted(id) {
  if (!_audioNoiseLive[id]) _startNoiseTick(id);
  _setNoiseState(id, true);
}

function _onEvtNoiseOff(id, dev) {
  _stopNoiseLoop(dev);
  _clearNoiseTick(id);
  _setNoiseState(id, false);
}

async function _onEvtNoiseRequest(id, dev) {
  const src = _getAudioSources()[dev];
  _audioAddLine(id, '', `req: dev=${dev} src=${src||'?'} keys=${Object.keys(_getAudioSources())}`);
  const type = _sourceType(src);
  if (type !== 'push') { _audioAddLine(id, '', `skip: not push (${type})`); return; }
  const owner = await _isPushOwner(src);
  if (!owner) return;
  if (_audioNoiseLoops[dev]) { _audioAddLine(id, '', `skip: already running`); return; }
  _audioAddLine(id, '', `starting mic loop...`);
  _startNoiseLoop(dev, id);
}

async function _onEvtRecordRequest(id, dev) {
  const src = _getAudioSources()[dev];
  const type = _sourceType(src);
  if (type !== 'push') return;
  if (!await _isPushOwner(src)) return;
  _pushAudioRecord(dev, id);
}

function _onEvtVoice(id, evt) {
  if      (evt.state === 'warmup')    _audioSetState(id, 'warmup');
  else if (evt.state === 'listening') _audioSetState(id, 'listening');
  else if (evt.state === 'recording') _audioSetState(id, 'recording');
  else if (evt.state === 'done')      _audioSetState(id, 'transcribing');
}

function _onEvtTranscript(id, evt) {
  _audioAddLine(id, evt.ts || '', evt.text || '');
  _audioSetState(id, 'listening');
}

function _onEvtRecord(id, evt) {
  _audioAddLine(id, new Date().toLocaleTimeString(), `● ${evt.duration||''} ${evt.path||''}`, evt.path);
  _audioSetState(id, 'listening');
}

function _onEvtWake(id, evt) {
  _audioSetState(id, 'listening');
  _audioAddLine(id, evt.ts || '', `⚡ ${evt.heard||''}`);
  document.getElementById(`${id}-wake-btn`)?.classList.add('active');
}


// ══════════════════════════════════════════════════════════════════════════
// LISTEN / WAKE CONTROLS
// ══════════════════════════════════════════════════════════════════════════

const _audioListening = new Set();
const _audioPushCaptures = new Map();

async function _onEvtListenStart(id, evt) {
  const dev = evt.device;
  _audioListening.add(id);
  _audioSetState(id, 'warmup');
  if (_audioPushCaptures.has(dev)) return;
  const loop = { stopped: false };
  _audioPushCaptures.set(dev, loop);
  if (!await _isPushOwner(evt.source) || loop.stopped) {
    if (_audioPushCaptures.get(dev) === loop) _audioPushCaptures.delete(dev);
    return;
  }
  const socket = loop.socket = new WebSocket(`${location.origin.replace(/^http/, 'ws')}/api/audio/push/${encodeURIComponent(dev)}`);
  socket.onclose = () => { if (_audioPushCaptures.get(dev) === loop) _stopAudioPush(dev); };
  socket.onopen = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: evt.device_id ? { deviceId: { exact: evt.device_id } } : true
      });
      if (loop.stopped) { stream.getTracks().forEach(t => t.stop()); return; }
      loop.stream = stream;
      const ctx = loop.ctx = new AudioContext();
      // Keep the worklet here: batch live mono PCM without a recorder or another file.
      const url = URL.createObjectURL(new Blob([`registerProcessor('audio-capture', class extends AudioWorkletProcessor {
        constructor() { super(); this.pcm = new Int16Array(Math.round(sampleRate * .128)); this.used = 0; }
        process(inputs) {
          for (const sample of inputs[0][0] || []) {
            this.pcm[this.used++] = Math.round(Math.max(-1, Math.min(1, sample)) * 32767);
            if (this.used === this.pcm.length) {
              this.port.postMessage(this.pcm.buffer, [this.pcm.buffer]);
              this.pcm = new Int16Array(Math.round(sampleRate * .128)); this.used = 0;
            }
          }
          return true;
        }
      });`], { type: 'application/javascript' }));
      try { await ctx.audioWorklet.addModule(url); } finally { URL.revokeObjectURL(url); }
      if (loop.stopped) return;
      const node = new AudioWorkletNode(ctx, 'audio-capture', { channelCount: 1, channelCountMode: 'explicit' });
      await new Promise((resolve, reject) => {
        socket.onmessage = resolve;
        socket.addEventListener('close', () => reject(new Error('listening stopped')), { once: true });
        socket.send(JSON.stringify({ ...evt, rate: ctx.sampleRate }));
      });
      node.port.onmessage = ({ data }) => {
        if (loop.stopped) return;
        if (socket.bufferedAmount > 131072) { _stopAudioPush(dev); return; }
        socket.send(data);
      };
      ctx.createMediaStreamSource(stream).connect(node);
      node.connect(ctx.destination); // output stays silent
      stream.getTracks().forEach(track => track.onended = () => _stopAudioPush(dev));
      await ctx.resume();
      if (!loop.stopped) _emitEvent('audio.voice', { device: dev, state: 'listening' });
    } catch (error) {
      if (!loop.stopped) _emitEvent('audio.listen-error', { device: dev, msg: JSON.stringify(error.message) });
      _stopAudioPush(dev);
    }
  };
}

function _stopAudioPush(dev) {
  const loop = _audioPushCaptures.get(dev);
  if (!loop) return;
  loop.stopped = true;
  _audioPushCaptures.delete(dev);
  loop.stream?.getTracks().forEach(t => t.stop());
  loop.ctx?.close().catch(() => {});
  loop.socket?.close();
}

function _onEvtListenStop(id, evt) {
  _stopAudioPush(evt.device || _tileDev(id));
  _audioListening.delete(id);
  _audioSetState(id, 'idle');
}

window.addEventListener('pagehide', () => {
  for (const dev of _audioPushCaptures.keys()) _stopAudioPush(dev);
});

async function audioListenToggle(id, dev) {
  if (_audioListening.has(id)) {
    _audioListening.delete(id);
    _audioSetState(id, 'idle');
    if (_sourceType(_getAudioSources()[dev]) === 'push') {
      _stopAudioPush(dev);
      const args = ['audio', 'listen-off'];
      if (dev) args.push('--device', dev);
      await _runStart(`audio-off_${id}`, args);
    }
    stopProc(`audio_${id}`);
  } else {
    const args = ['audio', 'listen', '--continuous'];
    if (dev)           args.push('--device', dev);
    _audioListening.add(id);
    _audioSetState(id, 'warmup');
    startProc(`audio_${id}`, args);
  }
}

async function audioWake(id, dev) {
  const wakeBtn = document.getElementById(`${id}-wake-btn`);
  if (wakeBtn?.classList.contains('active')) {
    await stopProc(`audio_wake_${id}`);
    wakeBtn.classList.remove('active');
    if (!_audioListening.has(id)) _audioSetState(id, 'idle');
    return;
  }
  const cfg  = dashboard.tiles.find(t => t.id === id)?.config || {};
  const args = ['audio', 'wake'];
  if (cfg.wake_word) args.push('--word', cfg.wake_word);
  if (dev)           args.push('--device', dev);
  await startProc(`audio_wake_${id}`, args);
  wakeBtn?.classList.add('active');
  _audioSetState(id, 'listening');
}


// ══════════════════════════════════════════════════════════════════════════
// STATE & DISPLAY
// ══════════════════════════════════════════════════════════════════════════

const _AUDIO_STATE_DOT = { idle:'gray', warmup:'amber', listening:'amber', recording:'red', transcribing:'gray' };
const _AUDIO_STATES    = ['idle','warmup','listening','recording','transcribing','wake'];

function _audioSetState(id, state) {
  const tile = document.querySelector(`[data-id="${id}"]`);
  if (!tile) return;
  _AUDIO_STATES.forEach(s => tile.classList.remove(`state-${s}`));
  if (state !== 'idle') tile.classList.add(`state-${state}`);
  setDot(id, _AUDIO_STATE_DOT[state] || 'gray');
  const btn = document.getElementById(`${id}-listen-btn`);
  if (btn) btn.style.color = _audioListening.has(id) ? 'var(--accent)' : 'var(--text3)';
}

function _audioAddLine(id, ts, text, wavPath = null) {
  const box = document.getElementById(`${id}-transcript`);
  if (!box) return;
  box.querySelector('div')?.remove(); // remove placeholder
  const row = document.createElement('div');
  row.className = 'transcript-line';
  row.innerHTML = `<span class="ts">${ts}</span><span class="tr-text">${text}</span>` +
    (wavPath ? `<button class="play-btn" onclick="audioPlay('${wavPath}',this)">▶</button>` : '');
  box.appendChild(row);
  while (box.children.length > 10) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

const _players = {};
function audioPlay(path, btn) {
  if (_players[path]) { _players[path].pause(); _players[path] = null; btn.textContent = '▶'; return; }
  const a = new Audio(path); _players[path] = a; btn.textContent = '■';
  a.play(); a.onended = () => { btn.textContent = '▶'; _players[path] = null; };
}


// ══════════════════════════════════════════════════════════════════════════
// NOISE DETECTION
// ══════════════════════════════════════════════════════════════════════════

const _audioNoiseTiles = new Set();
const _audioNoiseLive  = {};   // tile id → sparkLive handle
const _audioNoiseLoops = {};    // dev → { stream, analyser, interval }
const _audioNoiseEma   = {};    // dev → ema baseline

function audioDetectToggle(id, dev) {
  const src  = _getAudioSources()[dev];
  const type = _sourceType(src);

  if (_audioNoiseTiles.has(id)) {
    // stop
    if (_audioNoiseLoops[dev]) _stopNoiseLoop(dev);
    else if (type !== 'push')  _runStop(`detect-noise-${dev}`);
    _emitEvent('audio.noise-off', { device: dev });
    return;
  }

  if (type === 'push') {
    // push mic: signal the owning browser to start its mic loop via SSE
    _emitEvent('audio.noise-request', { device: dev });
  } else {
    // local/server device: Python proc handles it
    _runStart(`detect-noise-${dev}`, ['audio', 'detect-noise', '--device', dev, '--continuous']);
  }
}

function _startNoiseLoop(dev, id) {
  if (_audioNoiseLoops[dev]) return;  // idempotent
  navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
    _startNoiseTick(id);
    const ctx      = new AudioContext({ sampleRate: 16000 });
    const source   = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);

    const interval = setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const raw = Math.min(100, Math.sqrt(sum / buf.length) * 200);
      // EMA baseline + hard floor (matches Python score_rms)
      const ema = _audioNoiseEma[dev] ?? raw;
      _audioNoiseEma[dev] = ema * 0.95 + raw * 0.05;
      const score = Math.max(0, raw - _audioNoiseEma[dev]);
      const final_score = score > 2.0 ? Math.round(score * 100) / 100 : 0;
      _audioNoiseLive[id]?.feed(final_score);
      if (final_score > 0) {
        fetch(`/api/emit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hint: 'audio.noise', device: dev, score: final_score }),
        }).catch(() => {});
      }
    }, 200);  // 5fps

    _audioNoiseLoops[dev] = { stream, ctx, interval };
    _emitEvent('audio.noise-started', { device: dev });
  }).catch(e => console.warn('audio: getUserMedia failed', e));
}

function _stopNoiseLoop(dev) {
  const loop = _audioNoiseLoops[dev];
  if (!loop) return;
  clearInterval(loop.interval);
  loop.stream.getTracks().forEach(t => t.stop());
  loop.ctx.close().catch(() => {});
  delete _audioNoiseLoops[dev];
  delete _audioNoiseEma[dev];
}

function _startNoiseTick(id) {
  _clearNoiseTick(id);
  _audioNoiseLive[id] = sparkLive(id, 'noise', { intervalMs: 200 });
}

function _clearNoiseTick(id) {
  _audioNoiseLive[id]?.end();
  delete _audioNoiseLive[id];
}

function _setNoiseBtnActive(id, on) {
  const btn = document.getElementById(`${id}-noise-btn`);
  if (btn) {
    btn.classList.toggle('active', on);
    btn.title = on ? 'Stop noise detect' : 'Noise detect';
  }
  if (on) _audioNoiseTiles.add(id); else _audioNoiseTiles.delete(id);
}

function _setNoiseState(id, on) {
  _setNoiseBtnActive(id, on);
  const line = document.getElementById(`${id}-spl-noise`);
  const fill = document.getElementById(`${id}-spf-noise`);
  if (line) line.style.opacity = on ? '1' : '0.3';
  if (fill) fill.style.opacity = on ? '1' : '0.15';
  if (on)  { if (!_audioListening.has(id)) setDot(id, 'amber'); }
  else     { _clearNoiseTick(id); if (!_audioListening.has(id)) setDot(id, 'gray'); }
}


// ══════════════════════════════════════════════════════════════════════════
// PUSH AUDIO HELPERS
// ══════════════════════════════════════════════════════════════════════════

async function _pushAudioRecord(dev, id) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    const chunks = [];
    recorder.ondataavailable = e => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(chunks, { type: 'audio/webm' });
      try {
        await fetch(`/api/audio/push/${encodeURIComponent(dev)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'audio/webm' },
          body: blob,
        });
      } catch (e) { console.warn('audio: push record failed', e); }
    };
    _audioSetState(id, 'recording');
    recorder.start();
    // record for max 30s, stop on silence detection TODO
    setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, 30000);
  } catch (e) {
    console.warn('audio: getUserMedia failed', e);
  }
}
