"""
detect — image detection and quality checks

Usage:
  detect yolo <image>
  detect face <image>
  detect face-haar <image>
  detect face-yunet <image>
  detect face-ssd <image>
  detect recognize <image>
  detect recognize-lbph <image>
  detect quality <image>

Defaults match security: YOLO26, YuNet, and SFace.
Results are printed as JSON.

Help: help detect | detect --help
"""

import argparse
import os
import json
import re
import cv2
import threading
import numpy as np

from logui import logui


# ── models ──────────────────────────────────────────────────────────────

MODELS_DIR = os.path.join(".out", "models")

def _model_path(filename):
    return os.path.join(MODELS_DIR, filename)

def _ensure_model(url, filename):
    os.makedirs(MODELS_DIR, exist_ok=True)
    path = _model_path(filename)
    if not os.path.exists(path):
        import urllib.request
        urllib.request.urlretrieve(url, path)
    return path


def _inference_model_path(path):
    """Use an available ONNX export; otherwise retain the PyTorch model."""
    exported = os.path.splitext(path)[0] + ".onnx"
    if os.path.isfile(exported):
        try:
            import onnx
            import onnxruntime
        except ImportError:
            pass
        else:
            return exported
    return path


# ── yolo ────────────────────────────────────────────────────────────────

_yolo_lock        = threading.Lock()
_yolo_model       = None
_yolo_model_tried = False
_yolo_model_lock  = threading.Lock()

_YOLO_MODEL_PATH = os.path.join(MODELS_DIR, "yolo26n.pt")

def _yolo_load_model():
    global _yolo_model, _yolo_model_tried
    with _yolo_model_lock:
        if _yolo_model is not None:
            return _yolo_model
        if _yolo_model_tried:
            return None
        _yolo_model_tried = True
        try:
            os.makedirs(MODELS_DIR, exist_ok=True)
            if not os.path.exists(_YOLO_MODEL_PATH):
                from ultralytics import YOLO as _YOLO_dl
                _YOLO_dl(os.path.abspath(_YOLO_MODEL_PATH))
            from ultralytics import YOLO
            _yolo_model = YOLO(_inference_model_path(_YOLO_MODEL_PATH), task="detect")
            return _yolo_model
        except Exception as e:
            logui.warn("detect.yolo.model_unready",
                       model=_YOLO_MODEL_PATH, msg=str(e))
            return None


def _yolo_parse(results):
    out = []
    if not results:
        return out
    for r in results:
        names = getattr(r, "names", {}) or {}
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            continue
        xyxy = boxes.xyxy.cpu().numpy() if hasattr(boxes, "xyxy") else []
        confs = boxes.conf.cpu().numpy() if hasattr(boxes, "conf") else []
        clses = boxes.cls.cpu().numpy()  if hasattr(boxes, "cls")  else []
        for i in range(len(xyxy)):
            out.append({
                "label": str(names.get(int(clses[i]), int(clses[i]))),
                "conf":  round(float(confs[i]), 2),
                "bbox": [round(float(v), 2) for v in xyxy[i]],
                "source": "yolo",
            })
    return out


def detect_yolo(frame, min_conf=0.80):
    """Run YOLO object detection on frame.
    Returns list of {label, conf, bbox, source} or None on model/frame error."""
    with _yolo_lock:
        model = _yolo_load_model()
        if model is None or frame is None:
            return None
        try:
            results = model.predict(frame, verbose=False, conf=min_conf)
        except Exception as e:
            logui.warn("detect.error", msg=str(e))
            return None
        return _yolo_parse(results)


_YOLO_S_MODEL_PATH = os.path.join(MODELS_DIR, "yolo26s.pt")
_yolo_s_model = None
_yolo_s_tried = False

def _yolo_s_load_model():
    global _yolo_s_model, _yolo_s_tried
    with _yolo_model_lock:
        if _yolo_s_model is not None:
            return _yolo_s_model
        if _yolo_s_tried:
            return None
        _yolo_s_tried = True
        try:
            os.makedirs(MODELS_DIR, exist_ok=True)
            if not os.path.exists(_YOLO_S_MODEL_PATH):
                from ultralytics import YOLO as _YOLO_dl
                _YOLO_dl(os.path.abspath(_YOLO_S_MODEL_PATH))
            from ultralytics import YOLO
            _yolo_s_model = YOLO(_inference_model_path(_YOLO_S_MODEL_PATH), task="detect")
            return _yolo_s_model
        except Exception as e:
            logui.warn("detect.yolo.model_unready",
                       model=_YOLO_S_MODEL_PATH, msg=str(e))
            return None


