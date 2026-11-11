import json
import heapq
import math
import os
import re
import sys
import tempfile
import threading
import time
import queue
import concurrent.futures
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2

import detectors
from logui import logui

SNAPSHOT_DIR = ".out/snapshots"
SESSIONS_DIR = ".out/security"

def _provider(ctx, key):
    """Resolve a declaratively-selected detectors.* provider from ctx."""
    return getattr(detectors, ctx[key])


# ═══════════════════════════════════════════════════════════════════════════════
# PROTOCOLS
# ═══════════════════════════════════════════════════════════════════════════════


# ── Default security protocol ──────────────────────────────────────────────
#
#   - enrich, save frame, record pending, submit
#   - yolo, gate, submit face stage
#   - face detect, identify, on_result callback
#


def default_security_protocol(ctx, signal, value, meta):
    """Away-mode security protocol with incident management."""
    _log_signal(signal, value, meta, ctx)

    # Incident gate
    with ctx["state_context"](ctx):
        incident = ctx.get("incident")
        if not incident:
            _tick_alert_window(ctx, signal)
            if not _should_trigger_alert(ctx, signal):
                return
            incident = _open_incident_if_needed(ctx)
        _record_signal(incident, signal)

    if meta.get("frame") is None:
        return

    is_dup = detectors.is_duplicate(meta.get("frame"), os.path.join(ctx.get("session_dir", ""), "detect.json"))
    if is_dup:
        return

    # Save snapshot (into ring buffer)
    meta["key"] = _ts_ms()
    meta["path"] = save_snapshot(meta["frame"], ctx["session_dir"], prefix="snap", basename=meta["key"], max_size=ctx["max_snapshot_size"])
    _enrich_meta(meta, ctx)

    with ctx["state_context"](ctx):
        incident = ctx.get("incident")
        if not ctx.get("armed") or not incident:
            return
        _incident_emit(ctx)
        _notify_alert(ctx, incident, signal, meta["path"])

    submit(lambda m: _process_objects(ctx, m,
        on_success=lambda _: _process_faces(ctx, m,
            on_result=lambda r: _handle_detection_result(ctx, r))), meta, queue=_cam(meta) or "default", ctx=ctx)

    _schedule_escalation(ctx)


def _process_objects(ctx, meta, *, on_success=None):
    t0 = time.monotonic()
    objects = _provider(ctx, "object_detector")(meta.get("frame"), min_conf=ctx["object_confidence"])
    meta["dt_objects"] = round((time.monotonic() - t0) * 1000)
    if objects:
        meta["objects"] = objects
        meta["basename"] = f"detect_{meta['key']}.jpg"
        _record_detect_json(ctx, meta, level="detected")
        annotated = annotate_frame(meta["frame"], objects)
        snap = save_snapshot(annotated, ctx.get("session_dir"), prefix="detect", basename=meta["key"])
        if _has_label(objects, min_conf=ctx["object_confidence"]):
            _emit_if_active(ctx)
        if on_success and _has_label(objects, _PERSON_LABEL, ctx["person_confidence"]):
            on_success(objects)
        else:
            _emit_detect(ctx, meta, objects=objects, snap=snap)


def _emit_detect(ctx, meta, *, objects=None, faces=None, snap=None):
    """Emit one security.detect event; omit empty objects/faces, skip if both empty."""
    kw = {}
    if objects:
        kw["objects"] = ",".join(f"{d['label']}:{d['conf']:.2f}" for d in objects)
    if faces:
        kw["faces"] = ",".join(faces)
    if not kw:
        return
    logui("security.detect", session_id=ctx.get("session_id"),
          cam=_cam(meta), snap=snap or meta.get("path"), **kw)


