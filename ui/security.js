// security.js

// ══════════════════════════════════════════════════════════════════════════
// SECURITY WATCH
// ══════════════════════════════════════════════════════════════════════════

const _secState = {};
const _secSparkMotion = {};
const _secSparkNoise = {};
const _secDetectStats = {}; // per-tile detect/face counters + tag clouds
const _secLogState = {};

const _SEC_QUIET_MS = 30_000;
const _SEC_REPEAT_COOLDOWN_MS = 60_000;

let _secCfgPop = null;
const _secCfgLabels = {
  move_threshold: 'Motion threshold', noise_threshold: 'Noise threshold', warmup: 'Warmup (seconds)',
  move_count: 'Motion count', noise_count: 'Noise count', window: 'Counting window (seconds)',
  escalate: 'Automatic escalation', escalation_interval: 'Escalation delay (seconds)',
  auto_resolve: 'Auto-resolve known faces', notify: 'Incident notifications',
  object_detector: 'Object detector', face_detector: 'Face detector', face_recognizer: 'Face recognizer',
  object_confidence: 'Object confidence', person_confidence: 'Person confidence',
  face_confidence: 'Minimum face confidence',
  confidence_threshold: 'LBPH recognition threshold',
  person_weight: 'Person weight', object_weight: 'Other object weight',
  face_weight: 'Face detection weight', detection_weight: 'Other detection weight',
  unknown_weight: 'Unknown identity weight', known_weight: 'Known identity weight',
  quality_floor: 'Minimum quality factor', burst_window: 'Burst window (seconds)',
  burst_scale: 'Burst decay scale', age_decay: 'Priority decay (seconds)',
  burst_penalty: 'Maximum burst penalty',
};

async function secOpenConfig(id, anchor) {
  if (_secCfgPop) { _secCfgPop.closePopover(); return; }
  try {
    const response = await fetch('/api/config?target=security');
    if (!response.ok) throw new Error();
    const data = await response.json();
    const sections = {
      sensors: ['sensors.move_threshold', 'sensors.noise_threshold', 'sensors.warmup',
        'triggers.move_count', 'triggers.noise_count', 'triggers.window'],
      incident: ['triggers.escalate', 'triggers.escalation_interval', 'triggers.auto_resolve', 'triggers.notify'],
      detectors: ['detectors.object_confidence', 'detectors.person_confidence', 'detectors.face_confidence',
        'detectors.confidence_threshold', 'detectors.object_detector', 'detectors.face_detector', 'detectors.face_recognizer'],
      score: Object.keys(data.score).map(key => `score.${key}`),
    };
    const html = Object.entries(sections).map(([title, paths], i) => {
      const rows = paths.map(path => {
        const [section, key] = path.split('.');
        const value = data[section][key];
        const opts = data._options[key];
        const disabled = (key === 'confidence_threshold' && data.detectors.face_recognizer !== 'detect_recognize_lbph')
          || (key === 'escalation_interval' && !data.triggers.escalate);
        const attrs = `data-section="${section}" data-key="${key}" aria-label="${_secCfgLabels[key]}" ${disabled ? 'disabled' : ''}`;
        const style = 'margin-left:auto;font-family:var(--mono);font-size:10px;background:var(--surface2);color:var(--text);border:1px solid var(--border2);border-radius:4px;padding:1px 2px;';
        const scoreAttrs = section === 'score'
          ? ['quality_floor', 'burst_penalty'].includes(key) ? 'min="0" max="1" step="0.01"'
            : key.endsWith('_weight') ? 'min="0" step="1"'
            : `min="${['burst_scale', 'age_decay'].includes(key) ? 0.1 : 0}" step="0.1"`
          : '';
        const control = opts
          ? `<select ${attrs} style="${style}">${opts.map(v => `<option value="${v}" ${v === value ? 'selected' : ''}>${v.replace(/^detect_(face_|recognize_)?/, '').replaceAll('_', '-').toUpperCase()}</option>`).join('')}</select>`
          : typeof value === 'boolean'
            ? `<input ${attrs} type="checkbox" style="margin-left:auto" ${value ? 'checked' : ''}>`
            : `<input ${attrs} type="number" value="${value}" ${scoreAttrs || `min="${key.endsWith('_count') ? 1 : 0}"
                ${key.endsWith('_confidence') ? 'max="1" step="0.01"' : key.endsWith('_threshold') && key !== 'confidence_threshold' ? 'step="0.1"' : 'step="1"'}`} style="${style}width:65px">`;
        return `<div class="dp-row"><label class="dp-row-pick" style="display:flex;align-items:center;gap:8px;${disabled ? 'opacity:.5' : ''}"><span class="dp-row-dim">${_secCfgLabels[key]}</span>${control}</label></div>`;
      }).join('');
      return `<div class="dp-meta" style="padding:${i ? '8px' : '4px'} 10px 3px;text-transform:uppercase;letter-spacing:.08em;font-size:9px;font-weight:700;color:var(--accent);opacity:1;${i ? 'border-top:1px solid var(--border2);margin-top:4px' : ''}">${title}</div>${rows}`;
    }).join('');
    const mediaWidth = document.querySelector('.tile-media')?.getBoundingClientRect().width || 320;
    const pop = _mediaPopover(anchor, html, { width:Math.max(200, Math.min(mediaWidth, 320)), placement:'bottom-end',
      onClose:() => { _secCfgPop = null; } });
    _secCfgPop = pop;
    pop.onchange = async ({target}) => {
      const {section, key} = target.dataset;
      if (!key || !target.reportValidity()) return;
      const value = target.type === 'checkbox' ? target.checked : target.type === 'number' ? target.valueAsNumber : target.value;
      if (await saveConfigValue('security', `${section}.${key}`, value)) data[section][key] = value;
      else if (target.type === 'checkbox') target.checked = data[section][key];
      else target.value = data[section][key];
      pop.querySelector('[data-key="escalation_interval"]').disabled = !data.triggers.escalate;
      pop.querySelector('[data-key="confidence_threshold"]').disabled = data.detectors.face_recognizer !== 'detect_recognize_lbph';
      pop.querySelectorAll('label').forEach(el => { el.style.opacity = el.querySelector('input,select').disabled ? '.5' : ''; });
    };
  } catch { toast('Could not load security settings', 'err'); }
}

