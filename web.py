"""
web — dashboard server and browser

Usage:
  web run [--port N]
  web open [--port N]
  web stop
  web status
  web log

The default port is 7000. log follows .out/events.log until Ctrl+C.

Help: help web | web --help
"""


from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError:
    print("web requires: pip install fastapi uvicorn"); sys.exit(1)

from dotenv import load_dotenv
import device as _device
import logui as _logui
import mediactl as _mediactl
import users as _users
import web_api as _api

HERE = Path(__file__).parent
UI_DIR = HERE / "ui"
_OUT = HERE / ".out"
PID_FILE = _OUT / "web.pid"
MEDIACTL = HERE / "mediactl.py"
EVENTS_LOG = _OUT / "events.log"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 7000


# Web serving

class _LazyStaticFiles(StaticFiles):
    async def check_config(self):
        if self.directory is not None and not os.path.exists(self.directory):
            return
        await super().check_config()


app = FastAPI(title="mediactl-web", docs_url=None, redoc_url=None)
app.post("/api/run")(_api.api_run)
app.post("/telegram/callback")(_api.telegram_callback)
app.get("/api/schema")(_api.api_schema)
app.get("/api/genres")(_api.api_genres)
app.get("/api/media/browse")(_api.api_media_browse)
app.get("/api/media/info")(_api.api_media_info)
app.get("/api/media/torrents")(_api.api_media_torrents)
app.get("/api/media/recommendations")(_api.api_media_recommendations)
app.post("/api/library/folder")(_api.api_library_folder)
app.get("/api/library")(_api.api_library)
app.get("/api/dashboard")(_api.api_dashboard_get)
app.post("/api/dashboard")(_api.api_dashboard_post)
app.get("/api/dashboard-templates")(_api.api_dashboard_templates_list)
app.post("/api/dashboard-template/apply")(_api.api_dashboard_template_apply)
app.post("/api/emit")(_api.api_emit)
app.get("/api/events")(_api.api_events)
app.get("/api/tail")(_api.api_tail)
app.get("/api/snapshot")(_api.api_snapshot)
app.get("/api/snapshots")(_api.api_snapshots)
app.get("/api/security/{session_id}/thumbs")(_api.api_security_thumbs)
app.post("/api/faces/promote")(_api.api_faces_promote)
app.get("/api/me")(_api.api_me)
app.post("/api/logout")(_api.api_logout)
app.post("/api/config")(_api.api_config_post)
app.get("/api/config")(_api.api_config)
app.get("/api/host")(_api.api_host)
app.get("/api/whoami")(_api.api_whoami)
app.get("/api/cameras")(_api.api_cameras)
app.post("/api/users/redeem")(_api.api_users_redeem)
app.post("/api/users/devices")(_api.api_users_devices)
app.post("/api/devices/status")(_api.api_devices_status)
app.post("/api/devices/manual")(_api.api_devices_manual)
app.websocket("/api/audio/push/{device}")(_api.api_audio_push)
app.post("/api/camera/push/{name}")(_api.api_camera_push)
app.post("/api/camera/request/{name}")(_api.api_camera_request)
app.post("/api/camera/release/{name}")(_api.api_camera_release)
app.get("/api/camera/latest/{name}")(_api.api_camera_latest)
app.post("/api/camera/forcerelease/{name}")(_api.api_camera_forcerelease)
app.post("/api/camera/motion/{name}")(_api.api_camera_motion_start)
app.post("/api/camera/motion/off/{name}")(_api.api_camera_motion_off)
for name in ("snapshots", "faces", "faces-unknown", "stream", "incidents", "security"):
    app.mount(f"/{name}", _LazyStaticFiles(directory=str(_OUT / name), check_dir=False), name=name)

