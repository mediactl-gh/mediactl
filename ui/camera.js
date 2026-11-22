// camera.js

// ══════════════════════════════════════════════════════════════════════════
// CAMERA FEED
// ══════════════════════════════════════════════════════════════════════════
//
// Source types (from /api/cameras):
//   push://...  → remote browser-owned camera (e.g. iPhone)
//   http(s)://  → network MJPEG stream
//   rtsp://     → network stream
//   integer     → local device index (laptop webcam)
//
// Stream / motion behaviour per type:
//   local  → getUserMedia in this browser; live canvas on tile; post snaps at fps
//   network → <img src=url> for stream; motion not supported
//   push   → signal owning browser via SSE; that browser runs getUserMedia + posts


// ══════════════════════════════════════════════════════════════════════════
// SOURCE RESOLUTION
// ══════════════════════════════════════════════════════════════════════════

let _cameraProcHooksBound = false;

// push://192.168.1.50:8080 → '192.168.1.50'
function _pushSourceIp(source) {
  const m = String(source).match(/^push:\/\/([^:/]+)/);
  return m ? m[1] : '';
}

// true if this browser is the device that owns this push camera
async function _isPushOwner(source) {
  const ip = await _getMyIp();
  return ip && ip === _pushSourceIp(source);
}

function _sourceType(source) {
  const s = String(source ?? '');
  if (s.startsWith('push://'))                                return 'push';
  if (s.startsWith('http://') || s.startsWith('https://') ||
      s.startsWith('rtsp://'))                               return 'network';
  return 'local';
}


// ══════════════════════════════════════════════════════════════════════════
// DEVICE DISCOVERY
// ══════════════════════════════════════════════════════════════════════════

// Returns { cameras: [...] } with real IP and push:// identifier.
// Called by web.js _postUserDevices().
async function discoverCameraDevices() {
  const ip  = await _getMyIp();
  const raw = await navigator.mediaDevices.enumerateDevices();
  const cameras = [];
  raw.filter(d => d.kind === 'videoinput').forEach(d => {
    const deviceId   = d.deviceId;
    // integer-index cameras keep their ordinal as identifier; all others get push://IP
    const isOrdinal  = /^\d+$/.test(deviceId);
    const identifier = isOrdinal ? deviceId : `push://${ip}`;
    cameras.push({
      identifier,
      deviceId,
      description: d.label,
      source:      ip,
    });
  });
  return { cameras };
}


// ══════════════════════════════════════════════════════════════════════════
// CAMERA TILE
// ══════════════════════════════════════════════════════════════════════════