TILES["security-watch"] = {
  html(tile) {
    const id = tile.id;
    return `
    ${_tileHeader(
      id,
      `SECURITY <span id="${id}-sec-cam" class="sec-dev-pill" onclick="event.stopPropagation();secPickSecurityDevices('${id}','camera')" title="Cameras">…</span><span style="opacity:.5;font-size:10px"> · </span><span id="${id}-sec-mic" class="sec-dev-pill" onclick="event.stopPropagation();secPickSecurityDevices('${id}','audio')" title="Microphones">…</span>`,
      `<span class="status-pill" id="${id}-arm-badge">IDLE</span><button class="icon-btn" onclick="secOpenConfig('${id}',this)" title="Settings" aria-label="Security settings">⚙</button>`,
    )}
    <div style="display:grid;grid-template-columns:3fr 2fr;gap:8px;padding:0 12px 12px;height:100%;min-height:0">
      <div class="cam-view" style="cursor:pointer;min-height:0" id="${id}-snap" onclick="secCamTap('${id}')">
        <div class="cam-idle" id="${id}-idle-state"></div>
        <div class="sec-feed-grid" id="${id}-feed-grid"></div>
        <div class="cam-face-overlay" id="${id}-face-overlay"></div>
        <div class="cam-ts" id="${id}-snap-ts"></div>
      </div>

      <div style="display:grid;grid-template-rows:auto 1fr 1fr;gap:8px;min-height:0">

        <!-- ─────────── 1. CONTROLS ─────────── -->
        <div style="background:var(--surface2);border-radius:8px;padding:14px 12px;display:flex;flex-direction:column;align-items:center;gap:10px">
          <button type="button" class="sec-arm-btn" id="${id}-armwatch-btn"
                  onclick="secToggle('${id}')"
                  style="width:100%;max-width:180px;padding:10px 16px;border-radius:8px;
                         font-family:var(--font);font-size:11px;font-weight:700;
                         letter-spacing:0.14em;text-transform:uppercase;cursor:pointer;
                         transition:all .15s">
            arm
          </button>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;width:100%;max-width:180px">
            <button type="button" id="${id}-resolve-btn" disabled onclick="runOp(['security','resolve','--session',_secLogSession('${id}').sessionId])"
              style="padding:7px 0;border:1px solid color-mix(in srgb, var(--green) 35%, transparent);border-radius:6px;
                     background:rgba(52,209,122,0.08);color:var(--green);font-family:var(--font);
                     font-size:9px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
                     cursor:pointer;transition:all .15s;opacity:.4">resolve</button>
            <button type="button" id="${id}-escalate-btn" disabled onclick="runOp(['security','escalate','--session',_secLogSession('${id}').sessionId])"
              style="padding:7px 0;border:1px solid color-mix(in srgb, var(--red) 35%, transparent);border-radius:6px;
                     background:rgba(247,79,79,0.08);color:var(--red);font-family:var(--font);
                     font-size:9px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
                     cursor:pointer;transition:all .15s;opacity:.4">escalate</button>
          </div>
        </div>

        <!-- ─────────── 2. TELEMETRY (sparklines + detect + faces) ─────────── -->
        <div style="background:var(--surface2);border-radius:8px;padding:10px;display:flex;flex-direction:column;gap:10px;min-height:0;overflow:hidden">

          <!-- Sparklines row -->
          <div style="display:flex;flex-direction:column;gap:4px;flex-shrink:0">
            <div class="spark-row"><span class="spark-label">MOTION</span>${sparkSVG(id, "motion", "amber")}<span class="spark-val" id="${id}-sv-motion">—</span></div>
            <div class="spark-row"><span class="spark-label">NOISE</span>${sparkSVG(id, "noise", "cyan")}<span class="spark-val" id="${id}-sv-noise">—</span></div>
            <div class="spark-row" id="${id}-wake-row" style="display:none">
              <span class="spark-label" style="color:var(--cyan)">WAKE</span>
              <span style="flex:1;font-size:9px;font-family:var(--mono);color:var(--cyan);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" id="${id}-wake-heard"></span>
            </div>
          </div>

          <!-- DETECT subsection -->
          <div style="display:flex;flex-direction:column;gap:4px;min-height:0;flex:1;border-top:1px solid var(--surface3);padding-top:8px">
            <div style="display:flex;justify-content:space-between;align-items:baseline;flex-shrink:0">
              <span style="font-size:9px;color:var(--text3);font-family:var(--mono);letter-spacing:.08em;text-transform:uppercase">DETECT</span>
              <span id="${id}-det-frames" style="font-size:9px;color:var(--text3);font-family:var(--mono)">0 frames</span>
            </div>
            <div id="${id}-det-objects"
                style="display:flex;flex-wrap:wrap;gap:4px;align-content:flex-start;overflow:auto;flex:1;min-height:24px">
              <div style="color:var(--text3);font-family:var(--mono);font-size:9px;opacity:.6">no detections yet</div>
            </div>
          </div>
        </div>

        <!-- ─────────── 3. LOG ─────────── -->
        <div style="display:flex;flex-direction:column;background:var(--surface2);border-radius:8px;padding:8px 10px;overflow:hidden;min-height:0;gap:6px">
          <!-- Pinned incident summary (only visible during active incident) -->
          <div id="${id}-log-pinned" style="display:none;flex-direction:column;gap:3px;
                                            padding:6px 8px;border-radius:6px;
                                            background:color-mix(in srgb, var(--red) 10%, transparent);
                                            border-left:2px solid var(--red);
                                            font-family:var(--mono);font-size:10px;flex-shrink:0">
            <div style="display:flex;justify-content:space-between;align-items:baseline">
              <span id="${id}-log-pin-state" style="color:var(--red);font-weight:700;letter-spacing:.06em">INCIDENT</span>
              <span id="${id}-log-pin-time" style="color:var(--text3);font-size:9px">—</span>
            </div>
            <div id="${id}-log-pin-detail" style="color:var(--text2);font-size:9px;line-height:1.3"></div>
          </div>
          <!-- Scrolling log -->
          <div class="log-feed" id="${id}-log" style="flex:1;min-height:0;overflow:auto;font-family:var(--mono);font-size:10px;line-height:1.5"></div>
        </div>

      </div>
    </div>`;
  },

  bind(id) {
    _secResetDetectStats(id);
    _secResetLogState(id);
    secRefreshSecurityDevicesHeader(id);
    fetch("/api/status")
      .then((r) => r.json())
      .then((d) => {
        if (d.running?.[id]) _secSetArmed(id);
      })
      .catch(() => {});
  },

  events(id) {
    onEvent("security", (evt) => {
      if (!document.getElementById(`${id}-snap`)) return;
      if (evt.event === "detect") {
        _secDetectOnEvent(id, evt);
        return;
      }
      _secLogOnEvent(id, "security", evt);

      if (evt.event === "incident") {
        if (evt.state === "escalated") _secSetAlertTier(id, "escalated");
        if (evt.state === "resolved") _secClearFeedGrid(id);
        else _secFetchThumbs(id, evt.id);
        if (evt.state === "resolved") {
          document.getElementById(id)?.classList.remove("alerting");
          if (_secState[id] !== "idle") _secSetArmed(id);
          else setDot(id, "");
        }
        return;
      }

      switch (evt.event) {
        case "motion": {
          const s = parseFloat(evt.score) || 0;
          _secSparkMotion[id]?.feed(s);
          setDot(id, "amber");
          break;
        }
        case "noise": {
          const n = parseFloat(evt.score ?? evt.rms ?? 0) || 0;
          _secSparkNoise[id]?.feed(n);
          break;
        }
        case "alert":
          sparkPush(id, "alert", 1);
          sparkSetVal(id, "alert", "!");
          _secSetAlertTier(id, "alerted");
          setDot(id, "red");
          document.getElementById(id)?.classList.add("alerting");
          setTimeout(
            () => document.getElementById(id)?.classList.remove("alerting"),
            2000,
          );
          toast("🚨 Security alert", "err");
          break;
        case "arm":
          _secSetArmed(id);
          break;
        case "disarm":
          _secSetIdle(id);
          break;
        case "ui_resolve":
          document.getElementById(id)?.classList.remove("alerting");
          if (_secState[id] !== "idle") _secSetArmed(id);
          else setDot(id, "");
          break;
        case "ui_escalate":
          document.getElementById(id)?.classList.add("alerting");
          setTimeout(
            () => document.getElementById(id)?.classList.remove("alerting"),
            2000,
          );
          _secSetAlertTier(id, "escalated");
          break;
      }
    });

    onEvent("security", (evt) => {
      if (!document.getElementById(`${id}-snap`)) return;
      _secLogOnEvent(id, "security", evt);
      if (
        evt?.event === "state" &&
        String(evt.state || "").toLowerCase() === "escalated"
      ) {
        _secSetAlertTier(id, "escalated");
      }
    });

    onEvent("faces", (evt) => {
      if (!document.getElementById(`${id}-snap`)) return;
      _secLogOnEvent(id, "faces", evt);
      if (evt.name && evt.name !== "motion") _secFaceOverlay(id, evt.name);
    });

    onEvent("audio", (evt) => {
      if (evt.event !== "wake" || !document.getElementById(`${id}-snap`))
        return;
      const row = document.getElementById(`${id}-wake-row`);
      const heard = document.getElementById(`${id}-wake-heard`);
      if (row) row.style.display = "";
      if (heard) heard.textContent = `"${evt.heard || ""}"`;
      setTimeout(() => {
        if (row) row.style.display = "none";
      }, 6000);
      _secLogOnEvent(id, "audio", evt);
    });
  },
};

