import os
import glob
import shutil
import threading

from logui import logui
from detectors import detect_face, detect_recognize, detect_train, _require_contrib

KNOWN_FACES_DIR = ".out/known_faces"
UNKNOWN_DIR     = ".out/faces-unknown"
RECOGNIZER_FILE = ".out/face_recognizer.yml"
LABELS_FILE     = ".out/face_labels.json"


# ── enrollment ─────────────────────────────────────────────────────────

def add(person_name, frame):
    """Save a face frame as reference for person_name.
    After enrolling, run train() to update the recognizer."""
    cv2 = _require_contrib()
    os.makedirs(KNOWN_FACES_DIR, exist_ok=True)
    path = os.path.join(KNOWN_FACES_DIR, f"{person_name}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    logui("faces.enrolled", name=person_name, path=path)
    return path


def remove(person_name):
    """Remove an enrolled face. Run train() after to update the recognizer."""
    path = os.path.join(KNOWN_FACES_DIR, f"{person_name}.jpg")
    if not os.path.exists(path):
        for ext in (".jpeg", ".png"):
            alt = os.path.join(KNOWN_FACES_DIR, f"{person_name}{ext}")
            if os.path.exists(alt):
                path = alt
                break
        else:
            raise Exception(f"'{person_name}' not found in {KNOWN_FACES_DIR}")
    os.remove(path)
    logui("faces.removed", name=person_name)


def promote(guest_id, person_name):
    """Copy an unknown capture into known_faces, clean up guest files, retrain.

    guest_id    — e.g. unknown001 (as emitted by faces.unknown_saved)
    person_name — human name to assign
    """
    import security_protocol as _sp

    guest_id = (guest_id or "").strip()
    person_name = (person_name or "").strip()
    if not guest_id or not person_name:
        raise Exception("usage: faces promote <guest_id> <name>")
    if guest_id.lower() == "unknown":
        raise Exception("invalid guest id — use unknown001 style id, not 'unknown'")

    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in person_name)[:80]
    if not safe:
        raise Exception("invalid name")

    os.makedirs(UNKNOWN_DIR, exist_ok=True)
    os.makedirs(KNOWN_FACES_DIR, exist_ok=True)

    direct = os.path.join(UNKNOWN_DIR, f"{guest_id}.jpg")
    if os.path.isfile(direct):
        src = direct
    else:
        matches = sorted(
            glob.glob(os.path.join(UNKNOWN_DIR, f"{guest_id}_*.jpg")),
            key=os.path.getmtime,
            reverse=True,
        )
        if not matches:
            raise Exception(f"no unknown capture for {guest_id!r} under {UNKNOWN_DIR}")
        src = matches[0]

    dest = os.path.join(KNOWN_FACES_DIR, f"{safe}.jpg")
    shutil.copy2(src, dest)
    _sp.remove_guest_files(guest_id)
    logui("faces.promoted", guest=guest_id, name=safe, path=dest)

    # Retrain immediately via submit
    _sp._submit_train()
    return dest


def list_enrolled():
    """Print all enrolled faces and recognizer state."""
    if not os.path.exists(KNOWN_FACES_DIR):
        print("  none enrolled")
        return
    files = [f for f in os.listdir(KNOWN_FACES_DIR)
             if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    if not files:
        print("  none enrolled")
        return
    trained = os.path.exists(RECOGNIZER_FILE)
    for f in sorted(files):
        print(f"  {os.path.splitext(f)[0]}")
    status = "trained" if trained else "NOT trained — run train()"
    print(f"  recognizer: {status}")


# ── train (convenience wrapper) ────────────────────────────────────────

def train():
    """Build LBPH recognizer from known + unknown faces."""
    result = detect_train(
        known_dir=KNOWN_FACES_DIR,
        unknown_dir=UNKNOWN_DIR,
    )
    logui("faces.trained", count=result["count"],
          names=", ".join(result["names"]),
          model=result["model"])
    return result


# ── recognize (convenience) ────────────────────────────────────────────

def recognize(snapshot=None, frame=None, confidence=80, camera_name=None):
    """Detect and identify faces in an image. Returns list of results.

    For pipeline use, call security_protocol.detect() instead — it handles side effects.
    This is a thin wrapper for one-shot identification.
    """
    cv2 = _require_contrib()
    if snapshot and os.path.exists(snapshot):
        frame = cv2.imread(snapshot)
    if frame is None:
        raise Exception("provide snapshot path or frame")
    return detect_recognize(frame, confidence=confidence)