def _process_faces(ctx, meta, *, on_result=None):
    t0 = time.monotonic()
    threshold_key = "score_thr" if ctx["face_detector"] == "detect_face_yunet" else "min_conf"
    face_detections = [d for d in (_provider(ctx, "face_detector")(meta.get("frame"),
                       **{threshold_key: ctx["face_confidence"]}) or [])
                       if _good_face(d, ctx)]
    meta["dt_face_detect"] = round((time.monotonic() - t0) * 1000)
    if not face_detections:
        _emit_detect(ctx, meta, objects=meta.get("objects"))
        return
    meta["detections"] = [{k: v for k, v in d.items() if k != "lm"} for d in face_detections]
    _record_detect_json(ctx, meta, level="detected")
    t1 = time.monotonic()
    recognize = _provider(ctx, "face_recognizer")
    face_results = recognize(meta["frame"], face_detections,
        confidence=ctx["confidence_threshold"])
    meta["dt_face_recognize"] = round((time.monotonic() - t1) * 1000)
    if not face_results:
        _emit_detect(ctx, meta, objects=meta.get("objects"))
        return
    meta["faces"] = face_results
    # Assign guest ids, then make `label` the identity (name | guest id) + a `known` flag.
    guest_ids = _save_unknowns(meta["frame"], face_results)
    for i, r in enumerate(face_results):
        gid = guest_ids.get(i)
        r["known"] = gid is None
        if gid:
            r["label"] = gid
        r.pop("guest", None)
        r.pop("lm", None)
    _record_detect_json(ctx, meta, level="detected")
    annotated = annotate_frame(meta["frame"], face_results,
        color=lambda r: (0, 255, 0) if r["known"] else (0, 0, 255),
        label_fmt=lambda r: f"{r['label']} ({r['distance']:.0f})")
    snap = save_snapshot(annotated, ctx.get("session_dir"), prefix="detect", basename=meta["key"])
    meta["path"] = snap
    _log_faces(face_results, snap)
    dt_total = round((time.monotonic() - t0) * 1000)
    logui("detect.timing", camera=_cam(meta), objects=meta.get("dt_objects"), face_detect=meta.get("dt_face_detect"), face_recognize=meta.get("dt_face_recognize"), total=dt_total)
    faces = [f"{r['label']}:{r['distance']:.0f}" for r in face_results]
    _emit_detect(ctx, meta, objects=meta.get("objects"), faces=faces, snap=snap)
    if on_result:
        label, known = _best_face(face_results)
        on_result({"faces": face_results, "label": label, "known": known, "snap": snap, "guest_ids": guest_ids, "objects": meta.get("objects", []), "path": meta.get("path")})


# ── Detection callbacks ──────────────────────────────────────────────────

def _emit_if_active(ctx):
    """Re-emit incident state if an incident is still open."""
    with ctx["state_context"](ctx):
        if ctx.get("incident"):
            _incident_emit(ctx)


def _handle_detection_result(ctx, result):
    """Route a detection result: resolve if known, escalate if unknown objects found."""
    with ctx["state_context"](ctx):
        incident = ctx.get("incident")
        if not incident:
            return
        if result.get("known") and ctx["auto_resolve"]:
            _incident_resolve(ctx, reason=result.get("label") or "known_face")
            return
        if result.get("objects") and ctx["state"] == "alerted":
            incident["detected_objects"] = [(d.get("label"), d.get("conf")) for d in result["objects"]]
            _set_state(ctx, "detected")
        _incident_emit(ctx)


# -- Helpers --


def _log_signal(signal, value, meta, ctx):
    if signal == "move":
        logui("security.motion", session=ctx.get("session_id"), score=value, camera=meta.get("camera"))
    elif signal == "noise":
        logui("security.noise", session=ctx.get("session_id"), score=value, audio=meta.get("audio"))

def _get_incident(ctx):
    return ctx.get("incident")

def _is_armed(ctx):
    return ctx.get("armed")

def _should_trigger_alert(ctx, signal):
    return _is_armed(ctx) and threshold_reached(ctx, signal)

def _tick_alert_window(ctx, signal):
    alert_windows = ctx["alert_windows"]
    alert_windows.setdefault(signal, _AlertWindow(ctx["window"])).add()

def _open_incident_if_needed(ctx):
    """Open and register a new incident if none is active; return the active incident."""
    incident = ctx.get("incident")
    if incident:
        return incident

    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:-3]
    incident = {"id": f"{ctx['session_id']}_{ts}", "started_at": datetime.now().isoformat(), "state": "alerted", "escalation_count": 0, "signals": [], "detected_objects": None, "resolved_reason": None, "notified_alerted": False, "notified_escalated": False}
    ctx["incident"] = incident
    _set_state(ctx, "alerted")
    logui.print(f"  incident open  {incident['id']}")
    return incident