push_app = FastAPI(title="mediactl-push", docs_url=None, redoc_url=None)
push_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST"], allow_headers=["*"])
push_app.post("/api/camera/push/{name}")(_api.push_app_camera_push)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Always serve the shell. Auth is checked client-side on load via /api/me —
    if 401, the registration modal appears. This allows the page to bootstrap
    on first use without a redirect loop.
    """
    f = UI_DIR / "web.html"
    return HTMLResponse(f.read_text(encoding="utf-8")) if f.exists() \
        else HTMLResponse("<h1>web.html not found</h1>", 500)


@app.get("/{filename}.js")
async def serve_js(filename: str):
    f = UI_DIR / f"{filename}.js"
    if not f.exists(): raise HTTPException(404, f"{filename}.js not found")
    return FileResponse(str(f), media_type="application/javascript")


@app.get("/manifest.json")
async def manifest():
    return JSONResponse({
        "name":             "mediactl",
        "short_name":       "mediactl",
        "start_url":        "/",
        "display":          "standalone",
        "background_color": "#0f0f0f",
        "theme_color":      "#0f0f0f",
        "icons": [
            {"src": "/snapshots/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/snapshots/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    })


@push_app.get("/")
async def push_app_index():
    """
    Landing page for the HTTPS push server.
    Visited once by the device to accept the self-signed cert.
    """
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset=UTF-8>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mediactl camera</title>
<style>
  body{font-family:sans-serif;background:#0a0b0f;color:#e8eaf0;
       display:flex;align-items:center;justify-content:center;
       height:100vh;margin:0;text-align:center;}
  h2{font-size:18px;margin-bottom:8px;}
  p{color:#7a7f90;font-size:13px;}
</style></head>
<body><div>
  <h2>✅ Certificate accepted</h2>
  <p>You can close this tab and return to the dashboard.<br>
  Your device camera will now work.</p>
</div></body></html>
""")


# Processes

_POPEN_KWARGS: dict = {}
if sys.platform == "win32":
    _POPEN_KWARGS["creationflags"] = subprocess.CREATE_NO_WINDOW

_server_start = time.time()
_procs: dict[str, subprocess.Popen] = {}
_proc_meta: dict[str, dict] = {}


def _ctl(*args, timeout=60) -> tuple[str, str, int]:
    cmd = [sys.executable, str(MEDIACTL)] + [str(a) for a in args]
    r   = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace", **_POPEN_KWARGS)
    return r.stdout, r.stderr, r.returncode


def _kill(proc_id: str):
    if proc_id in _procs:
        proc = _procs[proc_id]
        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception: pass
            # Give the process a moment to shut down gracefully via stdin watcher
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception: pass
        _procs.pop(proc_id, None)
        _proc_meta.pop(proc_id, None)


def _pipe_proc(proc_id: str, proc: subprocess.Popen, mirror: bool = True):
    """Thread: drain subprocess stdout. When mirror is True, copy it into events.log
    (for raw-output procs like shells). logui-based procs already write their own
    journal lines, so they start with mirror=False to avoid a second, racing writer."""
    try:
        for raw in proc.stdout:
            raw = raw.rstrip("\n")
            if raw and mirror:
                _logui.append_line(EVENTS_LOG, raw)
    except Exception:
        pass
    finally:
        _logui.append_line(EVENTS_LOG, f"[{datetime.now():%H:%M:%S}] proc.exit source={proc_id} code={proc.returncode}")
        _procs.pop(proc_id, None)
        _proc_meta.pop(proc_id, None)


def _proc_alive(pid: int) -> bool:
    try:
        import psutil; return psutil.pid_exists(pid)
    except ImportError: pass
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not h: return False
        ctypes.windll.kernel32.CloseHandle(h); return True
    else:
        try: os.kill(pid, 0); return True
        except (ProcessLookupError, PermissionError): return False


def _proc_kill(pid: int):
    try:
        import psutil; psutil.Process(pid).terminate(); return
    except ImportError: pass
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        os.kill(pid, signal.SIGTERM)