function _secActive(id) {
  const s = _secState[id];
  return !!s && s !== "idle";
}

function secRefreshSecurityDevicesHeader(id) {
  const tile = dashboard?.tiles?.find((t) => t.id === id);
  const cfg = tile?.config || {};
  const fmt = (arr) =>
    Array.isArray(arr) && arr.length ? arr.join(", ") : "—";
  const cam = document.getElementById(`${id}-sec-cam`);
  const mic = document.getElementById(`${id}-sec-mic`);
  if (cam) cam.textContent = fmt(cfg.securityCameras);
  if (mic) mic.textContent = fmt(cfg.securityAudios);
}

async function secPickSecurityDevices(id, kind) {
  if (_secActive(id)) {
    toast("Security is active", "info");
    return;
  }
  const tile = dashboard?.tiles?.find((t) => t.id === id);
  if (!tile) return;
  if (!tile.config) tile.config = {};
  const key = kind === "camera" ? "securityCameras" : "securityAudios";
  const cur = tile.config[key];
  const keys = Array.isArray(cur) ? cur.slice() : [];
  await openDevicePicker(id, kind === "camera" ? "camera" : "audio", {
    multi: true,
    selectedKeys: keys,
    onApply: async (picked) => {
      tile.config[key] = picked;
      secRefreshSecurityDevicesHeader(id);
      if (typeof saveDashboard === "function") await saveDashboard();
    },
  });
}

