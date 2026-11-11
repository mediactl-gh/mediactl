"""
security — arm/disarm, signal listeners, and incident sessions

Usage:
  security arm [--camera NAMES] [--audio NAMES]
               [--move-threshold N] [--noise-threshold N]
  security disarm | stop | status
  security listen [--camera NAMES] [--audio NAMES] [--session [ID]]
                  [--protocol FUNCTION] [--move-threshold N] [--noise-threshold N]
  security listen-stop [--session ID]
  security resolve [--session ID]
  security escalate [--session ID]

listen-stop without a session stops all sessions. Protocol functions are resolved
from security_protocol; omitting --protocol uses the listener's default.

Help: help security | security --help
"""

import argparse
import os, sys, json, tempfile, time, threading, math
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from logui import logui
import security_protocol

SNAPSHOT_DIR = ".out/snapshots"
SESSIONS_DIR = ".out/security"
_sessions      = {}
_sessions_lock = threading.Lock()
_arm_stop      = threading.Event()


SECURITY_FILE = Path(__file__).resolve().parent / ".out/security.json"

_ctx_lock = threading.Lock()
_store_lock = threading.Lock()
_RUNTIME_KEYS = ("session_id", "session_dir", "pid", "started_at", "armed", "state", "incident", "cameras", "audios")