@app.post("/api/start/{proc_id}")
async def api_start(proc_id: str, request: Request, user: dict = Depends(_users.users_require)):
    body = await request.json()
    args = body.get("args", [])
    if not args: raise HTTPException(400, "args required")
    _kill(proc_id)
    cmd  = [sys.executable, '-u', str(MEDIACTL)] + [str(a) for a in args]
    # Since workers are now threads (not multiprocessing), CREATE_NO_WINDOW is safe.
    proc = subprocess.Popen(cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace",
                            **_POPEN_KWARGS)
    _procs[proc_id]     = proc
    _proc_meta[proc_id] = {"args": args, "started": datetime.now().strftime("%H:%M:%S"), "pid": proc.pid}
    mirror = bool(body.get("mirror", True))
    threading.Thread(target=_pipe_proc, args=(proc_id, proc, mirror), daemon=True).start()
    return JSONResponse({"ok": True, "pid": proc.pid})


@app.post("/api/stop/{proc_id}")
async def api_stop(proc_id: str, user: dict = Depends(_users.users_require)):
    _kill(proc_id); return JSONResponse({"ok": True})


@app.post("/api/proc/{proc_id}/stdin")
async def api_proc_stdin(proc_id: str, request: Request, user: dict = Depends(_users.users_require)):
    """Write one line to a running process's stdin (e.g. an interactive pairing code)."""
    proc = _procs.get(proc_id)
    if not proc or proc.poll() is not None or not proc.stdin:
        raise HTTPException(404, "process not running")
    text = str((await request.json()).get("text", ""))
    try:
        proc.stdin.write(text + "\n")
        proc.stdin.flush()
    except Exception as e:
        raise HTTPException(500, str(e))
    return JSONResponse({"ok": True})


@app.get("/api/status")
async def api_status(user: dict = Depends(_users.users_require)):
    dead = [k for k, p in _procs.items() if p.poll() is not None]
    for k in dead: _procs.pop(k); _proc_meta.pop(k, None)
    return JSONResponse({"running": {k: _proc_meta.get(k, {}) for k in _procs},
                         "uptime":  int(time.time() - _server_start)})


# Server lifecycle

def run(port: int = None, host: str = None):
    load_dotenv(HERE / ".env", interpolate=False)
    port = int(os.environ.get("WEB_PORT") or DEFAULT_PORT) if port is None else port
    host = (os.environ.get("WEB_HOST") or DEFAULT_HOST) if host is None else host
    try: from logui import logui
    except ImportError: logui = None

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if _proc_alive(pid):
                if logui: logui("web.run", msg="already running", pid=pid, port=port)
                else:     print(f"  already running  pid={pid}  http://localhost:{port}")
                open_browser(port)
                return
        except (ValueError, OSError): pass
        PID_FILE.unlink(missing_ok=True)

    python = str(Path(sys.executable).resolve())
    web_py = str((HERE / "web.py").resolve())
    # pass our PID so the server watchdog can exit when we (the caller) exit
    cmd    = [python, web_py, "_serve", "--port", str(port), "--host", host,
              "--owner-pid", str(os.getpid())]
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS |
                                   subprocess.CREATE_NEW_PROCESS_GROUP |
                                   subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True

    _ensure_cert()
    _users.bootstrap_admin()
    proc = subprocess.Popen(cmd, **kwargs)
    url = f"https://localhost:{port}"
    if logui: logui("web.run", url=url, pid=proc.pid)
    else:     print(f"  mediactl-web  {url}  pid={proc.pid}")
    open_browser(port)


def stop():
    try: from logui import logui
    except ImportError: logui = None
    if not PID_FILE.exists():
        if logui: logui("web.stop", msg="not running")
        else:     print("  not running")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        _proc_kill(pid)
        _mediactl.cli_module("notify").listen_stop()
        PID_FILE.unlink(missing_ok=True)
        if logui: logui("web.stop", pid=pid)
        else:     print(f"  stopped pid={pid}")
    except (ValueError, OSError) as e:
        PID_FILE.unlink(missing_ok=True)
        if logui: logui("web.stop", msg=str(e))
        else:     print(f"  stop failed: {e}")


