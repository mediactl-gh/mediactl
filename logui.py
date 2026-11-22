import sys
import re
import threading
from datetime import datetime
from pathlib import Path

# Emoji-prefixed output crashes on Windows' cp1252 stdout; force tolerant utf-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Config ────────────────────────────────────────────────────────────────

JOURNAL = Path(".out/events.log")

# hint segment → emoji  (matches any segment of dotted hint, first wins)
EMOJI = {
    "alert":    "🚨",
    "critical": "💀",
    "error":    "❌",
    "warn":     "⚠️ ",
    "motion":   "〰️ ",
    "noise":    "🔊",
    "face":     "👤",
    "disarm":   "🔓",
    "arm":      "🔒",
    "security": "🔒",
    "home":     "🏠",
    "away":     "🚶",
    "presence": "📡",
    "lights":   "💡",
    "light":    "💡",
    "player":   "🎬",
    "playing":  "▶️ ",
    "device":   "🔌",
    "camera":   "📷",
    "snap":     "📷",
    "audio":    "🎙️ ",
    "speaker":  "🔊",
    "notify":   "🔔",
    "network":  "🌐",
    "torrent":  "⬇️ ",
    "library":  "📚",
    "llm":      "🤖",
    "web":      "🌍",
}

# severity → (ansi_color, file_marker)
SEVERITY = {
    0: ("\033[2m",         ""),     # dim
    1: ("\033[0m",         ""),     # normal
    2: ("\033[33m",        "!"),    # yellow  warning
    3: ("\033[1;31m",      "!!"),   # bold red  alert
    4: ("\033[1;31;5m",    "!!!"),  # bold red blink  critical
}
RESET = "\033[0m"

# disable color if not a tty or explicitly turned off
_color = sys.stdout.isatty()

# ── Journal ───────────────────────────────────────────────────────────────

_journal_lock = threading.Lock()

def append_line(path: Path, line: str):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def parse_line(line: str) -> dict | None:
    try:
        ts_end = line.index("]")
        ts     = line[1:ts_end]
        rest   = line[ts_end + 1:].strip()
        m = re.search(r'([\w-]+(?:\.[\w-]+)+)', rest)
        if not m:
            return None
        hint      = m.group(1)
        remainder = rest[m.end():].strip()
        severity = 1
        if remainder.startswith("!"):
            sev_tok, _, remainder = remainder.partition(" ")
            severity = len(sev_tok)
            remainder = remainder.strip()
        kwargs: dict = {}
        for m in re.finditer(r'(\w+)=(".*?"|.*?)(?=\s+\w+=|$)', remainder):
            kwargs[m.group(1)] = m.group(2).strip('"').replace("\\", "/")
        parts = hint.split(".", 1)
        return {"ts": ts, "hint": hint,
                "source": parts[0], "event": parts[1] if len(parts) > 1 else hint,
                "severity": severity, **kwargs}
    except Exception:
        return None


def _ensure_journal():
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)

def _write_journal(ts: str, hint: str, severity: int, kwargs: dict):
    _ensure_journal()
    marker = SEVERITY.get(severity, ("", ""))[1]
    # primary line
    kv = "  ".join(
        f"{k}={v}" for k, v in kwargs.items()
        if k not in ("snap", "severity") and v is not None
    )
    parts = [f"[{ts}]", hint]
    if marker:
        parts.append(marker)
    if kv:
        parts.append(" " + kv)
    line = " ".join(parts)

    # continuation lines
    continuations = []
    if "snap" in kwargs and kwargs["snap"]:
        continuations.append(f"  snap: {kwargs['snap']}")

    with _journal_lock:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            for c in continuations:
                f.write(c + "\n")

# ── Terminal ──────────────────────────────────────────────────────────────

def _emoji(hint: str) -> str:
    segments = hint.lower().replace(".", " ").split()
    for seg in segments:
        if seg in EMOJI:
            return EMOJI[seg] + " "
    return "  "

def _color_wrap(text: str, ansi: str) -> str:
    if not _color:
        return text
    return f"{ansi}{text}{RESET}"

def _format_terminal(ts: str, hint: str, severity: int, kwargs: dict) -> str:
    ansi = SEVERITY.get(severity, SEVERITY[1])[0]
    emoji = _emoji(hint)
    kv = "  ".join(
        f"\033[2m{k}\033[0m={v}" if _color else f"{k}={v}"
        for k, v in kwargs.items()
        if k not in ("severity",) and v is not None
    )
    label = _color_wrap(hint, ansi)
    ts_str = _color_wrap(f"[{ts}]", "\033[2m")
    parts = [ts_str, emoji + label]
    if kv:
        parts.append(" " + kv)
    return "  " + "  ".join(parts)

# ── Main callable ─────────────────────────────────────────────────────────

class _LogUI:
    """
    logui(hint, severity=1, **kwargs)

    hint     dotted namespace string: "security.motion", "lights.on", etc.
    severity 0=debug 1=info 2=warn 3=alert 4=critical
    kwargs   free-form metadata — key=value pairs
             special: snap=path  →  rendered as continuation line in journal
    """

    def __call__(self, hint: str, severity: int = 1, **kwargs):
        ts = datetime.now().strftime("%H:%M:%S")
        line = _format_terminal(ts, hint, severity, kwargs)
        print(line)
        try:
            _write_journal(ts, hint, severity, kwargs)
        except Exception:
            pass  # never crash the caller

    def print(self, *args, **kwargs):
        """Plain stdout print — no journal entry."""
        print(*args, **kwargs)

    def ok(self, hint: str, **kwargs):
        """Shorthand for severity=1 (normal/green confirmation)."""
        self(hint, severity=1, **kwargs)

    def warn(self, hint: str, **kwargs):
        """Shorthand for severity=2 (yellow warning)."""
        self(hint, severity=2, **kwargs)

    def err(self, hint: str, **kwargs):
        """Shorthand for severity=3 (red alert)."""
        self(hint, severity=3, **kwargs)

    def crit(self, hint: str, **kwargs):
        """Shorthand for severity=4 (critical)."""
        self(hint, severity=4, **kwargs)

    def debug(self, hint: str, **kwargs):
        """Shorthand for severity=0 (dim/verbose)."""
        self(hint, severity=0, **kwargs)


logui = _LogUI()