def read_security():
    try:
        data = json.loads(SECURITY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    for section, defaults in CONFIG_DEFAULTS.items():
        data[section] = {**defaults, **data.get(section, {})}
    data.setdefault("runtime", {"pid": None, "armed": False, "state": "idle", "incident": None})
    return data


@contextmanager
def edit_security():
    """Serialize settings and state updates across the web and security processes."""
    SECURITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock, open(SECURITY_FILE.with_suffix(".lock"), "a+") as lock:
        lock.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        tmp = None
        try:
            data = read_security()
            yield data
            fd, tmp = tempfile.mkstemp(dir=SECURITY_FILE.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, SECURITY_FILE)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
            lock.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _pid_alive(pid):
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_ctx(session_id=None):
    data = read_security()
    runtime = data["runtime"]
    if session_id and runtime.get("session_id") != session_id:
        return None
    return {**data["triggers"], **data["detectors"], **data["score"], **runtime, "state_context": _state_context}


def start_session(ctx):
    with edit_security() as data:
        if _pid_alive(data["runtime"].get("pid")):
            raise RuntimeError("security is already running")
        data["runtime"] = {key: ctx.get(key) for key in _RUNTIME_KEYS}


@contextmanager
def _state_context(ctx):
    """Refresh live state before each incident operation and persist it afterwards."""
    with _ctx_lock, edit_security() as data:
        runtime = data["runtime"]
        current = (runtime.get("session_id"), runtime.get("pid")) == (ctx.get("session_id"), ctx.get("pid"))
        if current:
            if (ctx.get("incident") or {}).get("id") != (runtime.get("incident") or {}).get("id"):
                ctx["alert_windows"] = {}
                timer = ctx.get("escalation_timer")
                if timer:
                    timer.cancel()
                ctx["escalation_timer"] = None
            ctx.update(runtime)
        else:
            ctx.update(armed=False, state="idle", incident=None)
        yield
        if current:
            data["runtime"] = {key: ctx.get(key) for key in _RUNTIME_KEYS}


def end_session(ctx):
    with _state_context(ctx):
        timer = ctx.get("escalation_timer")
        if timer:
            timer.cancel()
        ctx.update(pid=None, armed=False, state="idle", incident=None)

CONFIG_DEFAULTS = {
    "sensors": {"move_threshold": 2.0, "noise_threshold": 5.0, "warmup": 3},
    "triggers": {"move_count": 3, "noise_count": 5, "window": 30,
                 "escalate": True, "escalation_interval": 20, "auto_resolve": False, "notify": False,
                 "max_snapshot_size": 12},
    "detectors": {"object_detector": "detect_yolo", "face_detector": "detect_face_yunet",
                  "face_recognizer": "detect_recognize_sface", "object_confidence": 0.8,
                  "person_confidence": 0.8, "face_confidence": 0.8, "face_min_size": 80,
                  "confidence_threshold": 80},
    "score": {"person_weight": 70, "object_weight": 30, "face_weight": 40,
              "detection_weight": 25, "unknown_weight": 100, "known_weight": 85,
              "quality_floor": 0.2, "burst_window": 2.0, "burst_scale": 6.0,
              "age_decay": 15.0, "burst_penalty": 0.15},
}
CONFIG_OPTIONS = {
    "object_detector": ["detect_yolo", "detect_yolo_s"],
    "face_detector": ["detect_face_yunet", "detect_face_ssd"],
    "face_recognizer": ["detect_recognize_sface", "detect_recognize_lbph"],
}


def settings():
    data = read_security()
    return {section: data[section] for section in CONFIG_DEFAULTS}


_THUMB_W = 240


def update_setting(path: str, value):
    section, _, key = path.partition(".")
    default = CONFIG_DEFAULTS.get(section, {}).get(key)
    if default is None:
        raise ValueError("unknown security setting")
    if key in CONFIG_OPTIONS:
        valid = value in CONFIG_OPTIONS[key]
    elif isinstance(default, bool):
        valid = type(value) is bool
    else:
        valid = (type(value) in (int, float) and math.isfinite(value)
                 and value >= (1 if key.endswith("_count") or key == "face_min_size" else 0)
                 and (not isinstance(default, int) or float(value).is_integer())
                 and (not key.endswith("_confidence") or value <= 1))
        if section == "score":
            valid = (valid
                     and (key not in ("burst_scale", "age_decay") or value > 0)
                     and (key not in ("quality_floor", "burst_penalty") or value <= 1))
    if not valid:
        raise ValueError("invalid security setting")
    with edit_security() as data:
        data[section][key] = value


def session_thumbnails(session_id: str, n: int = 16) -> list[dict]:
    import cv2, base64
    from security_protocol import _detected_score

    session_dir = SECURITY_FILE.parent / "security" / session_id
    detect_file = session_dir / "detect.json"

    detected_entries = []
    if detect_file.exists():
        try:
            data = json.loads(detect_file.read_text(encoding="utf-8"))
            for fname, entry in data.get("detected", {}).items():
                if not isinstance(entry, dict):
                    continue
                img_path = session_dir / fname
                if not img_path.exists():
                    continue
                score = entry.get("score") or _detected_score(entry, settings()["score"])
                face_labels = [f.get("label", "") for f in (entry.get("faces") or []) if f.get("label")]
                object_labels = [o.get("label", "") for o in (entry.get("objects") or []) if o.get("label")]
                guests = [f.get("label") for f in (entry.get("faces") or []) if f.get("known") is False]
                confs = [c for c in ([o.get("conf") for o in (entry.get("objects") or [])]
                                     + [d.get("conf") for d in (entry.get("detections") or [])]) if c is not None]
                conf = round(max(confs), 2) if confs else 0
                detected_entries.append({"file": fname, "score": score, "faces": face_labels, "objects": object_labels, "guests": guests, "conf": conf, "img": img_path})
        except Exception:
            pass

    detected_entries.sort(key=lambda x: -x["score"])

    seen_faces = set()
    deduped = []
    for e in detected_entries:
        primary_face = next((f for f in e["faces"] if f and f != "unknown"), None)
        if primary_face:
            if primary_face in seen_faces:
                continue
            seen_faces.add(primary_face)
        deduped.append(e)
    detected_entries = deduped

    detected_files = {e["file"] for e in detected_entries}
    recent_snaps = []
    try:
        for f in session_dir.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png") and f.name.startswith("snap_") and f.name not in detected_files:
                recent_snaps.append(f)
        recent_snaps.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        pass

    merged = list(detected_entries)
    for snap in recent_snaps:
        if len(merged) >= n:
            break
        merged.append({"file": snap.name, "score": 0, "faces": [], "objects": [], "guests": [], "conf": 0, "img": snap})

    thumbs = []
    for e in merged[:n]:
        try:
            img = cv2.imread(str(e["img"]))
            if img is None:
                continue
            h, w = img.shape[:2]
            thumb_h = int(h * _THUMB_W / w)
            thumb = cv2.resize(img, (_THUMB_W, thumb_h))
            _, buf = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 75])
            thumbs.append({
                "file": e["file"],
                "score": round(e["score"], 1),
                "faces": e["faces"],
                "objects": e["objects"],
                "guests": e.get("guests", []),
                "conf": e.get("conf", 0),
                "thumb": base64.b64encode(buf).decode(),
            })
        except Exception:
            continue

    return thumbs


