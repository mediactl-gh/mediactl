"""
audio — microphones, speech recognition, speech output, and wake words

Usage:
  audio list
  audio record [--device NAME|INDEX] [--threshold N] [--max-duration SECONDS]
  audio detect-noise [--device NAME|INDEX] [--continuous] [--read-fps N] [--emit-fps N]
  audio detect-noise-off [--device NAME|INDEX]
  audio listen [--device NAME|INDEX] [--stt-engine faster-whisper] [--model NAME]
               [--max-duration SECONDS] [--continuous] [--wake WORD]
  audio listen-off [--device NAME|INDEX]
  audio speak <text...> [--service NAME] [--voice NAME] [--out-device NAME|INDEX]
  audio wake [--word WORD] [--device NAME|INDEX]

listen uses speech detection; select its microphone with --device.

Help: help audio | audio --help
"""

import argparse
import os
import wave
import math
import time
import json
import threading
import collections
import queue
from datetime import datetime
from pathlib import Path
from contextlib import closing
from logui import logui

_OUT = Path(__file__).parent / ".out"
AUDIO_DIR = _OUT / "audio"
CONFIG_FILE = _OUT / "audio_config.json"
MAX_CLIPS     = 50
DEFAULT_STT_MODEL = "base"
DEFAULT_STT_THRESHOLD = 500
DEFAULT_TTS_SERVICE = "edge"
DEFAULT_TTS_VOICE = "en-US-AriaNeural"
DEFAULT_WAKE_WORD = "hey computer"
DEFAULT_WAKE_MODEL = ""
RATE              = 16000
CHUNK             = 512   # ~32ms per chunk at 16kHz


# ── Config resolution ──────────────────────────────────────────────────────────

def sources() -> dict:
    devices = {}
    try:
        devices_file = _OUT / "devices.json"
        if devices_file.exists():
            contributed = json.loads(devices_file.read_text(encoding="utf-8"))
            for kind in ("mic", "speaker"):
                for device in contributed.get(kind, []):
                    name = device.get("name", "")
                    source = device.get("identifier", "")
                    if name and source is not None and source != "" and name not in devices:
                        devices[name] = source
    except Exception:
        pass
    return devices


def _file_cfg():
    try:
        path = Path(CONFIG_FILE)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "stt_model": DEFAULT_STT_MODEL,
                "stt_threshold": DEFAULT_STT_THRESHOLD,
                "tts_service": DEFAULT_TTS_SERVICE,
                "tts_voice": DEFAULT_TTS_VOICE,
                "wake_word": DEFAULT_WAKE_WORD,
                "wake_model": DEFAULT_WAKE_MODEL,
            }, indent=2), encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _resolve(key, param, default=None):
    if param is not None:
        return param
    file_val = _file_cfg().get(key)
    return file_val if file_val is not None else default

def _resolve_int(key, param, default):
    value = _resolve(key, param, default)
    return int(value) if value is not None else default

def _stt_model(param=None):
    return _resolve("stt_model", param, DEFAULT_STT_MODEL)

def _stt_thresh(param=None):
    return _resolve_int("stt_threshold", param, DEFAULT_STT_THRESHOLD)

def _tts_service(param=None):
    return (_resolve("tts_service", param, DEFAULT_TTS_SERVICE) or DEFAULT_TTS_SERVICE).lower()

def _tts_voice(param=None):
    return _resolve("tts_voice", param, DEFAULT_TTS_VOICE)

def _wake_word(param=None):
    v = _resolve("wake_word", param, DEFAULT_WAKE_WORD)
    return (v or "").lower().strip()

def _wake_model_path(param=None):
    return _resolve("wake_model", param, DEFAULT_WAKE_MODEL)


# ── Device registry ────────────────────────────────────────────────────────────

def _named_devices():
    return {d["name"]: d.get("identifier") for d in _device.devices_read("mic") if d.get("name")}

def _resolve_device(param=None, strict=False):
    """
    Resolve device param to (sd_index_or_None, net_url_or_None).
    param can be: name (str), index (int or str digit), None.
    Falls back to saved device config, then system default.
    Returns (idx: int|None, url: str|None) — exactly one is set, or both None for system default.
    """
    # explicit param
    val = param
    if val is None:
        val = _resolve("device", None)

    if val is None:
        return None, None

    # check named devices first
    named = _named_devices()
    key   = str(val)
    if key in named:
        source = named[key]
        if source is None or source == "":
            raise ValueError(f"audio device '{val}' has no identifier")
        try:
            return int(source), None
        except (ValueError, TypeError):
            return None, source   # URL

    # raw index
    try:
        return int(val), None
    except (ValueError, TypeError):
        pass

    if strict or param is not None:
        raise ValueError(f"unknown audio device '{val}'; use audio list")
    # unknown name — warn and use default
    logui.warn("audio.device", msg=f"unknown '{val}' — using default")
    return None, None