def _record_signal(incident, signal):
    if signal not in incident["signals"]:
        incident["signals"].append(signal)


def _notify_alert(ctx, incident, signal, snap_path):
    """Emit current incident state and send the first-alert notification if not yet sent."""
    if ctx.get("notify") and not incident.get("notified_alerted"):
        incident["notified_alerted"] = True
        _notify_incident_status(ctx, incident["id"], "alerted", signals=incident["signals"], attach_path=snap_path)

# ═══════════════════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════════════════


def _set_state(ctx, new_state):
    """Change state inside the supplied state context, which persists the transition."""
    prev = ctx.get("state")
    if prev == new_state:
        return
    ctx["state"] = new_state
    if ctx.get("incident"):
        ctx["incident"]["state"] = new_state
    logui("security.state", session=ctx.get("session_id"), prev=prev or "", state=new_state)


# ═══════════════════════════════════════════════════════════════════════════════
# THRESHOLD
# ═══════════════════════════════════════════════════════════════════════════════


def threshold_reached(ctx, signal, counts=None):
    counts = counts or ctx["counts"]
    if signal not in ctx["alert_windows"]:
        ctx["alert_windows"][signal] = _AlertWindow(ctx["window"])
    return ctx["alert_windows"][signal].count() >= counts.get(signal, counts.get("default", ctx["move_count"]))


class _AlertWindow:
    def __init__(self, seconds):
        self._s = seconds
        self._t = deque()

    def add(self):
        now = time.time()
        self._t.append(now)
        self._trim(now)

    def count(self):
        self._trim(time.time())
        return len(self._t)

    def _trim(self, now):
        while self._t and now - self._t[0] > self._s:
            self._t.popleft()


# ═══════════════════════════════════════════════════════════════════════════════
# INCIDENT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


def _incident_resolve(ctx, reason="resolved"):
    """Close current incident, cancel timers, return to armed inside the supplied state context."""
    incident = ctx.get("incident")
    if incident:
        incident["state"] = "resolved"
        incident["resolved_reason"] = reason
        _incident_emit(ctx)
        if ctx.get("notify"):
            _notify_incident_status(ctx, incident["id"], "resolved", reason=reason)
        logui.print(f"  incident resolved  {incident['id']}  reason={reason}")
    t = ctx.get("escalation_timer")
    if t:
        t.cancel()
    ctx["escalation_timer"] = None
    ctx["incident"] = None
    ctx["alert_windows"] = {}
    _set_state(ctx, "armed")


def _schedule_escalation(ctx):
    if not ctx.get("escalate"):
        return
    if ctx.get("state") == "escalated":
        return
    t = ctx.get("escalation_timer")
    if t:
        t.cancel()
    interval = ctx["escalation_interval"]
    t = threading.Timer(interval, _fire_escalation, args=(ctx,))
    t.daemon = True
    t.start()
    ctx["escalation_timer"] = t


def _fire_escalation(ctx):
    with ctx["state_context"](ctx):
        incident = ctx.get("incident")
        if not ctx.get("armed") or not incident or ctx.get("state") == "escalated":
            return
        incident["escalation_count"] = incident.get("escalation_count", 0) + 1
        incident["state"] = "escalated"
        _set_state(ctx, "escalated")
        logui("security.escalate", incident=incident)
        if ctx.get("notify") and not incident.get("notified_escalated"):
            incident["notified_escalated"] = True
            _notify_incident_status(ctx, incident["id"], "escalated", esc_count=incident["escalation_count"])



# ═══════════════════════════════════════════════════════════════════════════════
# INCIDENT SNAPS & EMIT
# ═══════════════════════════════════════════════════════════════════════════════