function secToggle(id) {
  if (_secActive(id)) _secStop(id);
  else _secStart(id);
}

function _secNewSessionId() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  const ts =
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  return `user_${ts}`;
}

async function _secStart(id) {
  _secSetArmed(id);
  const sessionId = _secNewSessionId();
  _secLogSession(id).sessionId = sessionId;
  const args = [
    "security",
    "listen",
    "--session",
    sessionId,
    "--protocol",
    "default_security_protocol",
  ];
  const tile = dashboard?.tiles?.find((t) => t.id === id);
  const cfg = tile?.config || {};
  const cams = cfg.securityCameras;
  const auds = cfg.securityAudios;
  if (Array.isArray(cams) && cams.length) args.push("--camera", cams.join(","));
  if (Array.isArray(auds) && auds.length) args.push("--audio", auds.join(","));
  if (document.getElementById(`${id}-face-chk`)?.checked) args.push("--face");
  if (document.getElementById(`${id}-transcribe-chk`)?.checked)
    args.push("--transcribe");
  await startProc(id, args);
}

async function _secStop(id) {
  await stopProc(id);
  _secSetIdle(id);
}

function _secSetArmed(id) {
  _secState[id] = "armed";
  const badge = document.getElementById(`${id}-arm-badge`);
  const btn = document.getElementById(`${id}-armwatch-btn`);
  const snap = document.getElementById(`${id}-snap`);
  const idle = document.getElementById(`${id}-idle-state`);
  if (badge) {
    badge.className = "status-pill amber";
    badge.textContent = "ARMED";
  }
  if (btn) {
    btn.textContent = "disarm";
    btn.style.border = "1px solid var(--red)";
    btn.style.color = "var(--red)";
    btn.style.background = "rgba(247,79,79,0.1)";
  }
  const resolveBtn = document.getElementById(`${id}-resolve-btn`);
  const escalateBtn = document.getElementById(`${id}-escalate-btn`);
  if (resolveBtn) {
    resolveBtn.disabled = false;
    resolveBtn.style.opacity = "1";
  }
  if (escalateBtn) {
    escalateBtn.disabled = false;
    escalateBtn.style.opacity = "1";
  }
  if (snap) snap.classList.add("armed");
  if (idle) idle.style.display = "none";
  _secResetDetectStats(id);
  _secResetLogState(id);
  _secClearFeedGrid(id);
  const motionMs =
    1000 /
    Math.max(typeof _getPushFps === "function" ? _getPushFps() : 2, 0.25);
  _secSparkMotion[id] = sparkLive(id, "motion", { intervalMs: motionMs });
  _secSparkNoise[id] = sparkLive(id, "noise", { intervalMs: motionMs });
  setDot(id, "red");
}