def _device_label(idx, url):
    if url:  return f"net:{url}"
    if idx is not None: return f"local:[{idx}]"
    return "local:[default]"

def _mic_rate_from_devices_json(idx):
    if idx is None or not (_OUT / "devices.json").exists():
        return None
    try:
        data = json.loads((_OUT / "devices.json").read_text(encoding="utf-8"))
        for d in data.get("mic", []):
            ident = d.get("identifier")
            try:
                ident = int(ident)
            except Exception:
                pass
            if ident == idx:
                rate = int(float(d.get("samplerate") or 0))
                return rate if rate > 0 else None
    except Exception:
        return None
    return None

def _mic_rate_from_runtime(sd, idx):
    try:
        dev = sd.query_devices(idx, "input") if idx is not None else sd.query_devices(kind="input")
        rate = int(float(dev.get("default_samplerate") or 0))
        return rate if rate > 0 else None
    except Exception:
        return None

def _open_input_stream_samplerate_first(sd, idx, callback, label=None, blocksize=CHUNK):
    label = label or _device_label(idx, None)
    rates = []
    from_registry = _mic_rate_from_devices_json(idx)
    if from_registry:
        rates.append(from_registry)
    if RATE not in rates:
        rates.append(RATE)
    from_runtime = _mic_rate_from_runtime(sd, idx)
    if from_runtime and from_runtime not in rates:
        rates.append(from_runtime)

    last_err = None
    for i, rate in enumerate(rates):
        try:
            return sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                                  blocksize=blocksize, device=idx, callback=callback)
        except Exception as e:
            last_err = e
            if i + 1 < len(rates):
                logui.warn("audio.device", msg=f"sample rate {rate} unsupported on {label}; retry {rates[i+1]}")
    raise last_err

def _out_device_index(param=None):
    v = _resolve("out_device", param)
    if v is None: return None
    try:
        v = _device.devices_find("speaker", v)["identifier"]
    except KeyError:
        pass
    try:
        index = int(v)
        if index >= 0: return index
    except (TypeError, ValueError):
        pass
    raise ValueError(f"unsupported speaker '{v}'; a local output index is required")


# ── Dependency helpers ─────────────────────────────────────────────────────────

def _require_sd():
    try:
        import sounddevice as sd
        return sd
    except ImportError:
        raise Exception("audio requires: pip install sounddevice scipy")

def _require_whisper():
    import sys

    from types import SimpleNamespace

    import_thread = threading.get_ident()

    def find_spec(fullname, path=None, target=None):
        if fullname == "torch" and threading.get_ident() == import_thread:
            raise ModuleNotFoundError("torch is optional for STT", name="torch")

    guard = SimpleNamespace(find_spec=find_spec) if sys.platform == "win32" else None
    if guard is not None:
        sys.meta_path.insert(0, guard)
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except ImportError:
        raise Exception("stt requires: pip install faster-whisper")
    finally:
        if guard is not None:
            sys.meta_path.remove(guard)


# ── File helpers ───────────────────────────────────────────────────────────────

def _trim_clips():
    if not os.path.exists(AUDIO_DIR): return
    files = sorted(
        [os.path.join(AUDIO_DIR, f) for f in os.listdir(AUDIO_DIR) if f.endswith(".wav")],
        key=os.path.getmtime
    )
    for f in files[:-MAX_CLIPS]:
        os.remove(f)

def _save_wav(data, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2)
        wf.setframerate(RATE); wf.writeframes(data.tobytes())


# ── Audio helpers ──────────────────────────────────────────────────────────────

def _rms(chunk):
    import numpy as np
    return math.sqrt(np.mean(chunk.astype(float) ** 2))

_WHISPER_CACHE = {}
_WHISPER_LOCK = threading.Lock()
_VOSK_CACHE = {}

def _load_whisper(model_size=None):
    size = _stt_model(model_size)
    with _WHISPER_LOCK:
        if size not in _WHISPER_CACHE:
            WhisperModel = _require_whisper()
            logui("audio.stt", msg=f"loading model '{size}'")
            _WHISPER_CACHE[size] = WhisperModel(size, device="cpu", compute_type="int8")
        return _WHISPER_CACHE[size]


def _whisper_engine(model_size=None):
    model = _load_whisper(model_size)

    def transcribe(samples):
        with _WHISPER_LOCK:
            decoded, _ = model.transcribe(samples, beam_size=1, condition_on_previous_text=False)
            return " ".join(segment.text.strip() for segment in decoded).strip()

    return transcribe