def status():
    try: from logui import logui
    except ImportError: logui = None
    if not PID_FILE.exists():
        if logui: logui("web.status", running=False)
        else:     print("  not running")
        return
    try:
        pid   = int(PID_FILE.read_text().strip())
        alive = _proc_alive(pid)
    except (ValueError, OSError):
        alive = False; pid = "?"
    if alive:
        if logui: logui("web.status", running=True, pid=pid)
        else:     print(f"  running  pid={pid}")
    else:
        PID_FILE.unlink(missing_ok=True)
        if logui: logui("web.status", running=False, msg="stale pid")
        else:     print("  not running (stale pid)")


def _ensure_cert() -> tuple:
    """
    Generate a self-signed TLS cert in .out/ if not present or if the current
    LAN IP is not covered by the existing cert's SANs.
    Returns (cert_path, key_path) as strings, or (None, None) if cryptography unavailable.
    """
    cert_path = HERE / ".out" / "server.crt"
    key_path  = HERE / ".out" / "server.key"

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509 import SubjectAlternativeName, DNSName, IPAddress
        import datetime, socket, ipaddress

        # resolve current LAN IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            lan_ip = "127.0.0.1"

        # check if existing cert already covers this IP
        if cert_path.exists() and key_path.exists():
            try:
                existing = x509.load_pem_x509_certificate(cert_path.read_bytes())
                sans = existing.extensions.get_extension_for_class(SubjectAlternativeName)
                covered_ips = {str(v) for v in sans.value.get_values_for_type(IPAddress)}
                if lan_ip in covered_ips:
                    return str(cert_path), str(key_path)  # good, reuse
                print(f"  TLS cert does not cover {lan_ip} — regenerating")
            except Exception:
                pass  # can't read cert, regenerate

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .add_extension(SubjectAlternativeName([
                DNSName("localhost"),
                DNSName(socket.gethostname()),
                IPAddress(ipaddress.ip_address(lan_ip)),
                IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]), critical=False)
            .sign(key, hashes.SHA256())
        )
        (HERE / ".out").mkdir(parents=True, exist_ok=True)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        print(f"  TLS cert generated → {cert_path}  (LAN IP: {lan_ip})")
        return str(cert_path), str(key_path)

    except ImportError:
        print("  WARNING: cryptography not installed — running HTTP (getUserMedia will be blocked)")
        return None, None
    except Exception as e:
        print(f"  WARNING: TLS cert generation failed ({e}) — running HTTP")
        return None, None


def _serve(port: int = None, host: str = None, owner_pid: int = 0):
    """Internal: blocking server. Called by run() as a detached subprocess.
    If owner_pid is given, a watchdog thread shuts the server down when that
    process exits (i.e. when the mediactl session that launched us ends).
    """
    load_dotenv(HERE / ".env", interpolate=False)
    port = int(os.environ.get("WEB_PORT") or DEFAULT_PORT) if port is None else port
    host = (os.environ.get("WEB_HOST") or DEFAULT_HOST) if host is None else host
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    os.environ["WEB_PORT"] = str(port)

    if owner_pid:
        def _watchdog():
            while True:
                time.sleep(2)
                if not _proc_alive(owner_pid):
                    _mediactl.cli_module("notify").listen_stop()
                    PID_FILE.unlink(missing_ok=True)
                    os.kill(os.getpid(), signal.SIGTERM)
                    break
        threading.Thread(target=_watchdog, daemon=True).start()

    cert = str(HERE / ".out" / "server.crt")
    key  = str(HERE / ".out" / "server.key")

    try:
        dashboard_ensure_templates()
        _mediactl.run()
    except Exception:
        pass

    server = uvicorn.Server(uvicorn.Config(
        "web:app", host=host, port=port, log_level="warning",
        ssl_certfile=cert, ssl_keyfile=key))
    stopped = threading.Event()

    def _discover_after_start():
        while not server.started:
            if stopped.wait(0.1):
                return
        if stopped.wait(5):
            return
        try:
            _device.discover()
        except Exception as e:
            _logui.logui.err("device.discover.error", error=str(e))

    threading.Thread(target=_discover_after_start, daemon=True).start()
    try:
        server.run()
    finally:
        stopped.set()
        _mediactl.scheduler_stop()
        _mediactl.cli_module("notify").listen_stop()