function _secSetAlertTier(id, tier) {
  _secState[id] = tier;
  const badge = document.getElementById(`${id}-arm-badge`);
  const esc = tier === "escalated";
  const btn = document.getElementById(`${id}-escalate-btn`);
  if (btn) {
    btn.disabled = esc;
    btn.style.opacity = esc ? ".4" : "1";
  }
  if (badge) {
    badge.className = "status-pill " + (esc ? "red" : "amber");
    badge.textContent = esc ? "ESCALATED" : "ALERTED";
  }
  // Refresh pinned line so its label tracks the tier.
  _secUpdatePinned(id);
  setDot(id, "red");
}

function _secSetIdle(id) {
  _secState[id] = "idle";
  const badge = document.getElementById(`${id}-arm-badge`);
  const btn = document.getElementById(`${id}-armwatch-btn`);
  const snap = document.getElementById(`${id}-snap`);
  const idle = document.getElementById(`${id}-idle-state`);
  if (badge) {
    badge.className = "status-pill";
    badge.textContent = "IDLE";
  }
  if (btn) {
    btn.textContent = "arm";
    btn.style.border = "1px solid var(--accent)";
    btn.style.color = "var(--accent)";
    btn.style.background = "rgba(79,142,247,0.1)";
  }
  const resolveBtn = document.getElementById(`${id}-resolve-btn`);
  const escalateBtn = document.getElementById(`${id}-escalate-btn`);
  if (resolveBtn) {
    resolveBtn.disabled = true;
    resolveBtn.style.opacity = ".4";
  }
  if (escalateBtn) {
    escalateBtn.disabled = true;
    escalateBtn.style.opacity = ".4";
  }
  if (snap) snap.classList.remove("armed");
  if (idle) idle.style.display = "";
  _secClearFeedGrid(id);
  _secHidePinned(id);
  const ts = document.getElementById(`${id}-snap-ts`);
  if (ts) ts.textContent = "";
  setDot(id, "");
  _secSparkMotion[id]?.end();
  _secSparkNoise[id]?.end();
  delete _secSparkMotion[id];
  delete _secSparkNoise[id];
  sparkClear(id, "motion");
  sparkClear(id, "noise");
  _secResetDetectStats(id);
  _secResetLogState(id);
  const log = document.getElementById(`${id}-log`);
  if (log) log.innerHTML = "";
}

function _secFaceOverlay(id, name) {
  const ov = document.getElementById(`${id}-face-overlay`);
  if (!ov) return;
  ov.textContent = name || "unknown";
  ov.classList.add("visible");
  setTimeout(() => ov.classList.remove("visible"), 6000);
}

function secCamTap(id) {
  if (_secState[id] === "armed") {
    const grid = document.getElementById(`${id}-feed-grid`);
    const last = grid?.querySelector("img:last-of-type");
    if (last) openLightbox(last.src);
  } else secToggle(id);
}

// ── Incident preview grid ─────────────────────────────────────────────────────

function _secPreviewThumb(img, src) {
  document.getElementById('sec-thumb-preview')?.remove();
  const rect = img.getBoundingClientRect();
  const ratio = img.naturalWidth / img.naturalHeight || 4 / 3;
  const height = Math.min(360, window.innerHeight - 24, 480 / ratio, (window.innerWidth - 24) / ratio);
  const width = height * ratio;
  const left = Math.max(12, Math.min(rect.left + (rect.width - width) / 2, window.innerWidth - width - 12));
  const top = rect.bottom + 8 + height <= window.innerHeight - 12 ? rect.bottom + 8 : rect.top - height - 8;
  const preview = document.createElement('div');
  preview.id = 'sec-thumb-preview';
  preview.style.cssText = `position:fixed;z-index:9000;pointer-events:none;
    left:${left}px;top:${Math.max(12, Math.min(top, window.innerHeight - height - 12))}px;
    width:${width}px;height:${height}px;background:var(--surface2);border:1px solid var(--border2);
    border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.4)`;
  const image = document.createElement('img');
  image.alt = 'Security snapshot preview';
  image.style.cssText = 'display:block;width:100%;height:100%;object-fit:contain;border-radius:inherit';
  image.src = img.src;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'icon-btn';
  close.textContent = '✕';
  close.setAttribute('aria-label', 'Close preview');
  close.style.cssText = 'position:absolute;top:6px;right:6px;pointer-events:auto;background:rgba(0,0,0,.7);color:#fff';
  close.onclick = () => preview.remove();
  preview.append(image, close);
  document.body.appendChild(preview);
  const full = new Image();
  full.onload = () => { image.src = src; };
  full.src = src;
  setTimeout(() => preview.remove(), 4000);
}

function _secClearFeedGrid(id) {
  const grid = document.getElementById(`${id}-feed-grid`);
  if (!grid) return;
  grid.innerHTML = "";
  grid.classList.remove("visible");
  grid.style.gridTemplateColumns = "";
  grid.style.gridTemplateRows = "";
}