# Factories receive model_size and return a callable: mono float32 at 16 kHz -> str.
# Register another factory here to expose it through --stt-engine automatically.
STT_ENGINES = {"faster-whisper": _whisper_engine}


# ── Core dynamic capture ───────────────────────────────────────────────────────

def _warmup_audio(chunks, score, stop, minimum=15, maximum=94, tolerance=50):
    recent = collections.deque(maxlen=10)
    count = 0
    for chunk, gap in chunks:
        if chunk is None:
            continue
        if gap:
            raise RuntimeError("microphone dropped audio during warmup")
        recent.append(score(chunk))
        count += 1
        if count >= minimum and (max(recent) - min(recent) < tolerance or count >= maximum):
            return sorted(recent)[len(recent) // 2]
    if not stop.is_set():
        raise RuntimeError("audio source ended during warmup")


def _capture_dynamic(threshold, max_duration=30, voice_confirm=4,
                     silence_confirm=35, device=None, speech_timeout=None):
    import numpy as np

    if max_duration <= 0 or (speech_timeout is not None and speech_timeout <= 0):
        raise ValueError("capture durations must be positive")
    stop = threading.Event()
    preroll = collections.deque(maxlen=voice_confirm + 5)
    speech = []
    voice_run = silence_run = 0
    max_samples = max(1, int(max_duration * RATE))
    samples = 0
    with closing(_audio_chunks(device, stop)) as chunks:
        baseline = _warmup_audio(chunks, _rms, stop)
        threshold = max(threshold, min(int(baseline * 2) + 300, 20000))
        logui.print(f"  threshold:{threshold}  listening...")
        deadline = time.monotonic() + (speech_timeout or max_duration)
        for chunk, gap in chunks:
            if time.monotonic() >= deadline:
                break
            if chunk is None:
                continue
            if gap:
                raise RuntimeError("microphone dropped audio during recording")
            voiced = _rms(chunk) > threshold
            if not speech:
                preroll.append(chunk)
                voice_run = voice_run + 1 if voiced else 0
                if voice_run < voice_confirm:
                    continue
                speech = list(preroll)
                samples = sum(len(block) for block in speech)
                deadline = time.monotonic() + max_duration
                logui("audio.voice", state="recording")
            else:
                speech.append(chunk)
                samples += len(chunk)
                silence_run = 0 if voiced else silence_run + 1
            if silence_run >= silence_confirm or samples >= max_samples:
                break
    if not speech:
        return None, 0
    if silence_run:
        speech = speech[:-silence_run]
    data = np.concatenate(speech)[:max_samples]
    logui("audio.voice", state="done")
    return data, len(data) / RATE


# ── Ops ────────────────────────────────────────────────────────────────────────


def discover() -> dict:
    """Enumerate physical mics and speakers via sounddevice, WASAPI only."""
    import sounddevice as sd
    hostapis = sd.query_hostapis()
    wasapi   = next((i for i, h in enumerate(hostapis) if "wasapi" in h["name"].lower()), None)
    mics, speakers = [], []
    for i, d in enumerate(sd.query_devices()):
        if wasapi is not None and d["hostapi"] != wasapi:
            continue
        if d["max_input_channels"]  > 0:
            mics.append({"name": f"mic-{len(mics)}", "identifier": i,
                         "description": d["name"], "samplerate": int(d["default_samplerate"]),
                         "channels": d["max_input_channels"]})
        if d["max_output_channels"] > 0:
            speakers.append({"name": f"speaker-{len(speakers)}", "identifier": i,
                             "description": d["name"], "samplerate": int(d["default_samplerate"]),
                             "channels": d["max_output_channels"]})
    return {"mic": mics, "speaker": speakers}


def list_devices():
    try:
        import json as _j
        data = _j.loads((_OUT / "devices.json").read_text(encoding="utf-8"))
        inputs  = [(d["identifier"], d["name"]) for d in data.get("mic", [])]
        outputs = [(d["identifier"], d["name"]) for d in data.get("speaker", [])]
    except Exception:
        inputs, outputs = [], []

    active_idx, _ = _resolve_device()
    cfg_out = _out_device_index()

    logui.print("  input devices:")
    for i, name in inputs:
        logui.print(f"  {name:<15} {i}" + ("  ◀" if i == active_idx else ""))

    logui.print("  output devices:")
    for i, name in outputs:
        logui.print(f"  {name:<15} {i}" + ("  ◀" if i == cfg_out else ""))

    fc = _file_cfg()
    if fc:
        logui.print(f"  .out config ({CONFIG_FILE}):")
        for k, v in fc.items(): logui.print(f"    {k}: {v}")


def record(filename=None, max_duration=30, threshold=None, device=None):
    """
    Dynamic capture until silence, save to .out/audio/.
    device — configured name or local index
    Push devices: emits record-request SSE, owning browser handles capture.
    """
    idx, url = _resolve_device(device)
    device_name = str(device if device is not None else idx if idx is not None else "default")

    if url and url.startswith("push://"):
        logui("audio.record-request", device=device_name)
        return None

    threshold = _stt_thresh(threshold)
    label     = _device_label(idx, url)
    logui.print(f"  record  device:{label}  threshold:{threshold}  max:{max_duration}s  Ctrl+C to stop")
    audio_data, duration_s = _capture_dynamic(threshold, max_duration, device=device)
    if audio_data is None:
        logui.print("  nothing captured"); return None
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = filename or os.path.join(AUDIO_DIR, f"audio_{ts}.wav")
    _save_wav(audio_data, path)
    _trim_clips()
    logui("audio.record", duration=f"{duration_s:.1f}s", path=path)
    return path


def detect_noise(on_noise=None, score_fn=None, device=None,
                 continuous=True, read_fps=30, emit_fps=5, stop_event=None):
    if read_fps <= 0 or emit_fps <= 0:
        raise ValueError("read_fps and emit_fps must be positive")
    score_fn = score_fn or score_rms
    on_noise = on_noise or _default_on_noise
    idx, url = _resolve_device(device)
    device_name = str(device if device is not None else idx if idx is not None else "default")
    if url and url.startswith("push://"):
        logui("audio.noise-request", device=device_name)
        return
    stop = stop_event if stop_event is not None else threading.Event()
    state = {}
    last_emit = 0.0
    blocksize = max(1, int(RATE / read_fps))
    logui.print(f"  detect-noise  device:{device_name}  Ctrl+C to stop")
    try:
        with closing(_audio_chunks(device, stop, blocksize=blocksize)) as chunks:
            _warmup_audio(chunks, lambda chunk: score_fn(chunk, state), stop,
                          minimum=max(1, math.ceil(read_fps / 2)),
                          maximum=max(1, math.ceil(read_fps * 3)), tolerance=5)
            if stop.is_set():
                return
            logui("audio.noise-started", device=device_name)
            for chunk, gap in chunks:
                if chunk is None:
                    continue
                if gap:
                    state.clear()
                score = score_fn(chunk, state)
                now = time.monotonic()
                if score > 0 and now - last_emit >= 1.0 / emit_fps:
                    last_emit = now
                    on_noise(device_name, score)
                    if not continuous:
                        return
    except KeyboardInterrupt:
        logui.print(f"  stopped  device:{device_name}")
    finally:
        logui("audio.noise-off", device=device_name)


def detect_noise_off(device=None):
    """Emit noise-off SSE. Actual proc is killed server-side."""
    logui("audio.noise-off", device=str(device or 'default'))


def _default_on_noise(device, score):
    """Default on_noise — log the event."""
    logui("audio.noise", device=device, score=round(score, 1))


# ── Audio score functions ─────────────────────────────────────────────────────
#
# All: score_fn(samples, state) → float   (0–100 normalised)
# samples is int16 numpy array. state is mutable dict.

def score_rms(samples, state):
    """RMS with EMA baseline + hard floor. Portable (same math works in JS Web Audio)."""
    import numpy as np
    raw = math.sqrt(float(np.mean(samples.astype(np.float64) ** 2)))
    # normalise: typical mic RMS 0–10000 → 0–100
    raw = min(100.0, raw / 100.0)
    ema = state.get("ema", raw)
    ema = ema * 0.95 + raw * 0.05
    state["ema"] = ema
    score = max(0.0, raw - ema)
    return round(score, 2) if score > 2.0 else 0.0


_push_listening: set[str] = set()


def push_open(device, client_ip):
    from urllib.parse import urlparse
    source = _named_devices().get(device)
    if not str(source).startswith("push://") or client_ip != urlparse(source).hostname:
        raise PermissionError("microphone belongs to another browser host")
    if source in _push_listening:
        raise ValueError("already listening on this device")
    _push_listening.add(source)
    return {"device": device, "source": source, "raw": queue.Queue(maxsize=32),
            "stop": threading.Event()}


def push_feed(session, data):
    if not data or len(data) % 2 or len(data) > 131072:
        raise ValueError("expected mono int16 PCM")
    try:
        session["raw"].put_nowait(data)
    except queue.Full as error:
        raise ValueError("audio upload queue is full") from error


def push_listen(session, options, on_ready):
    import numpy as np
    rate = int(options.get("rate", RATE))
    if not 8000 <= rate <= 192000:
        raise ValueError("invalid sample rate")

    def capture(stop):
        on_ready()
        received = time.monotonic()
        while not session["stop"].is_set() and not stop.is_set():
            try:
                data = session["raw"].get(timeout=0.1)
                received = time.monotonic()
                yield np.frombuffer(data, dtype="<i2"), rate, False
            except queue.Empty:
                if time.monotonic() - received >= 5:
                    raise RuntimeError("microphone upload stalled")
                yield None, 0, False

    return listen(device=session["device"], capture=capture,
                  model_size=options.get("model"), stt_engine=options.get("stt_engine", "faster-whisper"),
                  max_duration=float(options.get("max_duration", 30)),
                  continuous=bool(int(options.get("continuous", 0))),
                  timeout=float(options["timeout"]) if options.get("timeout") else None)


def push_close(session):
    session["stop"].set()
    _push_listening.discard(session["source"])
    logui("audio.listen-stop", device=session["device"])


def _capture_audio(device, stop):
    """Yield (mono int16 samples, sample rate, gap) from the local microphone."""
    idx, url = _resolve_device(device, strict=True)
    if url:
        raise ValueError("local microphone required")
    raw = queue.Queue(maxsize=128)
    overflow = threading.Event()

    def capture(indata, frames, time_info, status):
        try:
            raw.put_nowait((indata[:, 0].copy(), bool(status.input_overflow)))
        except queue.Full:
            overflow.set()

    stream = _open_input_stream_samplerate_first(_require_sd(), idx, capture, blocksize=0)
    received = time.monotonic()
    with stream:
        logui("audio.voice", state="listening", device=device)
        while not stop.is_set():
            if overflow.is_set():
                raise RuntimeError("microphone capture queue is full; processing cannot keep up")
            try:
                pcm, gap = raw.get(timeout=0.1)
                received = time.monotonic()
                yield pcm, int(stream.samplerate), gap
            except queue.Empty:
                if not stream.active:
                    raise RuntimeError("microphone stream stopped")
                if time.monotonic() - received >= 5:
                    raise RuntimeError("microphone capture stalled")
                yield None, 0, False


def _capture_network(url, stop):
    import av
    with av.open(url, timeout=5) as source:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        for frame in source.decode(audio=0):
            if stop.is_set():
                return
            for converted in resampler.resample(frame):
                yield converted.to_ndarray().ravel(), RATE, False
        for converted in resampler.resample(None):
            yield converted.to_ndarray().ravel(), RATE, False


def _audio_chunks(device, stop, capture=None, blocksize=CHUNK):
    import av
    import numpy as np
    if capture is None:
        _, url = _resolve_device(device, strict=True)
        if url:
            if not str(url).startswith(("http://", "https://", "rtsp://")):
                raise ValueError("capture requires a local microphone or network audio URL")
            capture = lambda event: _capture_network(url, event)
        else:
            capture = lambda event: _capture_audio(device, event)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    pending = np.empty(0, dtype=np.int16)
    interrupted = False
    with closing(capture(stop)) as source:
        for pcm, rate, gap in source:
            if stop.is_set():
                return
            if pcm is None:
                yield None, False
                continue
            if gap:
                pending = np.empty(0, dtype=np.int16)
                resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
                interrupted = True
            frame = av.AudioFrame.from_ndarray(pcm[None, :], format="s16", layout="mono")
            frame.sample_rate = rate
            for converted in resampler.resample(frame):
                pending = np.concatenate((pending, converted.to_ndarray().ravel()))
            while len(pending) >= blocksize:
                chunk, pending = pending[:blocksize], pending[blocksize:]
                yield chunk, interrupted
                interrupted = False


def _speech_segments(device, max_duration, timeout, stop, capture=None):
    """Yield mono float32 speech at 16 kHz from either capture source."""
    import numpy as np
    _require_whisper()  # Keep the Windows import guard for the bundled VAD too.
    from faster_whisper.vad import get_vad_model

    vad = get_vad_model()
    vad(np.zeros(CHUNK * 8, dtype=np.float32))
    history = collections.deque(maxlen=32)  # one second of context for bundled VAD
    preroll = collections.deque(maxlen=5)
    fresh, speech, silence = [], [], 0
    max_chunks = max(1, math.ceil(max_duration * RATE / CHUNK))
    idle_since = time.monotonic()
    with closing(_audio_chunks(device, stop, capture)) as source:
        for pcm, gap in source:
            if stop.is_set():
                return
            if not speech and timeout is not None and time.monotonic() - idle_since >= timeout:
                return
            if pcm is None:
                continue
            if gap:
                # Don't stitch speech across missing samples or retain stale VAD context.
                history.clear()
                preroll.clear()
                fresh, speech, silence = [], [], 0
                idle_since = time.monotonic()
                logui.warn("audio.capture", msg="microphone dropped audio; discarded interrupted phrase, listening resumed")
            chunk = pcm.astype(np.float32) / 32768.0
            history.append(chunk)
            fresh.append(chunk)
            if len(fresh) < 8:
                continue
            # Re-evaluate a short rolling window every 256 ms. The bundled
            # callable owns its model state/layout; no ONNX internals here.
            probabilities = vad(np.concatenate(history))[-len(fresh):]
            for block, probability in zip(fresh, probabilities):
                voiced = float(probability) >= (0.35 if speech else 0.5)
                if not speech:
                    if not voiced:
                        preroll.append(block)
                        continue
                    speech = list(preroll)
                    preroll.clear()
                    logui("audio.voice", state="recording", device=device)
                speech.append(block)
                silence = 0 if voiced else silence + 1
                idle_since = time.monotonic()
                if silence >= 16 or len(speech) >= max_chunks:
                    # Retain a little padding, not the full endpoint pause.
                    end = len(speech) - max(0, silence - 5)
                    segment = np.concatenate(speech[:end])
                    speech, silence = [], 0
                    logui("audio.voice", state="done", device=device)
                    yield segment
                    if stop.is_set():
                        return
            fresh.clear()


def listen(model_size=None, max_duration=30, continuous=False, wake_word=None,
           device=None, on_transcribe=None, quiet=False, timeout=None,
           stt_engine="faster-whisper", capture=None):
    """Capture live speech and emit completed phrases, without recording files.

    continuous keeps capture running while one decoder processes a bounded queue.
    on_transcribe(text) runs in the caller's thread for each nonempty result.
    stt_engine is a registered name or a callable: mono float32 at 16 kHz -> str.
    timeout ends the session after that many seconds waiting for speech.
    wake_word, when supplied, gates the start of this listening session.
    Push requests signal the owning browser and return, like camera.stream.
    capture(stop) can supply PCM to this same pipeline in the web process.
    """
    if max_duration <= 0 or (timeout is not None and timeout <= 0):
        raise ValueError("max_duration and timeout must be positive")
    if not callable(stt_engine) and stt_engine not in STT_ENGINES:
        raise ValueError(f"unknown STT engine '{stt_engine}'; available: {', '.join(STT_ENGINES)}")
    device = _resolve("device", device)
    _, source = _resolve_device(device, strict=True)
    if source and capture is None:
        if not source.startswith("push://"):
            raise ValueError("listen requires a local microphone or push device")
        if callable(stt_engine) or on_transcribe or wake_word:
            raise ValueError("browser listen uses a named STT engine and emits audio.transcript events")
        import device as _device
        device_id = next((d.get("deviceId", "") for d in _device.devices_read("mic") if d.get("name") == device), "")
        logui("audio.listen-start", device=device, source=source, device_id=device_id,
              model=model_size, max_duration=max_duration, continuous=int(continuous),
              stt_engine=stt_engine, timeout=timeout)
        return
    transcribe = stt_engine if callable(stt_engine) else STT_ENGINES[stt_engine](model_size)
    if wake_word:
        wake(word=wake_word, device=device)
    segments = queue.Queue(maxsize=2)
    stop, done = threading.Event(), threading.Event()
    errors = []

    def produce():
        try:
            with closing(_speech_segments(device, max_duration, timeout, stop, capture)) as source:
                for segment in source:
                    try:
                        segments.put_nowait(segment)
                    except queue.Full:
                        raise RuntimeError("listen transcription cannot keep up; use a faster STT engine or model")
                    if not continuous:
                        break
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=produce, name="audio-capture", daemon=True)
    if not quiet:
        logui.print(f"  listen  device:{device if device is not None else 'default'}"
                    f"  mode:{'continuous' if continuous else 'once'}  Ctrl+C to stop")
    worker.start()
    try:
        while True:
            if errors:
                raise errors[0]
            try:
                samples = segments.get(timeout=0.1)
            except queue.Empty:
                if done.is_set():
                    if errors:
                        raise errors[0]
                    return None
                continue
            if not continuous:
                worker.join(timeout=2)
                if worker.is_alive():
                    raise RuntimeError("microphone did not stop")
                if errors:
                    raise errors[0]
            text = transcribe(samples).strip()
            if text:
                if not quiet:
                    logui("audio.transcript", ts=datetime.now().strftime("%H:%M:%S"), text=text, device=device)
                if on_transcribe:
                    on_transcribe(text)
            if not continuous:
                return text
    finally:
        stop.set()
        worker.join(timeout=2)
        if worker.is_alive():
            logui.warn("audio.error", msg="capture worker did not stop within 2 seconds")