TILES['camera-feed'] = {
  html(tile) {
    const id    = tile.id;
    const cfg   = tile.config || {};
    const cam   = cfg.device || cfg.camera || '';
    const label = cfg.label  || cam || 'camera';
    return `
    ${_tileHeader(id,
      `<span onclick="openDevicePicker('${id}', 'camera')" title="Select camera" style="cursor:pointer">CAMERA</span> <span id="${id}-label" style="font-size:10px;color:var(--text2)">${label}</span>`,
      `<button class="icon-btn" id="${id}-stream-btn" onclick="toggleStream('${id}',_tileDev('${id}'))" title="Stream">
         <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
       </button>
       <button class="icon-btn" onclick="takeSnapshot('${id}',_tileDev('${id}'))" title="Snapshot">
         <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
       </button>`
    )}
    <div style="display:flex;flex-direction:column;gap:6px;padding:0 10px 10px;height:100%;min-height:0">
      <div class="cam-view" id="${id}-wrap" style="flex:1;cursor:zoom-in">
        <div class="cam-idle" id="${id}-empty">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.25"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
        </div>
        <div class="cam-ring" id="${id}-motion-ring"></div>
        <div class="cam-ts"   id="${id}-cam-ts"></div>
      </div>
      <div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0">
        <div class="spark-row" style="padding:0 2px;display:flex;align-items:center;gap:4px">
          <span class="spark-label">MOTION</span>
          ${sparkSVG(id,'motion','amber')}
          <span class="spark-val" id="${id}-sv-motion">—</span>
          <button class="icon-btn" id="${id}-motion-btn" onclick="toggleMotion('${id}',_tileDev('${id}'))" title="Motion detect" style="margin-left:auto">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.93 4.93l2.12 2.12M16.95 16.95l2.12 2.12M4.93 19.07l2.12-2.12M16.95 7.05l2.12-2.12"/></svg>
          </button>
        </div>
        <div class="log-feed" id="${id}-log" style="max-height:36px;overflow:hidden"></div>
      </div>
    </div>`;
  },

  bind() {},

  events(id, tile) {
    if (!_cameraProcHooksBound) {
      _cameraProcHooksBound = true;
      onEvent('proc', evt => {
        if (evt.event !== 'exit') return;
        dashboard?.tiles?.forEach(t => {
          if (t.type !== 'camera-feed') return;
          const tileCam = t.config?.device || t.config?.camera || '';
          const tileId  = t.id;
          if (evt.source === `detect-move-${tileCam}`) {
            if (_motionTiles.has(tileId)) {
              _setMotionState(tileId, false);
              _clearMotionTick(tileId);
            }
            if (!_streamingTiles.has(tileId)) setDot(tileId, '');
          }
          if (evt.source === `stream-${tileCam}`) {
            if (_streamingTiles.has(tileId)) {
              _setCamStreamState(tileId, false);
            }
            if (!_motionTiles.has(tileId)) setDot(tileId, '');
          }
        });
      });
    }

    onEvent('camera', async evt => {
      const cam = _tileDev(id);
      if (cam && evt.cam && evt.cam !== cam) return;
      if (!document.getElementById(`${id}-wrap`)) return;

      if (evt.event === 'snapshot-request' || evt.event === 'snapshot-push') {
        await _onEvtSnapshotRequest(id, evt.cam, evt.source);
        return;
      }
      if (evt.event === 'stream-start') {
        await _onEvtStreamStart(id, evt.cam, evt.source);
        return;
      }
      if (evt.event === 'stream-stop') {
        _onEvtStreamStop(id, evt.cam, evt.source);
        return;
      }

      const type = _sourceType(_getCameraSources()[cam]);

      switch (evt.event) {
        case 'snap':                  _onEvtSnap(id, cam, type, evt);                 break;
        case 'motion-request':   await _onEvtMotionRequest(id, cam, type);            break;
        case 'motion-started':        _onEvtMotionStarted(id);                        break;
        case 'motion-off':            _onEvtMotionOff(id, cam);                       break;
        case 'motion':                _onEvtMotion(id, evt);                          break;
      }
    });
  }
};


// ══════════════════════════════════════════════════════════════════════════
// EVENT HANDLERS
// ══════════════════════════════════════════════════════════════════════════

async function _onEvtSnapshotRequest(id, cam, source) {
  if (_sourceType(source) !== 'push') return;
  // only the first tile on the owner browser captures — all tiles render via SSE snap
  const owner = await _isPushOwner(source);
  if (!owner || _snapPending[cam]) return;
  _snapPending[cam] = true;
  _captureAndPost(cam).finally(() => { delete _snapPending[cam]; });
}

async function _onEvtStreamStart(id, cam, source) {
  _setCamStreamState(id, true);
  setDot(id, 'green');
  switch (_sourceType(source)) {
    case 'network':
      _showNetworkStream(id, source);
      break;
    case 'push':
      if (await _isPushOwner(source)) {
        _startPushLoop(cam, _getPushFps(), async (blob, _prev, imageData) => {
          _showImageDataInTile(id, imageData);
          await _postFrame(cam, blob, 'stream');
        }, id);
      }
      break;
    default:
      break;
  }
}

function _onEvtStreamStop(id, cam, source) {
  _setCamStreamState(id, false);
  setDot(id, '');
  delete _latestSnap[id];
  delete _loadingSnap[id];
  delete _streamStopping[id];
  if (_sourceType(source) === 'network') {
    document.getElementById(`${id}-wrap`)?.querySelector('.cam-stream')?.remove();
  } else {
    _stopPushLoop(cam, id);
  }
  const wrap = document.getElementById(`${id}-wrap`);
  if (wrap) {
    wrap.querySelector('canvas.cam-live')?.remove();
    wrap.querySelector('img.cam-snap')?.remove();
    if (!wrap.querySelector('.cam-idle'))
      wrap.insertAdjacentHTML('beforeend',
        `<div class="cam-idle" id="${id}-empty"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.25"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg></div>`);
  }
}

function _onEvtSnap(id, cam, type, evt) {
  _showSnapshot(id, evt.snap);
}

async function _onEvtMotionRequest(id, cam, type) {
  if (type !== 'push' || !await _isPushOwner(_getCameraSources()[cam])) return;
  if (_pushLoops[cam]) return;  // already running
  _startMotionLoop(cam, id);
}