async function _secFetchThumbs(tileId, sessionId) {
  const grid = document.getElementById(`${tileId}-feed-grid`);
  if (!grid || !sessionId) return;
  try {
    const res = await fetch(`/api/security/${sessionId}/thumbs?n=16`);
    if (!res.ok) return;
    const thumbs = await res.json();
    if (!thumbs.length) { _secClearFeedGrid(tileId); return; }

    const cols = Math.min(4, Math.ceil(Math.sqrt(thumbs.length)));
    grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    grid.classList.add("visible");

    grid.innerHTML = "";
    thumbs.forEach((t) => {
      const cell = document.createElement("div");
      cell.className = "sec-feed-cell";
      cell.style.position = "relative";
      const img = document.createElement("img");
      img.src = `data:image/jpeg;base64,${t.thumb}`;
      img.style.opacity = "1";
      cell.appendChild(img);
      cell.onclick = (e) => {
        e.stopPropagation();
        _secPreviewThumb(img, `/security/${encodeURIComponent(sessionId)}/${encodeURIComponent(t.file)}`);
      };
      const label = [...(t.faces || []), ...(t.objects || [])].join(", ");
      if (label) {
        const span = document.createElement("div");
        span.className = "sec-feed-caption";
        span.textContent = t.conf ? `${label} · ${Math.round(t.conf * 100)}%` : label;
        cell.appendChild(span);
      }
      const guest = (t.guests || [])[0];
      if (guest) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "sec-feed-enroll";
        btn.title = `Enroll ${guest}`;
        btn.setAttribute("aria-label", `Enroll ${guest}`);
        btn.innerHTML =
          '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>';
        btn.style.cssText =
          "position:absolute;top:3px;right:3px;width:22px;height:22px;display:flex;align-items:center;justify-content:center;padding:0;border:none;border-radius:50%;background:rgba(0,0,0,.55);color:#fff;cursor:pointer";
        btn.onclick = (e) => {
          e.stopPropagation();
          _secEnrollGuest(tileId, sessionId, guest);
        };
        cell.appendChild(btn);
      }
      grid.appendChild(cell);
    });
  } catch {}
}

function _secEnrollGuest(tileId, sessionId, guest) {
  if (typeof _showModal !== "function") return;
  _showModal(`
    <h3>Enroll face</h3>
    <div class="form-field">
      <label class="form-label" for="sec-enroll-name">Name</label>
      <input type="text" id="sec-enroll-name" class="form-input" autocomplete="off" placeholder="e.g. alice" />
    </div>
    <div id="sec-enroll-err" style="font-size:11px;color:#c44;min-height:16px"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px">
      <button type="button" class="btn" id="sec-enroll-cancel">Cancel</button>
      <button type="button" class="btn accent" id="sec-enroll-go">Enroll</button>
    </div>
  `);
  const nameEl = document.getElementById("sec-enroll-name");
  const errEl = document.getElementById("sec-enroll-err");
  document.getElementById("sec-enroll-cancel").onclick = () => closeModal();
  const go = async () => {
    const name = (nameEl.value || "").trim();
    if (!name) { errEl.textContent = "Name required"; return; }
    try {
      const res = await fetch("/api/faces/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guest, name }),
      });
      const data = await res.json();
      if (data.ok) {
        closeModal();
        toast(`Enrolled ${name}`, "ok");
        _secFetchThumbs(tileId, sessionId);
      } else {
        errEl.textContent = data.error || "Enroll failed";
      }
    } catch {
      errEl.textContent = "Enroll failed";
    }
  };
  document.getElementById("sec-enroll-go").onclick = go;
  nameEl.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } };
  setTimeout(() => nameEl.focus(), 0);
}

// ── Detect + Faces tag clouds ────────────────────────────────────────────────

function _secResetDetectStats(id) {
  _secDetectStats[id] = { frames: 0, objects: {} };
  _secRenderDetectStats(id);
}

function _secDetectOnEvent(id, evt) {
  const s =
    _secDetectStats[id] || (_secDetectStats[id] = { frames: 0, objects: {} });
  if (evt?.event !== "detect") return;
  s.frames += 1;
  const add = (csv) => {
    if (typeof csv !== "string") return;
    csv.split(",").forEach((part) => {
      const lbl = part.split(":")[0]?.trim();
      if (lbl) s.objects[lbl] = (s.objects[lbl] || 0) + 1;
    });
  };
  add(evt.objects);
  add(evt.faces);
  _secRenderDetectStats(id);
}

const _SEC_LABEL_COLORS = [
  "var(--accent)",
  "var(--green)",
  "var(--amber)",
  "var(--cyan)",
  "#c084fc",
  "#f472b6",
  "#fb923c",
  "#a3e635",
  "#60a5fa",
  "#facc15",
];
const _secLabelColorIdx = {};