def listen_off(device=None):
    """Ask the owning browser to release its microphone, like camera.stream_off."""
    device = _resolve("device", device)
    _resolve_device(device, strict=True)
    logui("audio.listen-stop", device=device)


def speak(text, service=None, voice=None, out_device=None):
    """Text-to-speech. Blocks until done.
    out_device — index (param → config → default)
    service    — 'edge' | 'os' (param → config → 'edge')
    voice      — TTS voice name (param → config → AriaNeural)
    """
    service    = _tts_service(service)
    voice      = _tts_voice(voice)
    out_device = _out_device_index(out_device)
    if service == "os":
        if out_device is not None:
            raise ValueError("selecting a speaker requires --service edge")
        _speak_os(text)
    else:
        _speak_edge(text, voice, out_device)
    logui("audio.tts", state="done")


def _speak_os(text):
    try:
        import pyttsx3
    except ImportError:
        raise Exception("os tts requires: pip install pyttsx3")
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()


def _speak_edge(text, voice, out_device=None):
    import tempfile
    try:
        import edge_tts
    except ImportError:
        raise Exception("edge tts requires: pip install edge-tts")

    async def _synth(tmp):
        await edge_tts.Communicate(text, voice).save(tmp)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_mp3 = f.name

    try:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, _synth(tmp_mp3)).result()
            else:
                loop.run_until_complete(_synth(tmp_mp3))
        except RuntimeError:
            asyncio.run(_synth(tmp_mp3))
    except Exception as e:
        raise Exception(f"edge-tts synthesis failed: {e}")

    try:
        import numpy as np
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_mp3(tmp_mp3)
            seg = seg.set_frame_rate(RATE).set_channels(1).set_sample_width(2)
            pcm = np.frombuffer(seg.raw_data, dtype=np.int16).astype(np.float32) / 32768.0
        except ImportError:
            import subprocess
            tmp_wav = tmp_mp3.replace(".mp3", ".wav")
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_mp3,
                 "-ar", str(RATE), "-ac", "1", "-f", "wav", tmp_wav],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            with wave.open(tmp_wav, "rb") as wf:
                raw = wf.readframes(wf.getnframes())
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            os.unlink(tmp_wav)

        sd = _require_sd()
        sd.play(pcm, samplerate=RATE, device=out_device)
        sd.wait()
    finally:
        try: os.unlink(tmp_mp3)
        except: pass