def detect_yolo_s(frame, min_conf=0.80):
    """Run YOLO26s object detection on frame.
    Returns list of {label, conf, bbox, source} or None on model/frame error."""
    with _yolo_lock:
        model = _yolo_s_load_model()
        if model is None or frame is None:
            return None
        try:
            results = model.predict(frame, verbose=False, conf=min_conf)
        except Exception as e:
            logui.warn("detect.error", msg=str(e))
            return None
        return _yolo_parse(results)


# ── face — haar cascade ────────────────────────────────────────────────

def _cascade(cv2):
    return cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

def detect_face(frame):
    """Run Haar cascade face detector on frame.
    Returns list of {label, conf, bbox: [x1,y1,x2,y2], source}."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cascade = _cascade(cv2)
    found, num_detections = cascade.detectMultiScale2(
        gray, 1.1, 7, minSize=(60, 60))
    num_detections = np.asarray(num_detections).flatten()
    max_det = max(num_detections) if len(num_detections) else 1
    return [{"label": "face",
             "conf": round(float(num_detections[i] / max_det), 2),
             "bbox": [int(x), int(y), int(x + w), int(y + h)],
             "source": "haar"}
            for i, (x, y, w, h) in enumerate(found)]


# ── face — yunet ────────────────────────────────────────────────────────

_yunet_detector = None

def _yunet_load():
    global _yunet_detector
    if _yunet_detector is None:
        path = _ensure_model(
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
            "face_detection_yunet.onnx")
        _yunet_detector = cv2.FaceDetectorYN.create(path, "", (0, 0))
    return _yunet_detector

def detect_face_yunet(frame, score_thr=0.5):
    """Run YuNet face detector on frame.
    Returns list of {label, conf, bbox: [x1,y1,x2,y2], source}."""
    detector = _yunet_load()
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    detector.setScoreThreshold(score_thr)
    _, faces = detector.detect(frame)
    if faces is None:
        return []
    return [{"label": "face",
             "conf": round(float(f[14]), 2),
             "bbox": [int(f[0]), int(f[1]),
                      int(f[0] + f[2]), int(f[1] + f[3])],
             "lm": [round(float(v), 2) for v in f[:15]],  # raw YuNet row for alignCrop
             "source": "yunet"}
            for f in faces]


# ── face — ssd ──────────────────────────────────────────────────────────

_ssd_net = None

def _ssd_load():
    global _ssd_net
    if _ssd_net is None:
        proto = _ensure_model(
            "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt",
            "deploy.prototxt")
        caffe = _ensure_model(
            "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel",
            "res10_300x300_ssd_iter_140000.caffemodel")
        _ssd_net = cv2.dnn.readNetFromCaffe(proto, caffe)
    return _ssd_net

def detect_face_ssd(frame, min_conf=0.5):
    """Run SSD ResNet-10 face detector on frame.
    Returns list of {label, conf, bbox: [x1,y1,x2,y2], source}."""
    net = _ssd_load()
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(frame, 1.0, (300, 300),
                                 (104.0, 177.0, 123.0), False, False)
    net.setInput(blob)
    detections = net.forward()
    out = []
    for i in range(detections.shape[2]):
        conf = float(detections[0, 0, i, 2])
        if conf < min_conf:
            continue
        x1 = int(detections[0, 0, i, 3] * w)
        y1 = int(detections[0, 0, i, 4] * h)
        x2 = int(detections[0, 0, i, 5] * w)
        y2 = int(detections[0, 0, i, 6] * h)
        out.append({"label": "face",
                    "conf": round(conf, 2),
                    "bbox": [x1, y1, x2, y2],
                    "source": "ssd"})
    return out


# ── recognize — LBPH ───────────────────────────────────────────────────

def _require_contrib():
    try:
        _ = cv2.face.LBPHFaceRecognizer_create
        return cv2
    except AttributeError:
        raise Exception("faces requires: pip install opencv-contrib-python")

def _load_labels(labels_file):
    if not os.path.exists(labels_file):
        return {}
    return json.loads(open(labels_file).read())

def _load_recognizer(recognizer_file, labels_file):
    """Load trained LBPH model + labels. Returns (recognizer, names) or (None, {})."""
    if not os.path.exists(recognizer_file):
        return None, {}
    try:
        recognizer = cv2.face.LBPHFaceRecognizer_create()
        recognizer.read(recognizer_file)
        return recognizer, _load_labels(labels_file)
    except Exception:
        logui.debug("faces.recognize_model_unread",
                     msg="train may be writing; detection-only this frame")
        return None, {}

def detect_recognize_lbph(frame, detections=None, confidence=80,
                     recognizer_file=None, labels_file=None):
    """Identify faces in frame via LBPH recognizer.
    detections — list of {label, bbox} from a face detector.
                 If None, runs detect_face(frame) on the whole frame.
    confidence — LBPH threshold (lower = stricter).
    recognizer_file / labels_file — model paths (default: .out/).
    Returns list of {name, bbox, distance, metric}."""
    _require_contrib()
    if detections is None:
        detections = detect_face(frame)
    rec_file = recognizer_file or ".out/face_recognizer.yml"
    lbl_file = labels_file or ".out/face_labels.json"
    recognizer, names = _load_recognizer(rec_file, lbl_file)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    results = []
    for det in detections:
        if det.get("label") != "face":
            continue
        x1, y1, x2, y2 = det["bbox"]
        if recognizer is not None:
            roi = cv2.resize(gray[y1:y2, x1:x2], (200, 200))
            label_id, distance = recognizer.predict(roi)
            name = (names.get(str(label_id), "unknown")
                    if distance < confidence else "unknown")
        else:
            distance, name = 0.0, "unknown"
        results.append({"label": name,
                        "bbox": [x1, y1, x2, y2],
                        "distance": round(float(distance), 2),
                        "source": "lbph"})
    return results


# ── train — LBPH ───────────────────────────────────────────────────────

def detect_train_lbph(known_dir, unknown_dir=None, out_dir=".out"):
    """Build LBPH recognizer from face images on disk.

    known_dir   — directory of <name>.jpg enrollment images
    unknown_dir — optional directory of unknownNNN.jpg guest images
    out_dir     — where to write face_recognizer.yml + face_labels.json

    Returns {count, names, model, labels} on success.
    Raises on no faces.  Pure: no callbacks, no logging beyond the return value.
    """
    _require_contrib()
    cascade = _cascade(cv2)
    faces, labels, names = [], [], {}
    label_id = 0

    # known faces
    if known_dir and os.path.exists(known_dir):
        for fname in sorted(os.listdir(known_dir)):
            if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            person = os.path.splitext(fname)[0]
            img = cv2.imread(os.path.join(known_dir, fname))
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            found = cascade.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
            if len(found):
                x, y, w, h = found[0]
                roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
            else:
                roi = cv2.resize(gray, (200, 200))
            names[str(label_id)] = person
            faces.append(roi)
            labels.append(label_id)
            label_id += 1

    # unknown guest base images (unknownNNN.jpg only)
    if unknown_dir and os.path.exists(unknown_dir):
        for fname in sorted(os.listdir(unknown_dir)):
            if not re.match(r'^unknown\d+\.jpg$', fname):
                continue
            person = os.path.splitext(fname)[0]
            img = cv2.imread(os.path.join(unknown_dir, fname))
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            found = cascade.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
            if len(found):
                x, y, w, h = found[0]
                roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
            else:
                roi = cv2.resize(gray, (200, 200))
            names[str(label_id)] = person
            faces.append(roi)
            labels.append(label_id)
            label_id += 1

    if not faces:
        raise Exception("no faces to train on")

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(faces, np.array(labels))

    os.makedirs(out_dir, exist_ok=True)
    model_path  = os.path.join(out_dir, "face_recognizer.yml")
    labels_path = os.path.join(out_dir, "face_labels.json")
    tmp_yml = model_path + ".tmp"
    tmp_lbl = labels_path + ".tmp"
    recognizer.save(tmp_yml)
    with open(tmp_lbl, "w", encoding="utf-8") as f:
        f.write(json.dumps(names))
    os.replace(tmp_yml, model_path)
    os.replace(tmp_lbl, labels_path)

    return {"count": label_id,
            "names": list(names.values()),
            "model": model_path,
            "labels": labels_path}


# ── recognize — sface (embedding, ArcFace-family) ──────────────────────

_SFACE_MODEL_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
                    "face_recognition_sface/face_recognition_sface_2021dec.onnx")
_SFACE_GALLERY   = ".out/face_embeddings.json"
_SFACE_COSINE_THR = 0.30      # cosine acceptance (lowered to merge poses; zoo default 0.363)
_sface_recognizer = None

def _sface_load():
    global _sface_recognizer
    if _sface_recognizer is None:
        path = _ensure_model(_SFACE_MODEL_URL, "face_recognition_sface.onnx")
        _sface_recognizer = cv2.FaceRecognizerSF.create(path, "")
    return _sface_recognizer

def _sface_embed(recognizer, frame, bbox, lm=None):
    """Embed the face. With YuNet landmarks (lm = raw 15-value row) use alignCrop for a
    pose-normalised embedding; else fall back to a plain crop+resize. Returns (1,128)."""
    if lm is not None:
        try:
            aligned = recognizer.alignCrop(frame, np.array(lm, dtype=np.float32).reshape(1, -1))
            return recognizer.feature(aligned)
        except Exception:
            pass
    x1, y1, x2, y2 = [int(v) for v in bbox]
    x1, y1 = max(0, x1), max(0, y1)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    return recognizer.feature(cv2.resize(crop, (112, 112)))

def _sface_load_gallery(gallery_file):
    """Return {name: [ (1,128) vec, ... ]}. Accepts legacy single flat-vec entries."""
    if not os.path.exists(gallery_file):
        return {}
    try:
        data = json.loads(open(gallery_file).read())
        out = {}
        for name, val in data.items():
            vecs = val if (val and isinstance(val[0], list)) else [val]  # legacy single → wrap
            out[name] = [np.array(v, dtype=np.float32).reshape(1, -1) for v in vecs]
        return out
    except Exception:
        return {}

def face_embedding(frame, bbox, lm=None):
    """SFace embedding (1,128 ndarray) of the face at bbox, aligned via lm if given."""
    return _sface_embed(_sface_load(), frame, bbox, lm)


def match_embedding(emb, gallery, threshold=None):
    """Best (label, cosine) for emb over gallery {label: vec(list|array)}.
    label is None when the best cosine is below threshold (default _SFACE_COSINE_THR)."""
    if emb is None or not gallery:
        return None, 0.0
    rec = _sface_load()
    thr = _SFACE_COSINE_THR if threshold is None else threshold
    best, best_c = None, -1.0
    for label, vec in gallery.items():
        v = vec if hasattr(vec, "shape") else np.array(vec, dtype=np.float32).reshape(1, -1)
        c = float(rec.match(emb, v, cv2.FaceRecognizerSF_FR_COSINE))
        if c > best_c:
            best, best_c = label, c
    return (best if best_c >= thr else None), best_c


def detect_recognize_sface(frame, detections=None, confidence=80,
                           gallery_file=None):
    """Identify faces via SFace embeddings. Contract-compatible with
    detect_recognize_lbph. `confidence` is accepted for interface parity but
    SFace decides via an internal cosine threshold; distance = (1-cosine)*100."""
    recognizer = _sface_load()
    if detections is None:
        detections = detect_face_yunet(frame)
    gallery = _sface_load_gallery(gallery_file or _SFACE_GALLERY)
    results = []
    for det in detections:
        if det.get("label") != "face":
            continue
        x1, y1, x2, y2 = det["bbox"]
        feat = _sface_embed(recognizer, frame, det["bbox"], det.get("lm"))
        name, distance = "unknown", 100.0
        if feat is not None and gallery:
            best_name, best_cos = None, -1.0
            for gname, gvecs in gallery.items():
                for gvec in gvecs:
                    cos = float(recognizer.match(
                        feat, gvec, cv2.FaceRecognizerSF_FR_COSINE))
                    if cos > best_cos:
                        best_name, best_cos = gname, cos
            if best_cos >= _SFACE_COSINE_THR:
                name = best_name
                distance = round((1.0 - best_cos) * 100, 2)
        results.append({"label": name,
                        "bbox": [x1, y1, x2, y2],
                        "distance": distance,
                        "lm": det.get("lm"),
                        "source": "sface"})
    return results


# ── train — sface (embedding gallery) ──────────────────────────────────

def detect_train_sface(known_dir, unknown_dir=None, out_dir=".out"):
    """Build an SFace embedding gallery from face images on disk. Mirrors
    detect_train_lbph's directory scan but persists embeddings to
    face_embeddings.json. Returns {count, names, model, labels}."""
    recognizer = _sface_load()
    gallery = {}

    def _add(directory, accept):
        for fname in sorted(os.listdir(directory)):
            if not accept(fname):
                continue
            person = os.path.splitext(fname)[0]
            img = cv2.imread(os.path.join(directory, fname))
            if img is None:
                continue
            dets = detect_face_yunet(img)
            if dets:
                feat = _sface_embed(recognizer, img, dets[0]["bbox"], dets[0].get("lm"))
            else:
                feat = recognizer.feature(cv2.resize(img, (112, 112)))
            if feat is not None:
                gallery.setdefault(person, []).append(feat.flatten().tolist())

    if known_dir and os.path.exists(known_dir):
        _add(known_dir, lambda f: f.lower().endswith((".jpg", ".jpeg", ".png")))
    if unknown_dir and os.path.exists(unknown_dir):
        _add(unknown_dir, lambda f: bool(re.match(r'^unknown\d+\.jpg$', f)))

    if not gallery:
        raise Exception("no faces to train on")

    os.makedirs(out_dir, exist_ok=True)
    model_path = os.path.join(out_dir, "face_embeddings.json")
    tmp = model_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(gallery))
    os.replace(tmp, model_path)

    return {"count": len(gallery),
            "names": list(gallery.keys()),
            "model": model_path,
            "labels": model_path}


# ── recognizer provider pairing + back-compat aliases ──────────────────

detect_recognize_lbph.train  = detect_train_lbph
detect_recognize_sface.train = detect_train_sface

# Legacy callers (faces.py, mediactl CLI) and the manual/CLI default = LBPH.
detect_recognize = detect_recognize_lbph
detect_train     = detect_train_lbph


# ── quality ─────────────────────────────────────────────────────────────

def detect_quality(frame):
    """Laplacian sharpness score for frame.
    Returns {quality: float}."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return {"quality": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)}