def _incident_emit(ctx):
    incident = ctx.get("incident")
    if not incident:
        return
    d = ctx.get("session_dir")
    if not d:
        return
    n = 0
    labels = ""
    best_conf = ""
    latest_file = ""
    try:
        data = json.loads(open(os.path.join(d, "detect.json")).read())
        detected = data.get("detected", {})
        n = len(detected)
        latest = max(detected.values(), key=lambda e: float(e.get("ts", 0)), default=None)
        if isinstance(latest, dict):
            face_labels = set()
            object_labels = set()
            best_c = 0.0
            for f in latest.get("faces") or []:
                lbl = f.get("label") or ""
                if lbl and lbl != "unknown":
                    face_labels.add(lbl)
                d_val = f.get("distance")
                if d_val is not None:
                    c = max(0.0, min(1.0, 1.0 - float(d_val) / 100.0))
                    best_c = max(best_c, c)
            for o in latest.get("objects") or []:
                lbl = o.get("label") or ""
                if lbl:
                    object_labels.add(lbl)
                c = float(o.get("conf", 0))
                best_c = max(best_c, c)
            labels = ",".join(sorted(face_labels | object_labels))
            if best_c > 0:
                best_conf = f"{best_c:.2f}"
            latest_file = latest.get("basename", "")
    except Exception:
        pass
    logui("security.incident", id=ctx.get("session_id"), state=incident["state"], n=n, labels=labels, conf=best_conf, file=latest_file)


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

_snapshot_queues = {}
_snapshot_queues_lock = threading.Lock()


def _evict_snapshot(workdir, max_size, new_path):
    if not max_size or max_size <= 0:
        return
    with _snapshot_queues_lock:
        q = _snapshot_queues.setdefault(workdir, queue.Queue(maxsize=max_size))
        if q.full():
            try:
                old = q.get_nowait()
                os.remove(old)
            except (FileNotFoundError, queue.Empty, OSError):
                pass
        q.put_nowait(new_path)


def save_snapshot(frame, workdir, prefix=None, basename=None, jpeg_quality=100, max_size=None):
    """Save frame as JPEG to workdir. Returns full path or None.

    When basename is provided, reuse that exact filename (overwrite).
    When max_size > 0, keep at most max_size files in workdir (LRU eviction).
    """
    # if not frame:
        # return None
    # if not workdir:
        # return None
    os.makedirs(workdir, exist_ok=True)
    name = f"{prefix}_{basename}.jpg" if prefix and basename else f"{basename}.jpg" if basename else None
    if not name:
        return None
    path = os.path.join(workdir, name)
    try:
        cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        logui.print(f"  snapshot saved  {path}")
    except Exception as e:
        logui.warn("detect.save_error", msg=str(e))
        return None

    _evict_snapshot(workdir, max_size, path)
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFY
# ═══════════════════════════════════════════════════════════════════════════════


def _alert_images(ctx, attach_path):
    out, seen = [], set()

    def add(p):
        if not p or not os.path.exists(p):
            return
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    add(attach_path)
    d = ctx.get("session_dir")
    if d and os.path.isdir(d):
        for p in sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg", ".png"))):
            add(p)
            if len(out) >= 2:
                break
    if not out:
        try:
            files = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
            if files:
                add(files[-1])
        except Exception:
            pass
    return out[:2]


def _notify_send(incident_id, phase, signals, reason, esc_count, paths):
    try:
        import notify

        t = incident_id[-10:] if incident_id and len(incident_id) > 10 else (incident_id or "?")
        if phase == "alerted":
            msg = f"alerted — {', '.join(signals or [])}"
            if paths:
                notify.send(msg, attach=paths[0], parse_mode=None)
                if len(paths) > 1:
                    notify.send("\u2060", attach=paths[1], parse_mode=None, bypass_cooldown=True)
            else:
                notify.send(msg, parse_mode=None)
        elif phase == "escalated":
            notify.send(f"escalated (#{esc_count or '?'})", parse_mode=None)
        elif phase == "resolved":
            notify.send(f"resolved — {reason or 'ok'}", parse_mode=None)
        else:
            return
        logui("security.notify", phase=phase, id=incident_id)
    except Exception as e:
        logui.warn("security.notify_error", msg=str(e))


def _notify_incident_status(ctx, incident_id, phase, *, signals=None, reason=None, esc_count=None, attach_path=None):
    if not ctx.get("notify") or not incident_id:
        return
    paths = _alert_images(ctx, attach_path) if phase == "alerted" else []
    threading.Thread(target=_notify_send, args=(incident_id, phase, signals, reason, esc_count, paths), daemon=True).start()