def _serve_push(host: str, push_port: int):
    """Run the HTTPS push server in a thread. Called from _serve()."""
    cert, key = _ensure_cert()
    if not cert:
        print(f"  push server skipped — no TLS cert")
        return
    try:
        uvicorn.run(push_app, host=host, port=push_port,
                    ssl_certfile=cert, ssl_keyfile=key,
                    log_level="warning")
    except Exception as e:
        print(f"  push server error: {e}")


def open_browser(port: int = None):
    load_dotenv(HERE / ".env", interpolate=False)
    port = int(os.environ.get("WEB_PORT") or DEFAULT_PORT) if port is None else port
    import webbrowser
    url = f"https://localhost:{port}"
    webbrowser.open(url)
    try: from logui import logui; logui("web.open", url=url)
    except ImportError: print(f"  opening {url}")


# Dashboards

_DASH_DIR = _OUT / "dashboard"
_TEMPLATES = json.loads((HERE / "dashboard_templates.json").read_text(encoding="utf-8"))
_DEV_TILE_TYPES = frozenset({"camera-feed", "audio", "device"})


def dashboard_path(username: str, layout: str = "default") -> Path:
    layout = str(layout or "default").strip()
    if not layout or any(not seg.isalnum() for seg in layout.replace("-", "_").split("_")):
        layout = "default"
    return _DASH_DIR / f"{username}-{layout}.json"