# ── dedup ───────────────────────────────────────────────────────────────

_HASH_SIZE = 8
_HAMMING_THRESH = 8

def _phash(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    resized = cv2.resize(gray, (_HASH_SIZE * 4, _HASH_SIZE * 4),
                         interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(resized))[:_HASH_SIZE, :_HASH_SIZE]
    med = np.median(dct)
    return np.packbits(dct > med).tobytes()

def _hamming(a, b):
    return sum(bin(x ^ y).count('1') for x, y in zip(a, b))

def is_duplicate(frame, detect_json_path):
    if frame is None:
        return False
    h = _phash(frame)
    try:
        data = json.loads(open(detect_json_path).read())
        for level in ("pending", "detected", "confirmed"):
            for entry in data.get(level, {}).values():
                phash = entry.get("phash")
                if phash and _hamming(bytes.fromhex(phash), h) < _HAMMING_THRESH:
                    return True
    except Exception:
        pass
    return False


# ── similarity ──────────────────────────────────────────────────────────

def detect_phash(frame):
    """Perceptual hash of a frame. Returns bytes for use with phash_distance."""
    return _phash(frame)

def refresh():
    """Preload detection models: YOLO + face detection + face recognition (best-effort)."""
    try:
        _yolo_load_model()
    except Exception:
        pass
    try:
        _yunet_load()
    except Exception:
        pass
    try:
        _sface_load()
    except Exception:
        pass


def phash_distance(hash_a, hash_b):
    """Hamming distance between two perceptual hashes (0 = identical)."""
    return _hamming(hash_a, hash_b)

def detect_similarity(frame_a, frame_b):
    """Perceptual similarity between two frames.
    Returns {distance: int, similar: bool}."""
    dist = _hamming(_phash(frame_a), _phash(frame_b))
    return {"distance": dist, "similar": dist < _HAMMING_THRESH}




CLI_SCHEMA = {"ops": {
    "yolo": {"args": "image"},
    "face": {"args": "image"},
    "face-haar": {"args": "image"},
    "face-yunet": {"args": "image"},
    "face-ssd": {"args": "image"},
    "recognize": {"args": "image"},
    "recognize-lbph": {"args": "image"},
    "quality": {"args": "image"},
}}


def cli(argv):
    """Parse detect command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="detect", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    args = p.parse_args(argv)

    a = args.action
    try:
        if not args.args:
            print("  usage: detect <yolo|face|face-yunet|face-ssd|face-haar|recognize|recognize-lbph|quality> <image>")
            return
        path = args.args[0]
        frame = cv2.imread(path)
        if frame is None:
            print(f"  ERROR: can't read: {path}"); return

        if   a == "quality":    result = detect_quality(frame)
        elif a == "yolo":       result = detect_yolo(frame)
        elif a == "face":       result = detect_face_yunet(frame)
        elif a == "face-haar":  result = detect_face(frame)
        elif a == "recognize":  result = detect_recognize_sface(frame)
        elif a == "recognize-lbph": result = detect_recognize_lbph(frame)
        elif a == "face-yunet": result = detect_face_yunet(frame)
        elif a == "face-ssd":   result = detect_face_ssd(frame)

        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        print(f"  ERROR: {e}")