# ── Config ──────────────────────────────────────────────────────────────────

DETECT_META_EXCLUDE = {"path", "frame"}
DETECT_LEVELS = ["pending", "detected", "confirmed"]

_DEFAULT_GATE_LABELS = ["person"]
_PERSON_LABEL = "person"

# Directories (relative to workdir unless overridden)
KNOWN_FACES_DIR = ".out/known_faces"
UNKNOWN_DIR = ".out/faces-unknown"


# ── Scoring ─────────────────────────────────────────────────────────────────

_burst_dq = []


def _tick_burst(ctx):
    now = time.monotonic()
    _burst_dq.append(now)
    while _burst_dq and _burst_dq[0] < now - ctx["burst_window"]:
        _burst_dq.pop(0)


def _burst_factor(ctx):
    return math.exp(-len(_burst_dq) / ctx["burst_scale"])


def _score(meta, ctx):
    """Score a queued item by quality, burst pressure, and age."""
    quality = meta.get("quality", 0.0)
    if not quality:
        return 0.0
    burst = meta.get("burst", 1.0)
    age = time.time() - meta.get("ts", time.time())
    return quality * burst * math.exp(-age / ctx["age_decay"])


# ── Submit (priority queue via heapq + ThreadPoolExecutor) ────────────

_submit_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
_submit_queues = {}
_submit_draining = set()
_submit_lock = threading.Lock()
_submit_counter = 0


def submit(fn, meta=None, *, queue="default", ctx=None):
    global _submit_counter
    meta = meta or {}
    with _submit_lock:
        q = _submit_queues.setdefault(queue, [])
        heapq.heappush(q, (-_score(meta, ctx), _submit_counter, fn, meta))
        _submit_counter += 1
        if queue not in _submit_draining:
            _submit_draining.add(queue)
            threading.Thread(target=_submit_drain, args=(queue,), daemon=True).start()


def _submit_drain(name):
    while True:
        with _submit_lock:
            q = _submit_queues.get(name)
            if not q:
                _submit_draining.discard(name)
                return
            _, _, fn, meta = heapq.heappop(q)
        _submit_executor.submit(fn, meta)


# ── detect() helpers ──────────────────────────────────────────────────────


def _enrich_meta(meta, ctx):
    meta["basename"] = os.path.basename(meta.get("path", ""))
    meta["quality"] = detectors.detect_quality(meta["frame"])["quality"]
    meta["burst"] = round(_burst_factor(ctx), 2)
    meta["ts"] = time.time()
    meta["phash"] = detectors.detect_phash(meta["frame"]).hex()
    _tick_burst(ctx)


# ── detect() helpers ──────────────────────────────────────────────────────


def _passes_gate(objects, ctx):
    if not _has_label(objects, _PERSON_LABEL, ctx["object_confidence"]):
        return False
    return True


def _good_face(d, ctx):
    """Reject low-confidence / tiny face boxes before recognition + enrollment."""
    b = d.get("bbox") or [0, 0, 0, 0]
    return (d.get("conf", 0) >= ctx["face_confidence"]
            and min(b[2] - b[0], b[3] - b[1]) >= ctx["face_min_size"])


# ── Shared micro-helper ───────────────────────────────────────────────────


def _cam(meta):
    """Normalise camera identifier from either meta key."""
    return meta.get("camera") or meta.get("cam")


# ── Face side-effects ──────────────────────────────────────────────────────


def _log_faces(results, snap):
    """Emit logui events for face results (label is the identity, known the flag)."""
    if not results:
        logui("faces.no_face", snap=snap)
        return
    logui("faces.detected", count=len(results), snap=snap)
    for r in results:
        conf = round(r["distance"], 1)
        evt = "faces.known" if r["known"] else "faces.unknown"
        logui(evt, name=r["label"], conf=conf, snap=snap)


# ── Unknown guest management ──────────────────────────────────────────────


def _next_guest_id():
    """Return next guest ID by scanning UNKNOWN_DIR for highest unknownNNN."""
    if not os.path.exists(UNKNOWN_DIR):
        return 1
    max_id = 0
    for f in os.listdir(UNKNOWN_DIR):
        m = re.search(r"unknown(\d+)", f)
        if m:
            max_id = max(max_id, int(m.group(1)))
    return max_id + 1