function _secColorFor(id, label) {
  const m = _secLabelColorIdx[id] || (_secLabelColorIdx[id] = {});
  if (!(label in m))
    m[label] = Object.keys(m).length % _SEC_LABEL_COLORS.length;
  return _SEC_LABEL_COLORS[m[label]];
}

function _secRenderTagCloud(id, host, entries, emptyText) {
  if (!host) return;
  if (!entries.length) {
    host.innerHTML = `<div style="color:var(--text3);font-family:var(--mono);font-size:9px;opacity:.6">${emptyText}</div>`;
    return;
  }

  const prev = {};
  host.querySelectorAll("[data-lbl]").forEach((el) => {
    prev[el.dataset.lbl] = el.dataset.count;
  });

  host.innerHTML = entries
    .map(([lbl, count]) => {
      const col = _secColorFor(id, lbl);
      return `<span data-lbl="${lbl}" data-count="${count}"
      style="display:inline-flex;align-items:baseline;gap:4px;padding:2px 7px;border-radius:10px;
             background:color-mix(in srgb, ${col} 14%, transparent);
             color:${col};font-family:var(--mono);font-size:10px;line-height:1.2;
             transition:background .25s">
      <span>${lbl}</span><span style="opacity:.7">${count}</span>
    </span>`;
    })
    .join("");

  host.querySelectorAll("[data-lbl]").forEach((el) => {
    const lbl = el.dataset.lbl;
    if (prev[lbl] !== el.dataset.count) {
      el.style.background = `color-mix(in srgb, ${el.style.color} 38%, transparent)`;
      setTimeout(() => {
        el.style.background = `color-mix(in srgb, ${el.style.color} 14%, transparent)`;
      }, 250);
    }
  });
}

function _secRenderDetectStats(id) {
  const s = _secDetectStats[id];
  if (!s) return;
  const framesEl = document.getElementById(`${id}-det-frames`);
  if (framesEl)
    framesEl.textContent = `${s.frames} frame${s.frames === 1 ? "" : "s"}`;
  const host = document.getElementById(`${id}-det-objects`);
  const entries = Object.entries(s.objects).sort((a, b) => b[1] - a[1]);
  _secRenderTagCloud(id, host, entries, "no detections yet");
}

// ── Log: simplified, timestamped, with a pinned incident summary ────────────
//
// What changed:
//   - Every line gets HH:MM prefix (mono, dim) so order/timing is obvious.
//   - 'incident opened' is deduped per incident — only the first one logs.
//     Subsequent alert/escalate signals update the pinned summary instead.
//   - During an incident: a pinned strip above the scrolling log shows current
//     state (ALERTED / ESCALATED), opened-at, and last face seen. Cleared on
//     resolve.
//   - Emoji noise stripped. Severity carried by color class only.
//   - Motion/noise/wake/face logging behaviour unchanged but cleaner output.

function _secResetLogState(id) {
  _secLogState[id] = {
    armState: "",
    incidentId: null,
    incidentOpenAt: 0, // ms when current incident opened
    incidentTier: "", // '' | 'alerted' | 'escalated'
    incidentLastFace: "", // last face label seen during incident
    escalatedThis: false,
    openedThis: false, // logged 'incident opened' for this incident yet
    lastMotionAt: 0,
    lastNoiseAt: 0,
    lastUnknownLog: {},
  };
  _secHidePinned(id);
}

function _secLogSession(id) {
  if (!_secLogState[id]) _secResetLogState(id);
  return _secLogState[id];
}

function _shortGuest(g) {
  if (!g) return "unknown";
  const m = String(g).match(/unknown(\d+)/i);
  return m ? `guest ${parseInt(m[1], 10)}` : g;
}

function _secNowHM() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Append a line with timestamp prefix and a severity class. */
function _secLog(id, text, cls = "") {
  const host = document.getElementById(`${id}-log`);
  if (!host) return;
  const line = document.createElement("div");
  line.className = `log-line ${cls || ""}`.trim();
  line.style.cssText = "display:flex;gap:8px;align-items:baseline";
  line.innerHTML = `
    <span style="color:var(--text3);font-size:9px;flex-shrink:0;width:34px">${_secNowHM()}</span>
    <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${text}</span>
  `;
  host.appendChild(line);
  // Keep last ~80 lines
  while (host.children.length > 80) host.removeChild(host.firstChild);
  host.scrollTop = host.scrollHeight;
}

function _secHidePinned(id) {
  const el = document.getElementById(`${id}-log-pinned`);
  if (el) el.style.display = "none";
}

