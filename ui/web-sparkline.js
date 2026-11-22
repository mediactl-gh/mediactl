// web-sparkline.js — spark series + sampled live channels
//
// Layer A (series / paint): sparkPush, sparkSetVal, sparkSVG — no timers.
// Layer B (sampling): sparkLive — max-hold between ticks, one sample per interval, then decay.

const _sparkData = {};
const _SPARK_MAX = 60;
const _sparkLiveByKey = {}; // `${id}-${signal}` → handle (for replacing same channel)

function sparkPush(id, signal, norm) {
  const key = `${id}-${signal}`;
  if (!_sparkData[key]) _sparkData[key] = [];
  _sparkData[key].push(Math.max(0, norm));
  if (_sparkData[key].length > _SPARK_MAX) _sparkData[key].shift();
  _sparkDraw(id, signal);
}

function _sparkDraw(id, signal) {
  const vals = _sparkData[`${id}-${signal}`];
  if (!vals || vals.length < 2) return;
  const line = document.getElementById(`${id}-spl-${signal}`);
  const fill = document.getElementById(`${id}-spf-${signal}`);
  if (!line) return;
  const W = 100, H = 20, n = vals.length;
  const sorted  = [...vals].sort((a, b) => a - b);
  const p95idx  = Math.floor(sorted.length * 0.95);
  const ceiling = Math.max(sorted[p95idx] || 0, 0.001);
  const step    = W / _SPARK_MAX;
  const offsetX = W - n * step;
  const pts = vals.map((v, i) => [offsetX + i * step, H - Math.min(v / ceiling, 1) * H]);
  const d   = pts.map((p, i) => `${i?'L':'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  line.setAttribute('d', d);
  if (fill) {
    const last = pts[pts.length - 1];
    fill.setAttribute('d', d + ` L${last[0].toFixed(1)},${H} L${pts[0][0].toFixed(1)},${H} Z`);
  }
}

function sparkSetVal(id, signal, raw) {
  const el = document.getElementById(`${id}-sv-${signal}`);
  if (el) el.textContent = raw > 999 ? Math.round(raw/1000) + 'k' : (raw || '—');
}

function sparkClear(id, signal) {
  delete _sparkData[`${id}-${signal}`];
  document.getElementById(`${id}-spl-${signal}`)?.setAttribute('d', '');
  document.getElementById(`${id}-spf-${signal}`)?.setAttribute('d', '');
  sparkSetVal(id, signal, 0);
}

function sparkSVG(id, signal, color) {
  return `<svg class="spark-svg" viewBox="0 0 100 22" preserveAspectRatio="none">
    <path class="spark-fill ${color}" id="${id}-spf-${signal}" d=""/>
    <path class="spark-line ${color}" id="${id}-spl-${signal}" d=""/>
  </svg>`;
}

/**
 * Live sampled channel: callers only feed() values while active, end() when done.
 * Max-hold within each interval, then sparkPush + sparkSetVal, then decay to 0 next tick.
 */
function sparkLive(id, signal, { intervalMs }) {
  const key = `${id}-${signal}`;
  _sparkLiveByKey[key]?.end();

  let pending = 0;
  const timer = setInterval(() => {
    const v = pending;
    pending = 0;
    sparkPush(id, signal, v);
    sparkSetVal(id, signal, v);
  }, intervalMs);

  const handle = {
    feed(x) {
      const n = Number(x);
      if (!Number.isFinite(n)) return;
      pending = Math.max(pending, Math.max(0, n));
    },
    end() {
      clearInterval(timer);
      if (_sparkLiveByKey[key] === handle) delete _sparkLiveByKey[key];
      pending = 0;
      sparkSetVal(id, signal, 0);
    }
  };
  _sparkLiveByKey[key] = handle;
  return handle;
}