# ── Wake word ──────────────────────────────────────────────────────────────────

VOSK_MODEL_URL  = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_DIR = str(_OUT / "models" / VOSK_MODEL_NAME)

def _ensure_wake_model(model_path=None):
    configured = _wake_model_path(model_path)
    if configured and os.path.exists(configured):
        return configured
    if os.path.exists(VOSK_MODEL_DIR):
        return VOSK_MODEL_DIR
    import urllib.request, zipfile, tempfile
    os.makedirs(os.path.dirname(VOSK_MODEL_DIR), exist_ok=True)
    logui.print(f"  vosk model not found — downloading {VOSK_MODEL_NAME}...")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        tmp_zip = f.name
    def _progress(count, block, total):
        pct = min(100, int(count * block * 100 / total))
        logui.print(f"  downloading... {pct}%", end="\r")
    urllib.request.urlretrieve(VOSK_MODEL_URL, tmp_zip, _progress)
    logui.print("  downloading... 100%  ")
    logui.print("  extracting...")
    with zipfile.ZipFile(tmp_zip, "r") as zf:
        zf.extractall(_OUT / "models")
    os.unlink(tmp_zip)
    logui.print(f"  model ready: {VOSK_MODEL_DIR}")
    return VOSK_MODEL_DIR

def wake(word=None, device=None, model_path=None, on_wake=None):
    """
    Wake word detection via vosk. Blocks until word heard, then returns.
    word       — overrides audio_config.json
    device     — name or index override
    model_path — vosk model dir (auto-downloaded to .out/models/ if absent)
    on_wake()  — hook called when word detected
    """
    import json as _json
    word = _wake_word(word)
    if not word:
        raise Exception("wake word required — pass word= or set wake_word in audio_config.json")
    try:
        from vosk import Model, KaldiRecognizer
        import vosk; vosk.SetLogLevel(-1)
    except ImportError:
        raise Exception("wake word requires: pip install vosk")

    model_path = _ensure_wake_model(model_path)
    idx, url   = _resolve_device(device)
    if url:
        raise Exception("wake word detection requires local mic, not net source")
    vmodel     = _VOSK_CACHE.get(model_path)
    if vmodel is None:
        vmodel = Model(model_path)
        _VOSK_CACHE[model_path] = vmodel
    recognizer = KaldiRecognizer(vmodel, RATE)
    heard = False
    label = _device_label(idx, None)
    stop = threading.Event()
    logui.print(f"  wake  word:'{word}'  device:{label}  Ctrl+C to stop")
    with closing(_audio_chunks(device, stop)) as chunks:
        logui("audio.wake", state="waiting", word=word, device=label, rate=RATE)
        for chunk, gap in chunks:
            if chunk is None:
                continue
            if gap:
                recognizer.Reset()
            if recognizer.AcceptWaveform(chunk.tobytes()):
                result = _json.loads(recognizer.Result()).get("text", "").lower()
                if word in result:
                    logui("audio.wake", heard=result)
                    heard = True
                    break
    if heard and on_wake:
        on_wake()