def dashboard_load(username: str) -> Optional[dict]:
    _DASH_DIR.mkdir(parents=True, exist_ok=True)
    candidates = sorted(
        (p for p in _DASH_DIR.glob("*.json")
         if p.stem.startswith(f"{username}-")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    legacy = _DASH_DIR / f"{username}.json"
    if not candidates and legacy.exists():
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            migrated = dashboard_path(username, data.get("layout", "default"))
            migrated.write_text(json.dumps(data, indent=2), encoding="utf-8")
            legacy.unlink()
            candidates = [migrated]
        except Exception:
            pass
    if not candidates:
        return None
    try:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def dashboard_save(username: str, data: dict):
    _DASH_DIR.mkdir(parents=True, exist_ok=True)
    dashboard_path(username, data.get("layout", "default")).write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def dashboard_exists(username: str) -> bool:
    return any(_DASH_DIR.glob(f"{username}-*.json"))


def _dashboard_devices() -> dict:
    try:
        data = json.loads((_OUT / "devices.json").read_text(encoding="utf-8"))
        return {
            "cameras": [c["name"] for c in data.get("cameras", [])],
            "audios":  [m["name"] for m in data.get("mic", [])],
            "devices": data.get("devices", []),
        }
    except Exception:
        return {"cameras": ["laptop"], "audios": ["default"], "devices": []}


def _build_dashboard(name: str) -> dict:
    """Materialize a template by name, expanding dynamic device/camera/audio tiles."""
    t = _TEMPLATES[name]
    tiles = [dict(x) for x in t["tiles"]]
    if t.get("library") and not any(x["id"] == "library" for x in tiles):
        tiles.append({"id": "library", "type": "library", "w": 1, "h": 2})
    d = _dashboard_devices()
    if t.get("include_devices"):
        for dev in d["devices"]:
            n = dev.get("name") or dev.get("identifier")
            if not n: continue
            tiles.append({
                "id": f"device-{n}", "type": "device", "w": 1, "h": 1,
                "config": {"name": n, "label": n, "ip": dev.get("identifier", "")},
            })
    if t.get("include_cameras"):
        for cam in d["cameras"]:
            tiles.append({
                "id": f"camera-{cam}", "type": "camera-feed", "w": 1, "h": 1,
                "config": {"device": cam, "label": cam},
            })
    if t.get("include_audios"):
        for mic in d["audios"]:
            tiles.append({
                "id": f"audio-{mic}", "type": "audio", "w": 1, "h": 1,
                "config": {"device": mic, "label": mic},
            })
    view = {"cols": t.get("cols", 5), "size": t.get("size", "small"), **t.get("view", {})}
    return {"tiles": tiles, "view": view, "layout": name}


def default_dashboard() -> dict:
    return _build_dashboard("default")


def media_focus_dashboard() -> dict:
    return _build_dashboard("media")


def dashboard_ensure_templates():
    """Materialize any missing preset dashboard templates to disk."""
    _DASH_DIR.mkdir(parents=True, exist_ok=True)
    for name in _TEMPLATES:
        f = _DASH_DIR / f"layout-{name}.json"
        if not f.exists():
            legacy = _OUT / f"dashboard-{name}.json"
            if legacy.exists():
                f.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                f.write_text(json.dumps(_build_dashboard(name), indent=2), encoding="utf-8")
        legacy = _OUT / f"dashboard-{name}.json"
        if legacy.exists():
            legacy.unlink()

    for f in _DASH_DIR.glob("*-browse.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("layout") != "browse":
                continue
            view = data.setdefault("view", {})
            if "editable" not in view or "adjustable_columns" not in view:
                view.update(_TEMPLATES["browse"]["view"])
                f.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            continue


def dashboard_create(user: dict):
    """Write the dashboard for a user from shared discovered devices."""
    _DASH_DIR.mkdir(parents=True, exist_ok=True)
    layout = "default" if "admin" in user.get("tags", []) else "media"
    path = dashboard_path(user['name'], layout)
    template = _DASH_DIR / f"layout-{layout}.json"
    if template.exists():
        data = json.loads(template.read_text(encoding="utf-8"))
    else:
        data = _build_dashboard(layout)
    data["layout"] = layout
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _logui.logui("web.dashboard-created", name=user["name"])


def _dashboard_prepare_media(data: dict) -> dict:
    """Prepare a media dashboard for per-user persistence."""
    media = dict(data)
    media["layout"] = "media"
    tiles = media.get("tiles") if isinstance(media.get("tiles"), list) else []
    media["tiles"] = [t for t in tiles
                       if isinstance(t, dict) and t.get("type") not in _DEV_TILE_TYPES]
    return media


def dashboard_sync_user_devices(user: dict):
    import users
    dashboard_sync_devices(user)
    admin = users.users_find_admin()
    if admin and admin["id"] != user["id"]:
        dashboard_sync_devices(admin)


def dashboard_sync_devices(user: dict):
    """Drop stale device tiles, append tiles for new devices; keep all other tiles and view."""
    _DASH_DIR.mkdir(parents=True, exist_ok=True)
    existing = dashboard_load(user['name'])
    if not existing:
        dashboard_create(user)
        return

    layout = existing.get("layout", "default")
    path = dashboard_path(user['name'], layout)

    full_default = default_dashboard()
    wanted_order = [] if existing.get("layout") in ("media", "browse") else [
        t for t in full_default["tiles"] if t.get("type") in _DEV_TILE_TYPES
    ]
    wanted_ids = {t["id"] for t in wanted_order}
    tiles = existing.get("tiles") or []
    if not isinstance(tiles, list):
        tiles = []
    kept = [t for t in tiles if not (t.get("type") in _DEV_TILE_TYPES and t.get("id") not in wanted_ids)]
    have = {t["id"] for t in kept if t.get("id")}
    by_id = {t["id"]: t for t in tiles if t.get("id")}
    for wt in wanted_order:
        wid = wt.get("id")
        if not wid or wid in have:
            continue
        kept.append(by_id.get(wid, wt))
        have.add(wid)

    old_ids = [t.get("id") for t in tiles if t.get("id")]
    new_ids = [t.get("id") for t in kept if t.get("id")]
    if old_ids == new_ids:
        return

    out = dict(existing)
    out["tiles"] = kept
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    _logui.logui("web.dashboard-created", name=user["name"])


def dashboard_save_layout(username: str, data: dict, template: str = ""):
    if template and not re.fullmatch(r"[a-zA-Z0-9]+(?:[_-][a-zA-Z0-9]+)*", template):
        raise ValueError("invalid template name")
    data["layout"] = template or data.get("layout") or "default"
    if data.get("layout") == "media":
        data = _dashboard_prepare_media(data)
    dashboard_save(username, data)
    if template:
        (_DASH_DIR / f"layout-{template}.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )


def dashboard_list_templates() -> list[dict]:
    out = []
    for p in sorted(_DASH_DIR.glob("layout-*.json")):
        try: d = json.loads(p.read_text(encoding="utf-8"))
        except Exception: continue
        out.append({"name": p.stem.removeprefix("layout-"),
                    "tiles": len(d.get("tiles", []))})
    return out


def dashboard_apply_template(user: dict, name: str, from_layout=None, size=None, cols=None) -> dict:
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("invalid template name")
    src = _DASH_DIR / f"layout-{name}.json"
    if not src.exists():
        raise FileNotFoundError(f"no such template: {name}")
    try:
        current = dashboard_path(user["name"], from_layout)
        if current.exists():
            outgoing = json.loads(current.read_text(encoding="utf-8"))
            if size in ("small", "medium", "big"):
                outgoing.setdefault("view", {})["size"] = size
            if type(cols) is int and 2 <= cols <= 8:
                outgoing.setdefault("view", {})["cols"] = cols
            dashboard_save(user["name"], outgoing)
        data = json.loads(src.read_text(encoding="utf-8"))
        saved = dashboard_path(user["name"], name)
        if saved.exists():
            view = json.loads(saved.read_text(encoding="utf-8")).get("view", {})
            data.setdefault("view", {}).update({key: view[key] for key in ("size", "cols") if key in view})
    except Exception as e:
        raise RuntimeError(f"template parse failed: {e}") from e
    data["layout"] = name
    dashboard_save(user["name"], data)
    dashboard_sync_devices(user)
    return data


# CLI

CLI_SCHEMA = {"ops": {
    "run": {"params": ["--port"]},
    "open": {"params": ["--port"]},
    "stop": {},
    "status": {},
    "log": {},
}}


def main():
    ap  = argparse.ArgumentParser(prog="web")
    sub = ap.add_subparsers(dest="op")
    p   = sub.add_parser("run");    p.add_argument("--port", type=int, default=None)
    p   = sub.add_parser("open");   p.add_argument("--port", type=int, default=None)
    p   = sub.add_parser("_serve"); p.add_argument("--port", type=int, default=None)
    p.add_argument("--host", default=None)
    p.add_argument("--owner-pid", type=int, default=0, dest="owner_pid")
    sub.add_parser("stop")
    sub.add_parser("status")
    args = ap.parse_args()
    if   args.op == "run":    run(port=args.port)
    elif args.op == "open":   open_browser(args.port)
    elif args.op == "_serve": _serve(port=args.port, host=args.host, owner_pid=args.owner_pid)
    elif args.op == "stop":   stop()
    elif args.op == "status": status()
    else: ap.print_help()


def cli(argv):
    """Parse web command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="web", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("--port", type=int, default=None)
    args = p.parse_args(argv)

    a = args.action
    if   a == "run":    run(port=args.port)
    elif a == "open":   open_browser(port=args.port)
    elif a == "stop":   stop()
    elif a == "status": status()
    elif a == "log":
        import pathlib
        f = pathlib.Path(".out/events.log")
        if not f.exists(): print("  events.log not found"); return
        with f.open("r", encoding="utf-8") as fh:
            fh.seek(0, 2)
            while True:
                line = fh.readline()
                if line: print(line, end="")
                else: time.sleep(0.25)


if __name__ == "__main__":
    main()