function _secUpdatePinned(id) {
  const ls = _secLogSession(id);
  const root = document.getElementById(`${id}-log-pinned`);
  if (!root) return;

  if (!ls.incidentId) {
    root.style.display = "none";
    return;
  }

  const stateEl = document.getElementById(`${id}-log-pin-state`);
  const timeEl = document.getElementById(`${id}-log-pin-time`);
  const detailEl = document.getElementById(`${id}-log-pin-detail`);

  const tier = (ls.incidentTier || "alerted").toUpperCase();
  const isEsc = ls.incidentTier === "escalated";
  if (stateEl) {
    stateEl.textContent = tier;
    stateEl.style.color = isEsc ? "var(--red)" : "var(--amber)";
  }
  if (timeEl) {
    if (ls.incidentOpenAt) {
      const d = new Date(ls.incidentOpenAt);
      const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      const elapsed = Math.max(
        0,
        Math.floor((Date.now() - ls.incidentOpenAt) / 1000),
      );
      const mm = Math.floor(elapsed / 60),
        ss = elapsed % 60;
      timeEl.textContent = `since ${hm} · ${mm}m${String(ss).padStart(2, "0")}s`;
    } else {
      timeEl.textContent = "";
    }
  }
  if (detailEl) {
    detailEl.textContent = ls.incidentLastFace
      ? `last face: ${ls.incidentLastFace}`
      : "awaiting face match…";
  }
  root.style.borderLeftColor = isEsc ? "var(--red)" : "var(--amber)";
  root.style.background = isEsc
    ? "color-mix(in srgb, var(--red) 12%, transparent)"
    : "color-mix(in srgb, var(--amber) 10%, transparent)";
  root.style.display = "flex";
}

// Tick the elapsed timer on the pinned strip once per second.
setInterval(() => {
  Object.keys(_secLogState).forEach((id) => {
    if (_secLogState[id]?.incidentId) _secUpdatePinned(id);
  });
}, 1000);

function _secLogOnEvent(id, source, evt) {
  if (!evt?.event) return;
  const ls = _secLogSession(id);
  const now = Date.now();

  if (source === "security") {
    switch (evt.event) {
      case "arm":
        if (ls.armState !== "armed") {
          ls.armState = "armed";
          ls.lastMotionAt = ls.lastNoiseAt = 0;
          ls.lastUnknownLog = {};
          _secLog(id, "armed", "red");
        }
        return;

      case "disarm":
        if (ls.armState !== "disarmed") {
          ls.armState = "disarmed";
          _secLog(id, "disarmed", "green");
        }
        return;

      case "incident":
        if (evt.state === "alerted") {
          // Same incident? Just refresh pinned, don't relog.
          if (ls.incidentId && ls.incidentId === evt.id) {
            ls.incidentTier = "alerted";
            _secUpdatePinned(id);
            return;
          }
          // New incident.
          ls.incidentId = evt.id || null;
          ls.incidentOpenAt = now;
          ls.incidentTier = "alerted";
          ls.incidentLastFace = "";
          ls.escalatedThis = false;
          if (!ls.openedThis || ls.incidentId) {
            ls.openedThis = true;
            _secLog(id, "incident opened", "red");
          }
          _secUpdatePinned(id);
        } else if (evt.state === "escalated") {
          ls.incidentTier = "escalated";
          if (!ls.escalatedThis) {
            ls.escalatedThis = true;
            _secLog(id, "escalated", "amber");
          }
          _secUpdatePinned(id);
        } else if (evt.state === "resolved") {
          if (ls.incidentId) _secLog(id, "resolved", "green");
          ls.incidentId = null;
          ls.incidentOpenAt = 0;
          ls.incidentTier = "";
          ls.incidentLastFace = "";
          ls.escalatedThis = false;
          ls.openedThis = false;
          _secHidePinned(id);
        }
        return;

      case "motion":
        if (now - ls.lastMotionAt >= _SEC_QUIET_MS) {
          ls.lastMotionAt = now;
          _secLog(id, "motion", "amber");
        }
        return;

      case "noise":
        if (now - ls.lastNoiseAt >= _SEC_QUIET_MS) {
          ls.lastNoiseAt = now;
          _secLog(id, "noise", "amber");
        }
        return;
    }
    return;
  }

  if (source === "security") return;

  if (source === "faces") {
    if (evt.event === "known") {
      const label = evt.name || "known face";
      if (ls.incidentId) ls.incidentLastFace = label;
      _secLog(id, label, "green");
      _secUpdatePinned(id);
    } else if (evt.event === "unknown_saved") {
      const label = _shortGuest(evt.guest);
      if (ls.incidentId) ls.incidentLastFace = label;
      _secLog(id, `new face — ${label}`, "amber");
      _secUpdatePinned(id);
    } else if (evt.event === "unknown_seen") {
      const g = evt.guest || "";
      const label = _shortGuest(g);
      if (ls.incidentId) ls.incidentLastFace = label;
      const last = ls.lastUnknownLog[g] || 0;
      if (now - last >= _SEC_REPEAT_COOLDOWN_MS) {
        ls.lastUnknownLog[g] = now;
        _secLog(id, `${label} again`, "amber");
      }
      _secUpdatePinned(id);
    } else if (evt.event === "trained") {
      _secLog(id, "model updated", "green");
    } else if (evt.event === "promoted") {
      _secLog(id, `labeled ${evt.name || ""}`, "green");
    }
    return;
  }

  if (source === "audio" && evt.event === "wake") {
    _secLog(id, `"${evt.heard || ""}"`, "cyan");
  }
}