def refresh():
    """Preload audio models: STT (whisper) + wake word (vosk)."""
    try:
        _load_whisper()
    except Exception:
        pass
    try:
        _ensure_wake_model()
    except Exception:
        pass


def _discover():
    _DEVICES_F = _OUT / "devices.json"
    found = discover()
    if not found:
        return
    try:
        existing = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except Exception:
        existing = {}
    existing["mic"] = found.get("mic", [])
    existing["speaker"] = found.get("speaker", [])
    for m in found.get("mic", []):
        logui("device.discovered", name=m["name"], identifier=str(m["identifier"]), type="mic")
    for s in found.get("speaker", []):
        logui("device.discovered", name=s["name"], identifier=str(s["identifier"]), type="speaker")
    _OUT.mkdir(parents=True, exist_ok=True)
    _DEVICES_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")


import device as _device


CLI_SCHEMA = {"ops": {
    "list": {},
    "record": {"params": ["--device", "--threshold", "--max-duration"]},
    "detect-noise": {"params": ["--device", "--continuous", "--read-fps", "--emit-fps"]},
    "detect-noise-off": {"params": ["--device"]},
    "listen": {"params": ["--device", "--stt-engine", "--model", "--max-duration", "--continuous", "--wake"]},
    "listen-off": {"params": ["--device"]},
    "speak": {"args": "text", "params": ["--voice", "--service", "--out-device"]},
    "wake": {"params": ["--word", "--device"]},
}}


