"""
camera — capture, streaming, and motion detection

Usage:
  camera list
  camera snapshot [name] [--camera NAME]
  camera stream [name] [--camera NAME]
  camera stream-off [name] [--camera NAME]
  camera detect-move [name] [--camera NAME] [--read-fps N] [--emit-fps N] [--once]
  camera listen [name] [--camera NAME] [--read-fps N] [--emit-fps N] [--confidence N]
  help camera | camera help | camera --help

--camera takes precedence over the positional name. Use a configured camera name.
Motion defaults: read 10 fps, emit 5 fps; --once stops after the first motion event.
stream and motion commands run until Ctrl+C (push cameras signal their browser).
"""

import argparse
import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
from logui import logui

SNAPSHOT_DIR     = ".out/snapshots"
STREAM_PIDS_FILE = ".out/stream_pids.json"
MAX_SNAPSHOTS    = 50
DEFAULT_PUSH_FPS = 2.0
DEFAULT_STREAM_WINDOW = 30
DEFAULT_STREAM_FPS = 5


_FRAME_DIR = Path(__file__).parent / ".out"
_STREAM_DIR = _FRAME_DIR / "stream"
_SNAPSHOTS_DIR = _FRAME_DIR / "snapshots"


def sources() -> dict:
    return _cameras() or {}


def push_settings() -> dict:
    try:
        cfg_file = _FRAME_DIR / "camera_config.json"
        cfg = json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
    except Exception:
        cfg = {}
    return {
        "push_fps": float(cfg.get("push_fps") or DEFAULT_PUSH_FPS),
    }


def latest_snapshot(name=None) -> Path:
    pattern = f"{name}_*.jpg" if name else "*.jpg"
    files = sorted((f for f in _SNAPSHOTS_DIR.glob(pattern) if not f.name.startswith("faces_")),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("no snapshot")
    return files[0]


def snapshots(name=None, limit=40) -> list[dict]:
    pattern = f"{name}_*.jpg" if name else "*.jpg"
    files = sorted(_SNAPSHOTS_DIR.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "ts": datetime.fromtimestamp(f.stat().st_mtime).isoformat()}
            for f in files[:limit]]


_viewers: dict[str, set] = {}


def request_view(name: str, user: dict) -> int:
    _viewers.setdefault(name, set()).add(user["id"])
    logui("camera.request", cam=name, by=user.get("name", "?"))
    return len(_viewers[name])


def release_view(name: str, user: dict) -> int:
    if name in _viewers:
        _viewers[name].discard(user["id"])
    viewers = len(_viewers.get(name, set()))
    if viewers == 0:
        logui("camera.release", cam=name, by=user.get("name", "?"))
    return viewers


def force_release(name: str, user: dict):
    _viewers[name] = set()
    logui("camera.release", cam=name, by=user.get("name", "?"), forced="true")


def request_motion(name: str, user: dict):
    logui("camera.motion-request", cam=name, by=user.get("name", "?"))


def stop_motion(name: str, user: dict):
    logui("camera.motion-off", cam=name, by=user.get("name", "?"))


def request_push_snapshot(args) -> bool:
    if args[:2] != ["camera", "snapshot"] or "--camera" not in args:
        return False
    name = args[args.index("--camera") + 1]
    if not is_push_camera(name):
        return False
    logui("camera.snapshot-request", cam=name, source=camera_source(name))
    return True


def _stream_window() -> int:
    try:
        f = _FRAME_DIR / "camera_config.json"
        d = json.loads(f.read_text()) if f.exists() else {}
        return int(d.get("stream_window") or DEFAULT_STREAM_WINDOW)
    except Exception:
        return DEFAULT_STREAM_WINDOW