# ── Guest re-ID by face embedding (sidecar gallery, not in detect.json) ────

_GUEST_EMB = lambda: os.path.join(UNKNOWN_DIR, "embeddings.json")
_GUEST_EMB_MAX = 5   # embeddings kept per guest (pose variety for re-ID)


def _load_guest_embeddings():
    try:
        return json.loads(open(_GUEST_EMB(), encoding="utf-8").read())
    except Exception:
        return {}


def _write_guest_embeddings(data):
    os.makedirs(UNKNOWN_DIR, exist_ok=True)
    tmp = _GUEST_EMB() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _GUEST_EMB())


def _forget_guest_embedding(guest):
    data = _load_guest_embeddings()
    if data.pop(guest, None) is not None:
        _write_guest_embeddings(data)


def _save_unknowns(frame, results):
    """Assign guest IDs for unknown faces. Returns {i: guest_name}.

    Identity is matched by SFace embedding against the guest gallery (kNN, no training),
    so the same face reuses its id even before the recognizer model exists — a new id is
    minted only when no existing guest is within the cosine threshold."""
    guest_ids = {}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    gallery = _load_guest_embeddings()   # {guest: [vec, vec, ...]}
    for i, r in enumerate(results):
        if not _is_unknown(r["label"]):
            continue
        emb = detectors.face_embedding(frame, r["bbox"], r.get("lm"))
        # Match against every stored embedding (best cosine over all guests' poses)
        flat = {f"{g}#{j}": v for g, vecs in gallery.items() for j, v in enumerate(vecs)}
        key, _ = detectors.match_embedding(emb, flat)
        guest = key.split("#")[0] if key else None
        os.makedirs(UNKNOWN_DIR, exist_ok=True)
        if guest is None:
            # First sighting — mint a guest id, save base image + embedding
            guest = f"unknown{_next_guest_id():03d}"
            path = os.path.join(UNKNOWN_DIR, f"{guest}.jpg")
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if emb is not None:
                gallery[guest] = [emb.flatten().tolist()]
                _write_guest_embeddings(gallery)
            _store_record(UNKNOWN_DIR, f"{guest}.jpg", guest=guest, ts=ts)
            logui("faces.unknown_saved", path=path, guest=guest)
        else:
            # Repeat sighting — save under that id, enrich its pose set (capped)
            path = os.path.join(UNKNOWN_DIR, f"{guest}_{ts}.jpg")
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if emb is not None and len(gallery.get(guest, [])) < _GUEST_EMB_MAX:
                gallery.setdefault(guest, []).append(emb.flatten().tolist())
                _write_guest_embeddings(gallery)
            logui("faces.unknown_seen", path=path, guest=guest)
        guest_ids[i] = guest
    return guest_ids


def remove_guest_files(guest_id):
    """Delete faces-unknown/<guest>.jpg and <guest>_*.jpg after promote."""
    import glob

    base = os.path.join(UNKNOWN_DIR, f"{guest_id}.jpg")
    for path in [base] + glob.glob(os.path.join(UNKNOWN_DIR, f"{guest_id}_*.jpg")):
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
        except OSError as e:
            logui.warn("faces.promote_cleanup", path=path, msg=str(e))


# ── Identity stores (reuse the detect.json struct, one per face dir) ───────


def _store_record(store_dir, basename, level="detected", **fields):
    """Record/promote a key in <store_dir>/detect.json, reusing _record_detect_json."""
    _record_detect_json({"session_dir": store_dir},
                        {"basename": basename, **fields}, level=level)