def _on_exit(signum, frame):
    stop_all()

def _install_signal_handler():
    import signal
    old_sigint = signal.getsignal(signal.SIGINT)
    old_sigterm = signal.getsignal(signal.SIGTERM)
    def _handler(s, f):
        stop_all()
        if callable(old_sigint): old_sigint(s, f)
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


# ── Arm / Disarm ───────────────────────────────────────────────────────────────

def is_armed(session_id=None):
    ctx = _read_ctx(session_id)
    return bool(ctx and ctx.get("armed") and _pid_alive(ctx.get("pid")))

def arm(
    cameras         = None,
    audios          = None,
    move_threshold  = None,
    noise_threshold = None,
    warmup          = None,
    on_alert        = None,
):
    _arm_stop.clear()
    protocol = on_alert or security_protocol.default_security_protocol
    sid = listen(
        cameras=cameras, audios=audios,
        move_threshold=move_threshold, noise_threshold=noise_threshold,
        warmup=warmup, on_alert=protocol, session=True,
    )
    logui("security.arm", session=sid, at=datetime.now().isoformat()[:19])
    try: import notify; notify.armed()
    except Exception: pass
    try:
        while not _arm_stop.is_set():
            _arm_stop.wait(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_all()

def disarm(session_id=None):
    if session_id is None:
        with _sessions_lock:
            for sid, entry in _sessions.items():
                if entry.get("ctx", {}).get("armed"):
                    session_id = sid
                    break
    if session_id:
        stop(session_id)
    else:
        stop_all()
    _arm_stop.set()
    logui("security.disarm")
    try: import notify; notify.disarmed()
    except Exception: pass

def resolve(session_id=None):
    ctx = _read_ctx(session_id)
    if not ctx or not ctx.get("incident"):
        logui.warn("security.resolve", session=session_id or "", msg="no incident")
        return
    with _state_context(ctx):
        security_protocol._incident_resolve(ctx)

def escalate(session_id=None):
    ctx = _read_ctx(session_id)
    inc = ctx.get("incident") if ctx else None
    if not inc:
        logui.warn("security.escalate", session=session_id or "", msg="no incident")
        return
    with _state_context(ctx):
        inc = ctx.get("incident")
        if not inc:
            return
        inc["escalation_count"] = inc.get("escalation_count", 0) + 1
        inc["state"] = "escalated"
        security_protocol._set_state(ctx, "escalated")
        security_protocol._incident_emit(ctx)

def status():
    ctx = _read_ctx()
    alive = _pid_alive(ctx.get("pid"))
    armed = alive and ctx.get("armed")
    logui.print(f"  {'ARMED' if armed else 'disarmed'}" + (f"  state:{ctx.get('state', 'armed')}" if armed else ""))
    if alive:
        logui.print(f"  session:{ctx.get('session_id')}  pid:{ctx['pid']}")
        if ctx.get("incident"):
            logui.print(f"  incident:{ctx['incident']['id']}")
    # recent snapshots
    if os.path.exists(SNAPSHOT_DIR):
        snaps = sorted(os.listdir(SNAPSHOT_DIR), reverse=True)[:3]
        if snaps:
            logui.print("  recent snapshots:")
            for sn in snaps: logui.print(f"    {sn}")


# ── Workers ────────────────────────────────────────────────────────────────────

def _cam_worker(name, on_alert, ctx, stop_event):
    import camera as _camera
    import traceback
    def cb(cam, frame, score):
        if time.time() - ctx["started_at"] < ctx["warmup"]: return
        if score < ctx["move_threshold"]: return
        try:
            on_alert(ctx, "move", score, {"signal": "move", "camera": cam, "frame": frame})
        except Exception:
            traceback.print_exc()
    try: _camera.detect_move(name=name, on_move=cb, continuous=True, stop_event=stop_event)
    except Exception:
        traceback.print_exc()

def _noise_worker(device, on_alert, ctx, stop_event):
    import audio as _audio
    import traceback
    def cb(dev, score):
        if time.time() - ctx["started_at"] < ctx["warmup"]: return
        if score < ctx["noise_threshold"]: return
        try:
            on_alert(ctx, "noise", score, {"signal": "noise", "audio": dev, "frame": None})
        except Exception:
            traceback.print_exc()
    try: _audio.detect_noise(device=device, on_noise=cb, continuous=True, stop_event=stop_event)
    except Exception:
        traceback.print_exc()


# ── Session helpers ────────────────────────────────────────────────────────────

def _session_monitor(session_id, threads, session_dir):
    while any(t.is_alive() for t in threads):
        time.sleep(0.5)
    with _sessions_lock:
        _sessions.pop(session_id, None)

def _wait_for_stop(stop_events, threads):
    while any(t.is_alive() for t in threads):
        for ev in stop_events:
            ev.wait(timeout=0.5)

def _shutdown(stop_event, stop_events, threads):
    stop_event.set()
    for ev in stop_events:
        ev.set()
    for t in threads:
        t.join(timeout=3)


# ── listen() ───────────────────────────────────────────────────────────────────

def listen(
    cameras         = None,
    audios          = None,
    move_threshold  = None,
    noise_threshold = None,
    warmup          = None,
    on_alert        = None,
    session         = False,
):
    """
    Detects move and noise for given devices.
    Every signal above threshold calls on_alert(ctx, signal, value, meta).

    session=False (default): blocks until Ctrl-C, then cleans up.
    session=True:  returns a session id string; runs in background.
    session="<id>": uses the given id instead of auto-generated one.
    """
    _install_signal_handler()
    import camera as _camera
    import audio   as _audio

    config = settings()
    if move_threshold is None: move_threshold = config["sensors"]["move_threshold"]
    if noise_threshold is None: noise_threshold = config["sensors"]["noise_threshold"]
    if warmup is None: warmup = config["sensors"]["warmup"]
    if on_alert is None:    on_alert        = lambda ctx, signal, value, meta: logui.debug("security.listen", session=ctx.get("session_id"), signal=signal, value=value)

    cams = [cameras] if isinstance(cameras, str) else list(cameras) if cameras else []
    mics = [audios]  if isinstance(audios,  str) else list(audios)  if audios  else []
    if not cams: cams = list(_camera._cameras() or {})[:1]
    if not mics: mics = list(_audio._named_devices() or {})[:1]

    if session is True:
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    elif session:
        session_id = str(session)
    else:
        session_id = None

    session_dir = None
    if session_id:
        session_dir = os.path.join(SESSIONS_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)

    # Protocols receive settings and a context manager for shared state updates.
    ctx = {
        **config["triggers"], **config["detectors"], **config["score"],
        "counts": {"move": config["triggers"]["move_count"], "noise": config["triggers"]["noise_count"]},
        "alert_windows":   {},
        "escalation_timer": None,
        "session_id":      session_id,
        "session_dir":     session_dir,
        "cameras":         cams,
        "audios":          mics,
        "move_threshold":  float(move_threshold),
        "noise_threshold": float(noise_threshold),
        "warmup":          warmup,
        "armed":           bool(session_id),
        "state":           "armed" if session_id else "idle",
        "pid":             os.getpid(),
        "incident":        None,
        "started_at":      time.time(),
        "face":            True,
        "state_context":   _state_context,
    }

    logui("security.listen", session=session_id or "blocking", warmup=warmup)
    if cams:  logui.print(f"    move   cameras:{', '.join(cams)}  score_threshold:{move_threshold}")
    if mics:  logui.print(f"    noise  audio:{', '.join(str(d) for d in mics)}  score_threshold:{noise_threshold}")

    start_session(ctx)
    try:
        threads, stop_events, worker_info = _start_workers(cams, mics, on_alert, ctx)
        stdin_stop = _start_stdin_watcher(stop_events)
    except Exception:
        end_session(ctx)
        raise

    if session_id:
        mon = threading.Thread(target=_session_monitor, args=(session_id, threads, session_dir), daemon=True)
        mon.start()
        with _sessions_lock:
            _sessions[session_id] = {
                "threads": threads, "monitor": mon, "dir": session_dir,
                "ctx": ctx, "stop_events": stop_events, "workers": worker_info,
            }
        try:
            _wait_for_stop(stop_events, threads)
        except KeyboardInterrupt:
            pass
        finally:
            end_session(ctx)
            _shutdown(stdin_stop, stop_events, threads)
            with _sessions_lock:
                _sessions.pop(session_id, None)
        return session_id

    try:
        _wait_for_stop(stop_events, threads)
    except KeyboardInterrupt:
        pass
    finally:
        end_session(ctx)
        _shutdown(stdin_stop, stop_events, threads)


def _start_workers(cams, mics, on_alert, ctx):
    threads, stop_events, worker_info = [], [], []
    for c in cams:
        ev = threading.Event()
        stop_events.append(ev)
        threads.append(threading.Thread(target=_cam_worker, args=(c, on_alert, ctx, ev), daemon=True))
        worker_info.append(f"cam:{c}")
    for d in mics:
        ev = threading.Event()
        stop_events.append(ev)
        threads.append(threading.Thread(target=_noise_worker, args=(d, on_alert, ctx, ev), daemon=True))
        worker_info.append(f"mic:{d}")
    for t in threads:
        t.start()
    return threads, stop_events, worker_info


def _start_stdin_watcher(stop_events):
    stdin_stop = threading.Event()
    def _watch():
        try:
            while not stdin_stop.is_set():
                if sys.stdin.read(1) == '':
                    break
        except (OSError, ValueError):
            pass
        finally:
            for ev in stop_events:
                ev.set()
    threading.Thread(target=_watch, daemon=True).start()
    return stdin_stop


# ── Stop ───────────────────────────────────────────────────────────────────────

def stop(id):
    with _sessions_lock:
        entry = _sessions.pop(id, None)
    if not entry:
        logui.warn("security.stop", session=id, msg="not found")
        return
    workers = entry.get("workers", [])
    end_session(entry["ctx"])
    for ev in entry.get("stop_events", []):
        ev.set()
    for t in entry["threads"]:
        t.join(timeout=3)
    mon = entry.get("monitor")
    if mon and mon.is_alive():
        mon.join(timeout=2)
    logui.print(f"  stopped  {' '.join(workers)} monitor")


def stop_all():
    with _sessions_lock:
        ids = list(_sessions.keys())
    for sid in ids:
        stop(sid)


def list_sessions():
    with _sessions_lock:
        return {sid: {"dir": e["dir"], "alive": any(t.is_alive() for t in e["threads"]),
                       "armed": e.get("ctx", {}).get("armed", False)}
                for sid, e in _sessions.items()}


CLI_SCHEMA = {"ops": {
    "arm": {"params": ["--camera", "--audio", "--move-threshold", "--noise-threshold"]},
    "disarm": {},
    "status": {},
    "listen": {"params": ["--camera", "--audio", "--move-threshold", "--noise-threshold", "--session", "--protocol"]},
    "listen-stop": {"params": ["--session"]},
    "stop": {},
    "resolve": {"params": ["--session"]},
    "escalate": {"params": ["--session"]},
}}


def cli(argv):
    """Parse security command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="security", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    # listen (raw core)
    p.add_argument("--move-threshold",  type=float, dest="move_threshold")
    p.add_argument("--noise-threshold", type=float, dest="noise_threshold")
    p.add_argument("--session",         nargs="?", const=True, default=False)
    p.add_argument("--protocol",       default="",
                    help="protocol function name (default: none, uses listen default)")
    p.add_argument("--camera",          default=None, dest="cameras")
    p.add_argument("--audio",           default=None, dest="audios")
    p.add_argument("--notify",          action="store_true", default=False)
    args = p.parse_args(argv)

    a = args.action
    try:
        if   a == "arm":    arm(
                                cameras         = args.cameras  or None,
                                audios          = args.audios   or None,
                                move_threshold  = args.move_threshold,
                                noise_threshold = args.noise_threshold,
                                )
        elif a == "disarm": disarm()
        elif a == "status": status()
        elif a == "stop":   disarm()
        elif a == "resolve": resolve(args.session or None)
        elif a == "escalate": escalate(args.session or None)
        elif a == "listen":
            import importlib
            mod = importlib.import_module("security_protocol")
            on_alert = getattr(mod, args.protocol, None) if args.protocol else None
            listen(
                                cameras         = args.cameras  or None,
                                audios          = args.audios   or None,
                                move_threshold  = args.move_threshold,
                                noise_threshold = args.noise_threshold,
                                session         = args.session,
                                on_alert        = on_alert,
                                )
        elif a == "listen-stop":
            if args.session: stop(args.session)
            else:            stop_all()
    except Exception as e:
        print(f"  ERROR: {e}")