def cli(argv):
    """Parse audio command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="audio", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--threshold",    type=int,    default=None)
    p.add_argument("--model",        default=None)
    p.add_argument("--stt-engine",   choices=STT_ENGINES, default="faster-whisper")
    p.add_argument("--max-duration", type=int,    default=30,   dest="max_duration")
    p.add_argument("--continuous",   action="store_true",        default=False)
    p.add_argument("--wake",         default=None,               dest="wake_word")
    p.add_argument("--voice",        default=None)
    p.add_argument("--service",      default=None)
    p.add_argument("--word",         default=None)
    p.add_argument("--device",       default=None)               # name or int index
    p.add_argument("--out-device",   default=None, dest="out_device")
    p.add_argument("--read-fps",     type=float,  default=None,  dest="read_fps")
    p.add_argument("--emit-fps",     type=float,  default=None,  dest="emit_fps")
    args = p.parse_args(argv)

    a = args.action
    try:
        if   a == "list":         list_devices()
        elif a == "record":       record(threshold=args.threshold, max_duration=args.max_duration,
                                      device=args.device)
        elif a == "detect-noise":
            kw = dict(device=args.device, continuous=args.continuous)
            if args.read_fps: kw["read_fps"] = args.read_fps
            if args.emit_fps: kw["emit_fps"] = args.emit_fps
            detect_noise(**kw)
        elif a == "detect-noise-off": detect_noise_off(device=args.device)
        elif a == "listen-off": listen_off(device=args.device)
        elif a == "listen":
            if any(v is not None for v in (args.voice, args.out_device)):
                raise ValueError("audio listen selects its microphone with --device")
            if args.threshold is not None:
                raise ValueError("audio listen uses speech detection; --threshold is not supported")
            listen(model_size=args.model, max_duration=args.max_duration,
                         continuous=args.continuous, wake_word=args.wake_word,
                         device=args.device, stt_engine=args.stt_engine)
        elif a == "speak":
            text = " ".join(args.args) if args.args else None
            if not text: print("  ERROR: audio speak <text>"); return
            speak(text, service=args.service, voice=args.voice,
                        out_device=args.out_device)
        elif a == "wake":
            wake(word=args.word, device=args.device)
    except Exception as e:
        print(f"  ERROR: {e}")