def _store_forget(store_dir, basename):
    """Drop a key from every level of <store_dir>/detect.json."""
    path = os.path.join(store_dir, "detect.json")
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return
    if not any(data.get(l, {}).pop(basename, None) is not None for l in DETECT_LEVELS):
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def promote_guest(guest, name):
    """Enroll a guest as a known face by appending its embedding(s) to the known gallery
    (harnessing captured frames). Image-independent; re-enrolling a name augments it."""
    import glob, shutil
    guest = (guest or "").strip()
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "").strip())[:80]
    if not guest or guest.lower() == "unknown" or not safe:
        raise ValueError("usage: promote_guest <unknownNNN> <name>")

    # Representative capture (optional now — fallback embedding source + record).
    base = os.path.join(UNKNOWN_DIR, f"{guest}.jpg")
    src = base if os.path.isfile(base) else max(
        glob.glob(os.path.join(UNKNOWN_DIR, f"{guest}_*.jpg")), key=os.path.getmtime, default="")

    # Resolve embedding(s): prefer the stored guest gallery, else compute from the capture.
    vecs = list(_load_guest_embeddings().get(guest) or [])
    if not vecs and src:
        img = cv2.imread(src)
        dets = detectors.detect_face_yunet(img) if img is not None else []
        emb = detectors.face_embedding(img, dets[0]["bbox"], dets[0].get("lm")) if dets else None
        if emb is not None:
            vecs = [emb.flatten().tolist()]
    if not vecs:
        raise FileNotFoundError(f"no capture or embedding for {guest!r}")

    # Append into the known gallery under the name (capped) — augments if the name exists.
    known = {n: [v.flatten().tolist() for v in vs]
             for n, vs in detectors._sface_load_gallery(detectors._SFACE_GALLERY).items()}
    known[safe] = (known.get(safe, []) + vecs)[-_GUEST_EMB_MAX:]
    tmp = detectors._SFACE_GALLERY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(known, f)
    os.replace(tmp, detectors._SFACE_GALLERY)

    # Best-effort image + store record (known-faces images are optional now).
    dest = os.path.join(KNOWN_FACES_DIR, f"{safe}.jpg")
    if src and os.path.isfile(src):
        os.makedirs(KNOWN_FACES_DIR, exist_ok=True)
        shutil.copy2(src, dest)
        _store_record(KNOWN_FACES_DIR, f"{safe}.jpg", level="confirmed",
                      name=safe, guest=guest, ts=_ts_ms())

    _store_forget(UNKNOWN_DIR, f"{guest}.jpg")
    _forget_guest_embedding(guest)
    remove_guest_files(guest)
    logui("security.promoted", guest=guest, name=safe, snap=dest)
    return dest


# ── Training (via submit) ─────────────────────────────────────────────────


def _submit_train(train_fn=None):
    """Submit a training job through the queue. Single-threaded, max 1 queued.
    train_fn — trainer paired with the active recognizer (default LBPH)."""
    train_fn = train_fn or detectors.detect_train_lbph

    def _run(m):
        try:
            result = train_fn(known_dir=KNOWN_FACES_DIR, unknown_dir=None)
            logui("faces.trained", count=result["count"], names=", ".join(result["names"]), model=result["model"])
        except Exception as e:
            logui.warn("faces.train_error", msg=str(e))

    submit(_run, queue="train")


# ── Persistence (_record_detect_json) ─────────────────────────────────────

_detect_json_lock = threading.Lock()