function _onEvtMotionStarted(id) {
  if (!_motionLive[id]) _startMotionTick(id);
  _setMotionState(id, true);
  if (!_streamingTiles.has(id)) setDot(id, 'amber');
}

function _onEvtMotionOff(id, cam) {
  _stopPushLoop(cam, id);
  _clearMotionTick(id);
  _setMotionState(id, false);
}

function _onEvtMotion(id, evt) {
  const score = parseFloat(evt.score || 0);
  if (!_motionLive[id]) {
    _startMotionTick(id);
    _setMotionState(id, true);
    if (!_streamingTiles.has(id)) setDot(id, 'amber');
  }
  _motionLive[id]?.feed(score);
}


// ══════════════════════════════════════════════════════════════════════════
// STREAM / MOTION CONTROLS
// ══════════════════════════════════════════════════════════════════════════

async function _runStart(procId, args) {
  try {
    await fetch(`/api/start/${encodeURIComponent(procId)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args }),
    });
  } catch (e) { console.warn('_runStart failed', e); }
}

async function _runStop(procId) {
  try {
    await fetch(`/api/stop/${encodeURIComponent(procId)}`, { method: 'POST' });
  } catch (e) { console.warn('_runStop failed', e); }
}

function toggleStream(id, cam) {
  const source = _getCameraSources()[cam];
  const type = _sourceType(source);
  if (_streamingTiles.has(id)) {
    _setCamStreamState(id, false);
    setDot(id, '');
    _streamStopping[id] = true;
    switch (type) {
      case 'push':
        // push: browser owns the loop — stop it locally and signal all dashboards
        _stopPushLoop(cam, id);
        _emitEvent('camera.stream-stop', { cam, source });
        break;
      default:
        _runStop(`stream-${cam}`);
        _runStart(`stream-off-${cam}`, ['camera', 'stream-off', '--camera', cam]);
        break;
    }
  } else {
    switch (type) {
      case 'push':
        // push: signal the owning browser to start its loop via SSE
        _emitEvent('camera.stream-start', { cam, source });
        break;
      default:
        _runStart(`stream-${cam}`, ['camera', 'stream', '--camera', cam]);
        break;
    }
  }
}

function toggleMotion(id, cam) {
  const type = _sourceType(_getCameraSources()[cam]);
  if (_motionTiles.has(id)) {
    switch (type) {
      case 'push':
        _stopPushLoop(cam, id);
        break;
      default:
        _runStop(`detect-move-${cam}`);
        break;
    }
    _emitEvent('camera.motion-off', { cam });
    return;
  }
  // start
  _setMotionBtnActive(id, true);
  switch (type) {
    case 'push':
      // push: signal the owning browser to start motion loop via SSE
      _emitEvent('camera.motion-request', { cam });
      break;
    default:
      _runStart(`detect-move-${cam}`, ['camera', 'detect-move', '--camera', cam]);
      break;
  }
}

function takeSnapshot(id, cam) {
  runOp(['camera', 'snapshot'].concat(cam ? ['--camera', cam] : []));
}


// ══════════════════════════════════════════════════════════════════════════
// MOTION SPARK — sparkLive (web-sparkline.js)
// ══════════════════════════════════════════════════════════════════════════

const _motionLive = {};   // tile id → { feed, end }

function _startMotionTick(id) {
  _clearMotionTick(id);
  const ms = 1000 / Math.max(_getPushFps(), 0.25);
  _motionLive[id] = sparkLive(id, 'motion', { intervalMs: ms });
}

function _clearMotionTick(id) {
  _motionLive[id]?.end();
  delete _motionLive[id];
}


// ══════════════════════════════════════════════════════════════════════════
// PUSH LOOP
// ══════════════════════════════════════════════════════════════════════════

const _pushLoops  = {};   // cam → intervalId
const _pushStreams = {};   // cam → MediaStream
const _pushCanvas = {};   // cam → { canvas, ctx, video }
const _pushPrev   = {};   // cam → previous ImageData
const _loopOwners = {};   // cam → tileId that started the loop

async function _startPushLoop(cam, fps, onFrame, tileId) {
  _stopPushLoop(cam);

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
  } catch (e) {
    console.warn('camera: getUserMedia failed', e); return;
  }
  _pushStreams[cam] = stream;
  if (tileId) _loopOwners[cam] = tileId;

  const video = document.createElement('video');
  video.srcObject = stream;
  video.setAttribute('playsinline', '');
  await video.play();

  const canvas  = document.createElement('canvas');
  canvas.width  = video.videoWidth  || 640;
  canvas.height = video.videoHeight || 480;
  const ctx = canvas.getContext('2d');
  _pushCanvas[cam] = { canvas, ctx, video };

  const interval = 1000 / (fps || _getPushFps());
  _pushLoops[cam] = setInterval(async () => {
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const prev      = _pushPrev[cam] || null;
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    _pushPrev[cam]  = imageData;
    const blob = await _canvasToBlob(canvas);
    if (blob) onFrame(blob, prev, imageData);
  }, interval);
}

function _stopPushLoop(cam, tileId) {
  if (tileId && _loopOwners[cam] && _loopOwners[cam] !== tileId) return;
  if (_pushLoops[cam])   { clearInterval(_pushLoops[cam]);  delete _pushLoops[cam]; }
  if (_pushStreams[cam]) { _pushStreams[cam].getTracks().forEach(t => t.stop()); delete _pushStreams[cam]; }
  delete _pushCanvas[cam];
  delete _pushPrev[cam];
  delete _loopOwners[cam];
  delete _diffEma[cam];
  if (tileId) {
    document.getElementById(`${tileId}-wrap`)?.querySelector('canvas.cam-live')?.remove();
  }
}

function _startMotionLoop(cam, id) {
  _startMotionTick(id);
  const motionFps = Math.max(_getPushFps(), 5);
  _startPushLoop(cam, motionFps, async (blob, prev, imageData) => {
    const score = _diffFrames(imageData, prev, cam);
    _motionLive[id]?.feed(score);
    if (score > 0) _postMotionScore(cam, score);
  }, id);
  // notify all dashboards that motion loop is running
  _emitEvent('camera.motion-started', { cam });
}

function _postMotionScore(cam, score) {
  fetch(`/api/camera/push/${encodeURIComponent(cam)}?mode=motion&score=${score}`, {
    method: 'POST',
  }).catch(() => {});
}

function _emitEvent(hint, data) {
  fetch('/api/emit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hint, ...data }),
  }).catch(() => {});
}


// ══════════════════════════════════════════════════════════════════════════
// FRAME HELPERS
// ══════════════════════════════════════════════════════════════════════════

async function _postFrame(cam, blob, mode = 'snap', score = 0) {
  try {
    const response = await fetch(`/api/camera/push/${encodeURIComponent(cam)}?mode=${mode}&score=${score}`, {
      method:  'POST',
      headers: { 'Content-Type': 'image/jpeg' },
      body:    blob,
    });
    if (!response.ok) throw new Error(`Upload failed: HTTP ${response.status}`);
  } catch (e) {
    console.warn('camera: _postFrame failed', e);
    _emitEvent('camera.error', { cam, stage: 'upload', error: e.name, msg: JSON.stringify(e.message || String(e)) });
  }
}

const _diffEma = {};  // cam → ema baseline

function _diffFrames(curr, prev, cam) {
  if (!prev || !curr) return 0;
  const a = curr.data, b = prev.data;
  let diff = 0;
  for (let i = 0; i < a.length; i += 4) {
    diff += Math.abs(a[i]   - b[i])
          + Math.abs(a[i+1] - b[i+1])
          + Math.abs(a[i+2] - b[i+2]);
  }
  const raw = (diff / (a.length / 4 * 255 * 3)) * 100;
  // EMA baseline: slow-adapting noise floor
  const ema = _diffEma[cam] ?? raw;
  _diffEma[cam] = ema * 0.98 + raw * 0.02;
  const score = Math.max(0, raw - _diffEma[cam]);
  return score > 0.05 ? Math.round(score * 100) / 100 : 0;
}

async function _captureAndPost(cam) {
  let stream;
  let stage = 'camera-access';
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    const video  = document.createElement('video');
    video.srcObject = stream;
    video.setAttribute('playsinline', '');
    stage = 'video-play';
    await video.play();
    stage = 'capture';
    const canvas  = document.createElement('canvas');
    canvas.width  = video.videoWidth  || 640;
    canvas.height = video.videoHeight || 480;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    stream.getTracks().forEach(t => t.stop());
    stream = null;
    stage = 'encode';
    const blob = await _canvasToBlob(canvas);
    if (!blob) throw new Error('Camera frame could not be encoded');
    stage = 'upload';
    await _postFrame(cam, blob, 'snap');
  } catch (e) {
    console.warn('camera: _captureAndPost failed', e);
    _emitEvent('camera.error', { cam, stage, error: e.name, msg: JSON.stringify(e.message || String(e)) });
  } finally {
    stream?.getTracks().forEach(t => t.stop());
  }
}

function _canvasToBlob(canvas) {
  return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.85));
}


// ══════════════════════════════════════════════════════════════════════════
// UI HELPERS
// ══════════════════════════════════════════════════════════════════════════

const _streamingTiles = new Set();
const _motionTiles    = new Set();

const _latestSnap     = {};
const _loadingSnap    = {};
const _streamStopping = {};  // id → true while waiting for proc to die
const _snapPending    = {};  // cam → true while capture in flight (dedup across tiles)

function _showSnapshot(id, path) {
  if (_streamStopping[id]) return;
  const wrap = document.getElementById(`${id}-wrap`);
  if (!wrap) return;
  document.getElementById(`${id}-empty`)?.remove();
  const clean = (path || '').replace(/\\/g, '/').replace(/^\.out\//, '/');
  _latestSnap[id] = clean;
  if (_loadingSnap[id]) return;
  _loadingSnap[id] = true;

  let img = wrap.querySelector('img.cam-snap');
  if (!img) {
    img = document.createElement('img');
    img.className = 'cam-snap';
    img.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:cover;';
    wrap.insertBefore(img, wrap.firstChild);
  }

  // track what we set so the onload check compares apples-to-apples
  let loadingSrc = clean;

  img.onload = img.onerror = () => {
    const latest = _latestSnap[id];
    if (latest && latest !== loadingSrc) {
      loadingSrc = latest;
      img.src = latest;
    } else {
      _loadingSnap[id] = false;
    }
  };

  img.src = clean;
  const ts = document.getElementById(`${id}-cam-ts`);
  if (ts) ts.textContent = new Date().toLocaleTimeString();
}

function _showNetworkStream(id, source) {
  const wrap = document.getElementById(`${id}-wrap`);
  if (!wrap) return;
  document.getElementById(`${id}-empty`)?.remove();
  let live = wrap.querySelector('.cam-stream');
  if (!live) {
    live = document.createElement('img');
    live.className = 'cam-stream';
    live.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:cover;';
    wrap.appendChild(live);
  }
  live.src = source;
}

function _showCanvasInTile(id, canvas) {
  const wrap = document.getElementById(`${id}-wrap`);
  if (!wrap) return;
  document.getElementById(`${id}-empty`)?.remove();
  let img = wrap.querySelector('img:not(.cam-stream)');
  if (!img) { img = document.createElement('img'); wrap.insertBefore(img, wrap.firstChild); }
  img.src = canvas.toDataURL('image/jpeg', 0.85);
  const ts = document.getElementById(`${id}-cam-ts`);
  if (ts) ts.textContent = new Date().toLocaleTimeString();
}

function _showImageDataInTile(id, imageData) {
  const wrap = document.getElementById(`${id}-wrap`);
  if (!wrap) return;
  document.getElementById(`${id}-empty`)?.remove();
  let cv = wrap.querySelector('canvas.cam-live');
  if (!cv) {
    cv = document.createElement('canvas');
    cv.className   = 'cam-live';
    cv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:cover;';
    wrap.appendChild(cv);
  }
  if (cv.width  !== imageData.width)  cv.width  = imageData.width;
  if (cv.height !== imageData.height) cv.height = imageData.height;
  cv.getContext('2d').putImageData(imageData, 0, 0);
  const ts = document.getElementById(`${id}-cam-ts`);
  if (ts) ts.textContent = new Date().toLocaleTimeString();
}

function _setCamStreamState(id, on) {
  const btn = document.getElementById(`${id}-stream-btn`);
  if (btn) {
    btn.classList.toggle('active', on);
    btn.title = on ? 'Stop stream' : 'Stream';
    btn.innerHTML = on
      ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
  }
  if (on) _streamingTiles.add(id); else _streamingTiles.delete(id);
}

function _setMotionBtnActive(id, on) {
  const btn = document.getElementById(`${id}-motion-btn`);
  if (btn) {
    btn.classList.toggle('active', on);
    btn.title = on ? 'Stop motion' : 'Motion detect';
  }
  if (on) _motionTiles.add(id); else _motionTiles.delete(id);
}

function _setMotionState(id, on) {
  _setMotionBtnActive(id, on);
  const line = document.getElementById(`${id}-spl-motion`);
  const fill = document.getElementById(`${id}-spf-motion`);
  if (line) line.style.opacity = on ? '1' : '0.3';
  if (fill) fill.style.opacity = on ? '1' : '0.15';
  // dot off on stop (don't override green stream dot)
  if (!on && !_streamingTiles.has(id)) setDot(id, '');
}