def _write_stream_frame(name: str, data: bytes) -> Path:
    _STREAM_DIR.mkdir(parents=True, exist_ok=True)
    cam_stream_dir = _STREAM_DIR / name
    cam_stream_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt
    ts   = _dt.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    frame = cam_stream_dir / f"{ts}.jpg"
    tmp = frame.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(frame)
    frames = sorted(cam_stream_dir.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
    for old in frames[:-max(1, _stream_window())]:
        old.unlink(missing_ok=True)
    return frame


def _write_snapshot_frame(name: str, data: bytes) -> Path:
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt
    ts   = _dt.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    snap = _SNAPSHOTS_DIR / f"{name}_{ts}.jpg"
    try:
        snap.write_bytes(data)
        snaps = sorted(_SNAPSHOTS_DIR.glob(f"{name}_*.jpg"), key=lambda f: f.stat().st_mtime)
        for old in snaps[:-50]: old.unlink(missing_ok=True)
    except Exception:
        pass
    return snap


def store_frame(name: str, data: bytes, stream: bool = False) -> Path:
    frame = _write_stream_frame(name, data)
    return frame if stream else _write_snapshot_frame(name, data)


def stream_frame(name: str, i: int = 0) -> Path:
    cam_dir = _STREAM_DIR / name
    if not cam_dir.exists():
        raise FileNotFoundError(f"no frames yet for '{name}'")
    files = sorted(cam_dir.glob("*.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"no frames yet for '{name}'")
    return files[i % len(files)]


CLI_SCHEMA = {"ops": {
    "list": {},
    "snapshot": {"params": ["--camera"]},
    "stream": {"params": ["--camera"]},
    "stream-off": {"params": ["--camera"]},
    "detect-move": {"params": ["--camera", "--read-fps", "--emit-fps", "--once"]},
    "listen": {"params": ["--camera", "--read-fps", "--emit-fps", "--confidence"]},
}}


def cli(argv):
    """Parse camera command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    parser = argparse.ArgumentParser(prog="camera", add_help=False, exit_on_error=False)
    parser.add_argument("action", choices=CLI_SCHEMA["ops"])
    parser.add_argument("name", nargs="?")
    parser.add_argument("--camera", dest="camera_name")
    parser.add_argument("--read-fps", type=float)
    parser.add_argument("--emit-fps", type=float)
    parser.add_argument("--confidence", type=int)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    name = args.camera_name or args.name

    if args.action == "list":
        return list_cameras()
    if args.action == "snapshot":
        return snapshot(name)
    if args.action == "stream":
        return stream(name)
    if args.action == "stream-off":
        return stream_off(name)
    kw = {"name": name}
    if args.read_fps is not None: kw["read_fps"] = args.read_fps
    if args.emit_fps is not None: kw["emit_fps"] = args.emit_fps
    if args.action == "detect-move":
        return detect_move(**kw, continuous=not args.once)
    if args.action == "listen":
        return listen(**kw, confidence=args.confidence)


def _is_network_source(source):
    return isinstance(source, str) and (
        source.startswith("rtsp://") or
        source.startswith("http://") or
        source.startswith("https://")
    )

def _is_push_source(source):
    return isinstance(source, str) and source.startswith("push://")


def snapshot(name=None, on_snapshot=None):
    """
    Capture one frame and save to .out/snapshots/.
    name        — camera name (param → CAMERA env → config → first configured)
    on_snapshot(path) — hook; default: print path
    """
    import cv2
    name, source = _resolve_source(name)
    if _is_push_source(source):
        logui("camera.snapshot-push", cam=name, source=source)
        return None

    cap = None
    try:
        # open capture
        if _is_network_source(source):
            cap = _open_network_cap(cv2, source, name)
            frame = _grab_latest(cap)
        else:
            cap = _open_local_cap(cv2, source, name)
            # local: skip early frames while exposure/white-balance settle —
            # the first frames from many webcams are fully black
            frame = None
            for _ in range(40):
                ret, frame = cap.read()
                if not ret or frame is None:
                    time.sleep(0.05)
                    continue
                if frame.mean() > 15:
                    break
                time.sleep(0.05)
        if frame is None:
            raise Exception(f"camera '{name}' returned no usable frames (check connection/permissions)")
    finally:
        if cap is not None:
            cap.release()

    # Write frame to .out/snapshots
    import cv2
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:-3]
    path = os.path.join(SNAPSHOT_DIR, f"{name}_{ts}.jpg").replace("\\", "/")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])

    # Trim old snapshots
    _trim_snapshots()

    if on_snapshot:
        on_snapshot(path)
    else:
        logui("camera.snap", cam=name, snap=path)
    return path


def stream(name=None, fps=None, **_kwargs):
    """
    Stream camera to the web dashboard. Long-running proc (local/network) or
    one-shot SSE emit (push). Ctrl+C to stop.
    fps — frames per second. Priority: --fps arg → STREAM_FPS env → default 5
    """
    import cv2
    name, source = _resolve_source(name)
    fps         = float(fps or DEFAULT_STREAM_FPS)
    frame_sleep = 1.0 / fps

    logui("camera.stream-start", cam=name, source=source)

    if _is_push_source(source):
        return   # owning browser reacts to stream-start SSE and opens getUserMedia

    cap = None
    try:
        cap = _open(cv2, name)
        logui.print(f"  stream  cam:{name}  fps:{fps}  Ctrl+C to stop")
        while True:
            t0 = time.monotonic()
            ret, frame = cap.read()
            if not ret:
                raise RuntimeError(f"camera '{name}' stream lost")
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                raise RuntimeError(f"camera '{name}' frame encoding failed")
            path = store_frame(name, buf.tobytes(), stream=True)
            logui("camera.snap", cam=name, snap=f"/stream/{name}/{path.name}")
            wait = frame_sleep - (time.monotonic() - t0)
            if wait > 0:
                time.sleep(wait)
    except KeyboardInterrupt:
        logui.print(f"  stopped  cam:{name}")
    finally:
        if cap is not None:
            cap.release()
        logui("camera.stream-stop", cam=name, source=source)


def stream_off(name=None, **_kwargs):
    """Emit stream-stop SSE. Actual proc is killed server-side by /api/stop."""
    name, source = _resolve_source(name)
    logui("camera.stream-stop", cam=name, source=source)


# ── Config resolution ──────────────────────────────────────────────────────────

def _cameras():
    """Return {name: identifier} from devices.json."""
    try:
        import json as _json
        data = _json.loads(open(".out/devices.json", encoding="utf-8").read())
        if data.get("cameras"):
            cams = {}
            for c in data["cameras"]:
                name, ident = c.get("name"), c.get("identifier")
                if not name or ident is None:
                    continue
                prev = cams.get(name)
                if prev is None or (_is_push_source(prev) and not _is_push_source(ident)):
                    cams[name] = ident
            return cams
    except Exception:
        pass
    return None

def camera_source(name):
    if not name:
        return ""
    cams = _cameras()
    if cams and name in cams:
        return cams[name]
    return ""


def is_push_camera(name):
    return str(camera_source(name)).startswith("push://")


def _resolve_source(name):
    source = camera_source(name)
    if source == "":
        cams = _cameras()
        raise Exception(f"camera '{name}' not found — configured: {', '.join(cams or {})}")
    return name, source


# ── Internal helpers ───────────────────────────────────────────────────────────

def _open_network_cap(cv2, source, name):
    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        raise Exception(f"could not open camera '{name}' ({source})")
    return cap

def _open_local_cap(cv2, source, name):
    # Windows: prefer DSHOW — MSMF (CAP_ANY default) causes long hangs on grab failures
    cap = None
    if hasattr(cv2, "CAP_DSHOW"):
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release(); cap = None
    if cap is None:
        cap = cv2.VideoCapture(source, cv2.CAP_ANY)
    if not cap.isOpened():
        raise Exception(f"could not open camera '{name}' ({source})")
    return cap

def _grab_latest(cap):
    """Skip buffered frames, return the freshest. For network streams with FFMPEG buffering."""
    # grab() is cheap (no decode) — skip a few buffered frames
    for _ in range(5):
        cap.grab()
    ret, frame = cap.read()
    return frame if ret else None

def _default_on_move(cam, frame, score):
    logui("camera.motion", cam=cam, score=score)

def _calc_frame_diff(cv2, frame, prev_gray):
    """Convert frame to blurred grayscale and diff against prev_gray. Returns (gray, score)."""
    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray  = cv2.GaussianBlur(gray, (21, 21), 0)
    score = 0
    if prev_gray is not None:
        diff  = cv2.absdiff(prev_gray, gray)
        _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        score = int(th.sum())
    return gray, score

def _trim_snapshots():
    if not os.path.exists(SNAPSHOT_DIR): return
    files = sorted(
        [os.path.join(SNAPSHOT_DIR, f) for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".jpg")],
        key=os.path.getmtime
    )
    for f in files[:-MAX_SNAPSHOTS]:
        os.remove(f)

def _load_pids():
    if os.path.exists(STREAM_PIDS_FILE):
        return json.loads(open(STREAM_PIDS_FILE).read())
    return {}

def _save_pids(pids):
    os.makedirs(".out", exist_ok=True)
    open(STREAM_PIDS_FILE, "w").write(json.dumps(pids))


# ── Open helper ────────────────────────────────────────────────────────────────

def _open(cv2, name):
    """Open a capture for camera name. Handles local/network/push source types."""
    name, source = _resolve_source(name)
    if _is_push_source(source):
        raise Exception(f"camera '{name}' is push:// — capture runs on the owning device")
    if _is_network_source(source):
        return _open_network_cap(cv2, source, name)
    return _open_local_cap(cv2, source, name)


# ── Motion detection ───────────────────────────────────────────────────────────

def detect_move(name=None, on_move=None, score_fn=None,
                continuous=True, read_fps=10, emit_fps=5, stop_event=None):
    """
    Motion detection loop — capture at read_fps, call on_move at most emit_fps.

    on_move(name, frame, score) — downstream hook. the plug-in point.
    score_fn(cv2, frame, state) → float — scoring function (default: score_diff).
        built-ins: score_diff (simple, matches JS), score_absdiff, score_mog2, score_contour_area
        compose:   score_any, score_all, score_median
    continuous — loop until Ctrl+C (True) or fire once (False)
    read_fps   — capture rate (default 10)
    emit_fps   — max on_move callback rate (default 5)
    stop_event — multiprocessing.Event to signal graceful shutdown
    """
    import cv2 as _cv2
    score_fn = score_fn or score_diff
    on_move  = on_move or _default_on_move

    _, source = _resolve_source(name)
    if _is_push_source(source):
        # signal owning browser to start motion loop via SSE
        logui("camera.motion-request", cam=name)
        return None

    cap = _open(_cv2, name)
    is_network = _is_network_source(source)

    frame_sleep  = 1.0 / read_fps
    min_emit_gap = 1.0 / emit_fps
    last_emit    = 0.0
    state        = {}
    last_score   = 0.0

    mode = "continuous" if continuous else "single"
    logui.print(f"  detect-move  cam:{name}  "
                f"read:{read_fps}fps  emit:{emit_fps}fps  mode:{mode}"
                + ("  Ctrl+C to stop" if continuous else ""))

    _warmup(cap, _cv2, score_fn, state, frame_sleep)
    logui("camera.motion-started", cam=name)

    try:
        while True:
            t0 = time.time()

            # network: drain buffered frames, keep only the latest
            if is_network:
                frame = _grab_latest(cap)
            else:
                ret, frame = cap.read()
                if not ret:
                    frame = None
            if frame is None:
                logui.warn("camera.error", msg="stream lost"); break

            score = score_fn(_cv2, frame, state)
            last_score = score

            if score > 0:
                now = time.time()
                if now - last_emit >= min_emit_gap:
                    last_emit = now
                    on_move(name, frame, score)
                    if not continuous:
                        break

            elapsed = time.time() - t0
            wait = frame_sleep - elapsed
            if wait > 0:
                time.sleep(wait)

            if stop_event and stop_event.is_set():
                break

    except KeyboardInterrupt:
        logui.print(f"  stopped  cam:{name}")
    finally:
        cap.release()
    return last_score if not continuous else None


def _warmup(cap, cv2, score_fn, state, frame_sleep):
    """
    Adaptive warmup — feed frames until score_fn settles.
    Min 5 frames, then 3 consecutive stable frames. Cap at 60.
    """
    scores  = []
    settled = 0

    for i in range(60):
        ret, frame = cap.read()
        if not ret:
            break
        s = score_fn(cv2, frame, state)
        scores.append(s)

        if i < 5:
            time.sleep(frame_sleep)
            continue

        mean = sum(scores[-5:]) / len(scores[-5:])
        delta = abs(s - mean)
        if mean < 1 or delta < mean * 0.2:
            settled += 1
        else:
            settled = 0

        if settled >= 3:
            break
        time.sleep(frame_sleep)

    logui.print(f"  warmup done  ({len(scores)} frames)")


# ── Score functions ────────────────────────────────────────────────────────────
#
# All: score_fn(cv2, frame, state) → float   (0–100 normalised)
# state is a mutable dict persisted across calls.
#
# score_diff is the default — matches the JS _diffFrames exactly:
#   mean absolute RGB diff per pixel, normalised 0–100.
#   no blur, no threshold, no grayscale. fast and portable.

def score_diff(cv2, frame, state):
    """Mean absolute RGB diff with EMA baseline subtraction. ~0.5ms/640×480."""
    import numpy as np
    prev = state.get("prev")
    state["prev"] = frame.copy()
    if prev is None:
        return 0.0
    diff = np.abs(frame.astype(np.int16) - prev.astype(np.int16))
    raw  = float(diff.sum()) / (frame.shape[0] * frame.shape[1] * 255 * 3) * 100
    # EMA baseline: slow-adapting noise floor
    ema = state.get("ema", raw)
    ema = ema * 0.98 + raw * 0.02
    state["ema"] = ema
    score = max(0.0, raw - ema)
    return round(score, 2) if score > 0.5 else 0.0


def score_absdiff(cv2, frame, state):
    """Blur + grayscale + binary threshold — % changed pixels. ~1ms/640×480."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (21, 21), 0)
    prev = state.get("gray")
    state["gray"] = gray
    if prev is None:
        return 0.0
    diff  = cv2.absdiff(prev, gray)
    _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    changed = th.sum() / 255
    total   = gray.shape[0] * gray.shape[1]
    return round(100.0 * changed / total, 2)


def score_mog2(cv2, frame, state):
    """Background subtraction — ignores gradual lighting changes. ~3ms/640×480."""
    if "mog" not in state:
        state["mog"] = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=25, detectShadows=True
        )
    small = cv2.resize(frame, (320, 240))
    mask  = state["mog"].apply(small)
    fg    = (mask == 255).sum()
    total = mask.shape[0] * mask.shape[1]
    return round(100.0 * fg / total, 2)


def score_contour_area(cv2, frame, state):
    """Contour area after morphological cleanup — filters noise. ~2ms/640×480."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (21, 21), 0)
    prev = state.get("contour_gray")
    state["contour_gray"] = gray
    if prev is None:
        return 0.0
    diff  = cv2.absdiff(prev, gray)
    _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=2)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN,  kernel, iterations=1)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area  = sum(cv2.contourArea(c) for c in contours)
    total = gray.shape[0] * gray.shape[1]
    return round(100.0 * area / total, 2)


# ── Score combinators ──────────────────────────────────────────────────────────

def score_any(*fns):
    """Max — fires if any scorer sees motion."""
    def _score(cv2, frame, state):
        return max(fn(cv2, frame, state) for fn in fns)
    return _score

def score_all(*fns):
    """Min — fires only if every scorer agrees."""
    def _score(cv2, frame, state):
        return min(fn(cv2, frame, state) for fn in fns)
    return _score

def score_median(*fns):
    """Median vote — robust against single scorer noise."""
    import statistics
    def _score(cv2, frame, state):
        return round(statistics.median(fn(cv2, frame, state) for fn in fns), 2)
    return _score


# ── Ops ────────────────────────────────────────────────────────────────────────

def discover() -> list[dict]:
    """Probe physical camera indices via cv2."""
    import cv2
    found = []
    idx = 0
    while True:
        cap = cv2.VideoCapture(idx)
        try:
            if not cap.isOpened():
                break
            found.append({
                "name":       f"camera-{idx}",
                "identifier": idx,
                "width":      int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height":     int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps":        round(cap.get(cv2.CAP_PROP_FPS), 2),
            })
        finally:
            cap.release()
        idx += 1
    return found


def list_cameras():
    """List configured cameras. Marks active CAMERA from env/config."""
    cams   = _cameras()
    if not cams:
        logui.print("  no cameras configured in devices.json"); return
    for name, source in cams.items():
        logui.print(f"  {name:<15} {source}")


import cv2, queue, threading

def listen(name, confidence=None, on_alert=None,
           score_fn=None, read_fps=10, emit_fps=5):
    """Listen for motion on camera, optionally detect objects/faces."""
    ctx = {
        "_detect_queue": queue.Queue(maxsize=1),
        "_saved": queue.Queue(maxsize=30),
        "_on_alert": on_alert,
        "_name": name,
    }

    def _on_move(cam, frame, score):
        if frame is None:
            return
        from security_protocol import save_snapshot
        path = save_snapshot(frame, SNAPSHOT_DIR, prefix=f"cam_{name}", max_size=30)
        filename = os.path.basename(path) if path else ""
        try:
            ctx["_detect_queue"].put_nowait((frame, score, filename))
        except queue.Full:
            logui("camera.detect", score=score, filename=filename, detect="skip")

    threading.Thread(target=_detect_worker, args=(ctx,), daemon=True).start()
    logui.print(f"  camera listen  cam:{name}  Ctrl+C to stop")

    detect_move(name=name, on_move=_on_move, score_fn=score_fn,
                continuous=True, read_fps=read_fps, emit_fps=emit_fps)


def _detect_worker(ctx):
    from detectors import detect_yolo
    from security_protocol import annotate_frame, save_snapshot

    on_alert = ctx.get("_on_alert")

    while True:
        frame, score, filename = ctx["_detect_queue"].get()
        objects = detect_yolo(frame)
        if not objects:
            continue
        frame = annotate_frame(frame, objects)
        save_snapshot(frame, SNAPSHOT_DIR, prefix="detect", max_size=30)
        logui("camera.detect", score=score, filename=filename, detect=[(d["label"], d["conf"]) for d in objects])
        if on_alert:
            on_alert(ctx, "detect", score, {"objects": objects, "filename": filename})


def _discover():
    import json
    from pathlib import Path
    _OUT = Path(__file__).parent / ".out"
    _DEVICES_F = _OUT / "devices.json"
    cameras = discover()
    if not cameras:
        return
    try:
        existing = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except Exception:
        existing = {}
    existing["cameras"] = cameras
    for c in cameras:
        logui("device.discovered", name=c["name"], identifier=str(c["identifier"]), type="camera")
    _OUT.mkdir(parents=True, exist_ok=True)
    _DEVICES_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")