if sys.platform == "win32":
    import msvcrt

    def _lock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _detected_score(entry, ctx):
    def num(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    def conf(item):
        if "conf" in item:
            return clamp(num(item.get("conf")), 0.0, 1.0)
        if "distance" in item:
            return clamp(1.0 - num(item.get("distance"), 100.0) / 100.0, 0.0, 1.0)
        return 1.0

    def best(items, weights, default_weight):
        out = 0.0
        for item in items or []:
            label = str(item.get("label") or "").lower()
            weight = next((w for prefix, w in weights if label.startswith(prefix)), default_weight)
            out = max(out, weight * conf(item))
        return out

    importance = (
        best(entry.get("objects"), [("person", ctx["person_weight"])], ctx["object_weight"])
        + best(entry.get("detections"), [("face", ctx["face_weight"])], ctx["detection_weight"])
        + best(entry.get("faces"), [("unknown", ctx["unknown_weight"])], ctx["known_weight"])
    )
    quality = num(entry.get("quality"))
    quality_factor = clamp(quality / 1000.0 if quality > 1.0 else quality, ctx["quality_floor"], 1.0)
    burst = clamp(num(entry.get("burst"), 1.0), 0.0, 1.0)
    penalty = ctx["burst_penalty"]
    return round(importance * quality_factor * (1.0 - penalty + penalty * burst), 3)


def _record_detect_json(ctx, meta, level="pending"):
    basename = meta.get("basename") or os.path.basename(meta.get("path", ""))
    if not basename:
        return
    path = Path(ctx.get("session_dir")) / "detect.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    lock_path.touch(exist_ok=True)

    with _detect_json_lock, open(lock_path, "r+") as lf:
        _lock(lf)
        try:
            try:
                data = json.loads(path.read_text()) if path.exists() else {}
            except Exception:
                data = {}

            levels = [data.setdefault(l, {}) for l in DETECT_LEVELS]
            idx = DETECT_LEVELS.index(level)

            entry = {k: v for k, v in meta.items() if k not in DETECT_META_EXCLUDE and v}
            for l in levels[:idx]:
                entry.update(l.pop(basename, {}))
            if level == "detected":
                entry["score"] = _detected_score(entry, ctx)
            levels[idx][basename] = entry

            lines = ["{"]
            data_items = [(l, data[l]) for l in DETECT_LEVELS if data.get(l)]
            for si, (section, entries) in enumerate(data_items):
                lines.append(f'  "{section}": {{')
                for ei, (k, v) in enumerate(entries.items()):
                    comma = "," if ei < len(entries) - 1 else ""
                    lines.append(f'    "{k}": {_fmt_entry(v)}{comma}')
                lines.append("  }" + ("," if si < len(data_items) - 1 else ""))
            lines.append("}")

            fd, tmp = tempfile.mkstemp(prefix=".detect-", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w") as f:
                    f.write("\n".join(lines))
                    f.flush()
                    os.fsync(f.fileno())

                last_err = None
                for attempt in range(6):
                    try:
                        os.replace(tmp, path)
                        tmp = None
                        break
                    except PermissionError as e:
                        last_err = e
                        time.sleep(0.02 * (attempt + 1))
                else:
                    raise last_err
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        finally:
            _unlock(lf)


def _fmt_entry(v):
    scalars = {fk: fv for fk, fv in v.items() if not isinstance(fv, list)}
    arrays = {fk: fv for fk, fv in v.items() if isinstance(fv, list) and fv}
    body = json.dumps(scalars)[1:-1]
    if not arrays:
        return f"{{{body}}}"
    lines = [f"{{{body},"]
    for ai, (ak, av) in enumerate(arrays.items()):
        comma = "," if ai < len(arrays) - 1 else ""
        rows = [json.dumps(item, separators=(", ", ": ")) for item in av]
        lines.append(f'  "{ak}": [{rows[0]}')
        for r in rows[1:]:
            lines.append(f"           {r}")
        lines[-1] += f"]{comma}"
    lines.append("}")
    return "\n    ".join(lines)


# ── Annotate & save (generic) ────────────────────────────────────────────


def annotate_frame(frame, detections, color=(0, 255, 0), *, label_fmt=None):
    """Draw detections on a copy. detections: list of {bbox, label, ...}.
    bbox is (x1, y1, x2, y2). color can be a tuple or callable(d)->tuple."""
    img = frame.copy()
    for d in detections:
        x1, y1, x2, y2 = d["bbox"]
        label = label_fmt(d) if label_fmt else f"{d['label']} {d.get('conf', 1.0):.2f}"
        c = color(d) if callable(color) else color
        p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
        cv2.rectangle(img, p1, p2, c, 2)
        cv2.putText(img, label, (p1[0], max(0, p1[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
    return img


def _is_unknown(name):
    return name == "unknown" or name.startswith("unknown")


def _decode_phash(v):
    if isinstance(v, str):
        try:
            return bytes.fromhex(v)
        except ValueError:
            return None
    return v if isinstance(v, (bytes, bytearray)) else None


# ── Helpers ─────────────────────────────────────────────────────────────────


def _best_face(results):
    if not results:
        return "unknown", False
    return results[0]["label"], results[0]["known"]


def _has_label(objects, label=None, min_conf=0.0):
    if not objects:
        return False
    for d in objects:
        if d.get("conf", 0) >= min_conf:
            if label is None or d.get("label") == label:
                return True
    return False


def _ts_ms():
    return str(int(time.time() * 1000))
