"""
notify — send notifications and manage the command listener

Usage:
  notify send [message...] [--profile NAME] [--parse-mode HTML|Markdown]
              [--plain] [--photo|--attach PATH_OR_URL] [--buttons JSON]
  notify listen [--port N]
  notify listen-stop
  notify status

send requires a message or photo. --plain disables parse mode.
--buttons takes an inline keyboard JSON array.

Help: help notify | notify --help
"""

import argparse
import os
import io
import json
import re
import shlex
import threading
import time
import contextlib
from datetime import datetime
from pathlib import Path
import httpx
from typing import Any, Optional
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"), interpolate=False)

STATE_FILE = Path(".out/notify.json")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
BOT = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
PID_FILE   = Path(".out/notify.pid")
RUNTIME_FILE = Path(".out/notify_runtime.json")
WEBHOOK_PATH = "/telegram/callback"
DEFAULT_LISTEN_PORT = 7001

DEFAULT_PROFILE = "default"
_CHAT_TYPES = frozenset(("private", "group", "supergroup", "channel"))

# Minimum seconds between outbound `send()` calls per profile (security burst control). 0 = off.
SEND_COOLDOWN_SEC = 10
_send_lock = threading.Lock()
_send_last_at: dict[str, float] = {}
_drafts: dict[tuple, dict] = {}
_draft_lock = threading.Lock()
_startup_thread = None
_startup_lock = threading.Lock()
_client = None
_client_lock = threading.Lock()
_GROUP_ICONS = {"MEDIA": "🎬", "DEVICE": "📱", "SECURITY": "🛡", "SYSTEM": "⚙️"}

# Mobile / rich-text often inserts U+2014 EM DASH instead of two ASCII hyphens.
_EM_DASH = "\u2014"
_EN_DASH = "\u2013"


def _normalize_typographic_dashes(line: str) -> str:
    """Turn —foo / ––foo (typographic) into --foo for argparse."""
    s = line
    s = re.sub(rf"(^|[\s]){_EM_DASH}(?=[\w-])", r"\1--", s)
    s = re.sub(rf"(^|[\s]){_EN_DASH}{{2}}(?=[\w-])", r"\1--", s)
    return s


# --- State ---


def _load_state() -> dict:
    if STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text())
        raw.setdefault("chats", {})
        return raw
    return {"chats": {}}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state.pop("bot_token", None)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_chat_id(profile: str = DEFAULT_PROFILE) -> int:
    profile = profile or DEFAULT_PROFILE
    state = _load_state()
    chats: dict[str, Any] = state.get("chats") or {}
    if profile in chats:
        return int(chats[profile])

    print(f"[notify] Waiting for first message in channel (profile={profile!r})...")

    # Delete webhook to avoid 409 Conflict with getUpdates
    try:
        _delete_webhook()
    except Exception:
        pass

    while True:
        resp = httpx.get(
            f"{BOT}/getUpdates",
            params={
                "timeout": 30,
                "allowed_updates": json.dumps(["message", "channel_post", "my_chat_member"]),
            },
            timeout=40.0,
        )
        resp.raise_for_status()

        for update in resp.json().get("result", []):
            chat = (
                update.get("my_chat_member", {}).get("chat")
                or update.get("message", {}).get("chat")
                or update.get("channel_post", {}).get("chat")
            )
            if chat and chat.get("type") in _CHAT_TYPES:
                cid = chat["id"]
                state.setdefault("chats", {})
                state["chats"][profile] = cid
                _save_state(state)
                print(f"[notify] Chat ID {cid} saved for profile {profile!r}.")
                return cid


# --- Send ---


def send(
    text: str,
    parse_mode: Optional[str] = "HTML",
    photo: Optional[str] = None,
    attach: Optional[str] = None,
    buttons: Optional[list[list[dict]]] = None,
    profile: str = DEFAULT_PROFILE,
    bypass_cooldown: bool = False,
) -> dict:
    """Send Telegram message. `attach` is an alias for `photo` (local path, URL, or file_id)."""
    photo = photo or attach
    profile = profile or DEFAULT_PROFILE

    # Reserve cooldown slot before HTTP — avoids many parallel threads all passing the gate.
    if SEND_COOLDOWN_SEC > 0 and not bypass_cooldown:
        now = time.time()
        with _send_lock:
            last = _send_last_at.get(profile, 0.0)
            if now - last < SEND_COOLDOWN_SEC:
                print(f"[notify] send cooldown skip profile={profile!r} ({SEND_COOLDOWN_SEC:g}s)")
                return {"ok": True, "mediactl_notify": "cooldown"}
            _send_last_at[profile] = now

    chat_id = get_chat_id(profile)

    with httpx.Client(timeout=60.0) as client:
        if photo:
            data: dict = {"chat_id": str(chat_id), "caption": text}
            if parse_mode is not None:
                data["parse_mode"] = parse_mode
            if buttons:
                data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
            local = Path(photo) if not str(photo).startswith("http") else None
            if local and local.exists():
                with open(local, "rb") as f:
                    resp = client.post(f"{BOT}/sendPhoto", data=data,
                                       files={"photo": (local.name, f, "image/jpeg")})
            else:
                resp = client.post(f"{BOT}/sendPhoto", data={**data, "photo": photo})
        else:
            payload: dict = {"chat_id": chat_id, "text": text}
            if parse_mode is not None:
                payload["parse_mode"] = parse_mode
            if buttons:
                payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
            resp = client.post(f"{BOT}/sendMessage", json=payload)

    resp.raise_for_status()
    return resp.json()


# --- Webhook ---


def _drain_updates():
    """Ack any pending updates so Telegram doesn't re-deliver them after webhook is set."""
    # delete webhook first — getUpdates conflicts with an active webhook
    try:
        _delete_webhook()
    except Exception:
        pass
    resp = httpx.get(
        f"{BOT}/getUpdates",
        params={"offset": -1, "allowed_updates": json.dumps(["message", "channel_post", "my_chat_member"])},
        timeout=30.0,
    )
    resp.raise_for_status()
    results = resp.json().get("result", [])
    if results:
        last_id = results[-1]["update_id"]
        httpx.get(f"{BOT}/getUpdates", params={"offset": last_id + 1}, timeout=30.0)


def setup_webhook(public_url: str) -> dict:
    url = f"{public_url.rstrip('/')}{WEBHOOK_PATH}"
    result = _telegram("setWebhook", url=url,
                       allowed_updates=["message", "channel_post", "my_chat_member", "callback_query"])
    return {"ok": True, "result": result}


def _delete_webhook() -> dict:
    return {"ok": True, "result": _telegram("deleteWebhook")}


def get_webhook_info() -> dict:
    """Get current webhook registration status from Telegram."""
    return _telegram("getWebhookInfo")


def _listen_main(port: int):
    """Runs in a detached subprocess: sets up ngrok + webhook, serves callback, then waits."""
    import signal as _signal
    import threading

    http_port = port
    token = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    tunnel = None
    if not token:
        print("[notify] listener not started — set NGROK_AUTHTOKEN")
        return

    def _cleanup(sig=None, frame=None):
        print(f"[notify] shutting down")
        _delete_webhook()
        if tunnel:
            ngrok.disconnect(tunnel.public_url)
        PID_FILE.unlink(missing_ok=True)
        (PID_FILE.parent / "notify_tunnel.txt").unlink(missing_ok=True)
        raise SystemExit(0)

    _signal.signal(_signal.SIGTERM, _cleanup)

    try:
        # Create minimal FastAPI app for callback
        from fastapi import BackgroundTasks, FastAPI, Request
        from fastapi.responses import JSONResponse
        import uvicorn

        app = FastAPI()

        @app.post("/telegram/callback")
        async def telegram_callback(request: Request, background_tasks: BackgroundTasks):
            update = await request.json()
            background_tasks.add_task(dispatch, update)
            return JSONResponse(webhook_reply(update))

        # Run server in daemon thread
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=http_port, log_level="error"))
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        while not server.started:
            if not server_thread.is_alive():
                raise RuntimeError(f"listener failed to bind port {http_port}")
            time.sleep(0.05)

        try:
            from pyngrok import ngrok, conf
        except ImportError:
            raise RuntimeError("pyngrok not installed. Run: pip install pyngrok")
        conf.get_default().auth_token = token

        try:
            tunnel = ngrok.connect(f"http://localhost:{http_port}")
            host = tunnel.public_url
            print(f"[notify] ngrok tunnel: {host} -> localhost:{http_port}")
        except Exception as e:
            if "ERR_NGROK_334" in str(e) or "already online" in str(e):
                print(f"[notify] ERROR: ngrok tunnel conflict - previous tunnel still active")
                print(f"[notify] Wait 2-3 minutes or run: notify listen-stop")
                raise SystemExit(1)
            raise

        _drain_updates()
        setup_webhook(host)
        print(f"[notify] webhook registered: {host}{WEBHOOK_PATH}")

        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))

        # Store tunnel URL for status display
        tunnel_file = PID_FILE.parent / "notify_tunnel.txt"
        tunnel_file.write_text(tunnel.public_url, encoding="utf-8")

        RUNTIME_FILE.write_text(json.dumps({
            "pid": os.getpid(), "port": http_port, "state": "ready",
            "source_mtime": Path(__file__).stat().st_mtime_ns,
            "webhook": f"{host.rstrip('/')}{WEBHOOK_PATH}",
        }), encoding="utf-8")
        _publish_menu()
        threading.Event().wait()
    except KeyboardInterrupt:
        _cleanup()


def _listener_running():
    import subprocess, sys

    try:
        pid = int(PID_FILE.read_text().strip())
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                return ctypes.get_last_error() == 5
            try:
                return kernel.WaitForSingleObject(handle, 0) == 258
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _listener_state():
    try:
        return json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def status():
    running = _listener_running()
    state = _listener_state()
    current = state.get("source_mtime") == Path(__file__).stat().st_mtime_ns
    print(f"[notify] listener: {state.get('state', 'running') if running else 'stopped'}")
    if running:
        print(f"  pid: {PID_FILE.read_text().strip()}")
        print(f"  code: {'current' if current else 'outdated — run notify listen to reload'}")
    if state.get("port"):
        print(f"  port: {state['port']}")
    if state.get("webhook"):
        print(f"  webhook: {state['webhook']}")
    print(f"  log: {(PID_FILE.parent / 'notify.log').resolve()}")


def start():
    global _startup_thread
    with _startup_lock:
        if _startup_thread and _startup_thread.is_alive():
            return
        _startup_thread = threading.Thread(target=_discover, daemon=True)
        _startup_thread.start()


def listen(port: int = None):
    """Start notify listener as a background process."""
    import subprocess, sys
    http_port = int(os.environ.get("NOTIFY_PORT") or DEFAULT_LISTEN_PORT) if port is None else port
    if _listener_running():
        state = _listener_state()
        if (state.get("source_mtime") == Path(__file__).stat().st_mtime_ns
                and state.get("port") == http_port):
            print("[notify] listener already running")
            return
        print("[notify] reloading listener configuration")
        listen_stop()
    if not BOT:
        print("[notify] listener not started — bot token is not configured")
        return
    if not os.environ.get("NGROK_AUTHTOKEN", "").strip():
        print("[notify] listener not started — set NGROK_AUTHTOKEN")
        return
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    with (PID_FILE.parent / "notify.log").open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), "_listen", str(http_port)],
            stdout=log, stderr=log, **kwargs,
        )
    PID_FILE.write_text(str(proc.pid))
    RUNTIME_FILE.write_text(json.dumps({
        "pid": proc.pid, "port": http_port, "state": "starting",
        "source_mtime": Path(__file__).stat().st_mtime_ns,
    }), encoding="utf-8")
    print(f"[notify] listener started pid={proc.pid}")


def listen_stop():
    """Stop the background notify listener."""
    import subprocess, sys
    if _startup_thread and _startup_thread is not threading.current_thread():
        _startup_thread.join()
    if not PID_FILE.exists():
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        if pid <= 0:
            raise ValueError("invalid listener PID")
        try:
            if sys.platform == "win32":
                subprocess.call(["taskkill", "/F", "/T", "/PID", str(pid)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import signal as _signal
                os.kill(pid, _signal.SIGKILL)
        except OSError:
            pass
        PID_FILE.unlink(missing_ok=True)
        RUNTIME_FILE.unlink(missing_ok=True)
        (PID_FILE.parent / "notify_tunnel.txt").unlink(missing_ok=True)
        print(f"[notify] stopped pid={pid}")
    except (ValueError, OSError) as e:
        PID_FILE.unlink(missing_ok=True)
        print(f"[notify] stop failed: {e}")


# --- Dispatch ---


def is_allowed(chat_id: int) -> bool:
    chats = _load_state().get("chats", {})
    return chat_id in {int(v) for v in chats.values()}


def _snapshot_written_since(t0: float) -> Optional[Path]:
    """Newest .jpg in .out/snapshots/ written at or after t0 (clock skew slack)."""
    d = Path(".out/snapshots")
    if not d.is_dir():
        return None
    best: Optional[Path] = None
    best_m = 0.0
    for p in d.glob("*.jpg"):
        m = p.stat().st_mtime
        if m >= t0 - 0.5 and m >= best_m:
            best, best_m = p, m
    return best


def webhook_reply(update: dict):
    query = update.get("callback_query")
    if query:
        return {"method": "answerCallbackQuery", "callback_query_id": query["id"]}
    return {"ok": True}


def _telegram(method: str, **payload):
    global _client
    try:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(timeout=15.0, transport=httpx.HTTPTransport(retries=1))
        response = _client.post(f"{BOT}/{method}", json=payload)
        data = response.json()
    except (httpx.HTTPError, ValueError) as ex:
        raise RuntimeError(f"{method}: Telegram connection failed ({type(ex).__name__})") from None
    if response.is_error or not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description', 'Telegram request failed')}")
    return data.get("result")


def _publish_menu():
    try:
        _telegram("setMyCommands", commands=[{"command": "menu", "description": "Build a command"}])
        _telegram("setChatMenuButton", menu_button={"type": "commands"})
    except Exception as ex:
        print(f"[notify] menu setup failed: {ex}")
    for chat_id in set(_load_state().get("chats", {}).values()):
        try:
            result = _reply_chat(int(chat_id), "Mediactl is ready. Choose a group below.")
            if result:
                print(f"[notify] menu published message_id={result['message_id']}")
        except Exception as ex:
            print(f"[notify] menu failed: {ex}")


def _menu_groups():
    import mediactl

    groups = {}
    group = None
    for line in (mediactl.__doc__ or "").splitlines():
        name = line.strip()
        if name in _GROUP_ICONS:
            group = name
            groups[group] = []
        elif group and name and name.split()[0] in mediactl.CLI_MODULES:
            groups[group].append(name.split()[0])
    return groups


def _group_label(group):
    return f"{_GROUP_ICONS[group]} {group.title()}"


def _menu_keyboard(chat_id):
    buttons = [{"text": _group_label(group), "callback_data": f"group:{group}"}
               for group in _menu_groups()]
    if chat_id < 0:
        return {"inline_keyboard": [buttons[i:i + 2] for i in range(0, len(buttons), 2)]}
    buttons = [{"text": button["text"]} for button in buttons]
    return {"keyboard": [buttons[i:i + 2] for i in range(0, len(buttons), 2)],
            "resize_keyboard": True, "is_persistent": True}


def _show_draft(chat_id: int, draft: dict):
    import mediactl

    tokens = draft["tokens"]
    groups = _menu_groups()
    group = draft.get("group")
    text = "/" + shlex.join(tokens) if tokens else "Choose a group."
    if not tokens:
        choices = groups[group] if group in groups else list(groups)
        if group in groups:
            text = _group_label(group) + " — choose a module."
    else:
        module = mediactl.cli_module(tokens[0])
        ops = module.CLI_SCHEMA["ops"]
        if len(tokens) == 1:
            text += "\n\n" + (module.__doc__ or "Choose an operation.").strip()
            choices = list(ops)
        else:
            op = ops[tokens[1]]
            text += "\n\nArguments: " + (op.get("args") or "none")
            text += "\nReply with values or arguments; quote values containing spaces."
            text += "\nTap an option to append it, or Run to execute."
            choices = list(op.get("params", []))
    draft["version"] = str(time.time_ns())
    draft["choices"] = choices + (["Run"] if len(tokens) >= 2 else []) + (["Clear"] if tokens else [])
    buttons = [
        {"text": _group_label(choice) if not tokens and not group else choice,
         "callback_data": (f"cli:{draft['version']}:{i}" if tokens else
                           f"module:{choice}" if group else f"group:{choice}")}
        for i, choice in enumerate(draft["choices"])
    ]
    columns = 3 if tokens or group else 2
    rows = [buttons[i:min(i + columns, len(choices))] for i in range(0, len(choices), columns)]
    if len(buttons) > len(choices):
        rows.append(buttons[len(choices):])
    if group and not tokens:
        rows.append([{"text": "‹ Groups", "callback_data": "home"}])
    payload = {"chat_id": chat_id, "text": text[:4096],
               "reply_markup": {"inline_keyboard": rows}}
    if draft.get("message_id"):
        _telegram("editMessageText", message_id=draft["message_id"], **payload)
    else:
        result = _telegram("sendMessage", **payload)
        draft["message_id"] = result["message_id"]


def _compose(update: dict):
    import mediactl

    query = update.get("callback_query")
    msg = (query.get("message") if query else
           update.get("message") or update.get("channel_post")) or {}
    chat_id = msg.get("chat", {}).get("id")
    if not chat_id:
        return
    sender = (query or msg).get("from", {}).get("id")
    if msg.get("chat", {}).get("type") == "channel":
        sender = None
    key = (chat_id, sender)
    with _draft_lock:
        draft = _drafts.get(key)
        try:
            if query:
                parts = query.get("data", "").split(":")
                if parts == ["home"] or (len(parts) == 2 and parts[0] == "group" and parts[1] in _menu_groups()):
                    draft = {"tokens": [], "message_id": msg["message_id"]}
                    if parts[0] == "group":
                        draft["group"] = parts[1]
                    _drafts[key] = draft
                    _show_draft(chat_id, draft)
                    return
                if len(parts) == 2 and parts[0] == "module" and parts[1] in mediactl.CLI_MODULES:
                    draft = {"tokens": [parts[1]], "message_id": msg["message_id"]}
                    _drafts[key] = draft
                    _show_draft(chat_id, draft)
                    return
                valid = (draft and len(parts) == 3 and parts[0] == "cli"
                         and parts[1] == draft.get("version")
                         and msg.get("message_id") == draft.get("message_id")
                         and parts[2].isdigit()
                         and int(parts[2]) < len(draft["choices"]))
                if not valid:
                    draft = {"tokens": []}
                    _drafts[key] = draft
                    _show_draft(chat_id, draft)
                    return
                choice = draft["choices"][int(parts[2])]
                if choice == "Run":
                    line = shlex.join(draft["tokens"])
                    del _drafts[key]
                    _telegram("editMessageText", chat_id=chat_id,
                              message_id=draft["message_id"], text="/" + line,
                              reply_markup={"inline_keyboard": []})
                    return chat_id, line
                if choice == "Clear":
                    draft["tokens"] = []
                    draft.pop("group", None)
                else:
                    draft["tokens"].append(choice)
            else:
                text = (msg.get("text") or "").strip()
                if not text:
                    _drafts.pop(key, None)
                    _show_draft(chat_id, {"tokens": []})
                    return
                selected_group = next((group for group in _menu_groups()
                                       if text == _group_label(group)), None)
                if selected_group:
                    draft = {"tokens": [], "group": selected_group}
                    _drafts[key] = draft
                    _show_draft(chat_id, draft)
                    return
                line = _normalize_typographic_dashes(text.lstrip("/").strip())
                words = shlex.split(line)
                if not words:
                    _drafts.pop(key, None)
                    _show_draft(chat_id, {"tokens": []})
                    return
                words[0] = words[0].split("@", 1)[0].casefold()
                line = shlex.join(words)
                module = None
                if len(words) == 1 and words[0] in mediactl.CLI_MODULES:
                    module = words[0]
                elif len(words) == 2 and words[0] == "help" and words[1] in mediactl.CLI_MODULES:
                    module = words[1]
                if (draft and len(draft["tokens"]) >= 2 and not text.startswith("/")
                        and (msg.get("chat", {}).get("type") in ("private", "channel")
                             or msg.get("reply_to_message", {}).get("message_id") == draft.get("message_id"))):
                    draft["tokens"].extend(shlex.split(_normalize_typographic_dashes(text)))
                elif line in ("menu", "start", "help") or module:
                    draft = {"tokens": [module] if module else []}
                    _drafts[key] = draft
                elif words[0] in (*mediactl.CLI_MODULES, "run", "help", "exit", "quit", "--schema", "--help", "-h"):
                    return chat_id, line
                else:
                    draft = {"tokens": []}
                    _drafts[key] = draft
            _show_draft(chat_id, draft)
        except (Exception, SystemExit) as ex:
            _reply_chat(chat_id, f"ERROR: {ex}")


def _reply_chat(chat_id: int, text: str) -> None:
    if len(text) > 4096:
        text = text[:4093] + "..."
    try:
        return _telegram("sendMessage", chat_id=chat_id, text=text, reply_markup=_menu_keyboard(chat_id))
    except Exception as ex:
        print(f"[notify] reply failed: {ex}")


def _reply_chat_photo(chat_id: int, path: Path, caption: Optional[str] = None) -> None:
    """Send local JPEG as the reply (no path spam; optional short caption)."""
    try:
        p = path.resolve()
        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        with httpx.Client(timeout=120.0) as client:
            with open(p, "rb") as f:
                r = client.post(
                    f"{BOT}/sendPhoto",
                    data=data,
                    files={"photo": (p.name, f, "image/jpeg")},
                )
        r.raise_for_status()
    except Exception as ex:
        print(f"[notify] reply photo failed: {ex}")
        _reply_chat(chat_id, str(path))


def dispatch(update: dict):
    import mediactl

    command = _compose(update)
    if command is None:
        return
    chat_id, line = command

    # TODO: enforce is_allowed when per-user access control is ready
    # if not is_allowed(chat_id): return

    print(f"[notify] remote command from {chat_id}: {line}")
    parser = mediactl.make_parser()
    try:
        tokens = shlex.split(line)
    except ValueError as e:
        _reply_chat(chat_id, f"ERROR: {e}")
        return

    if tokens:
        tokens[0] = tokens[0].casefold()

    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            args = parser.parse_args(tokens)
    except SystemExit:
        msg = buf.getvalue().strip() or "ERROR: invalid command or arguments."
        _reply_chat(chat_id, msg)
        return
    except Exception as e:
        _reply_chat(chat_id, f"ERROR: {e}")
        return

    if not args.cmd:
        _reply_chat(chat_id, "Unknown command. Type 'help'.")
        return

    run_out = io.StringIO()
    run_err = io.StringIO()

    try:
        with contextlib.redirect_stdout(run_out), contextlib.redirect_stderr(run_err):
            result = mediactl.dispatch(args)
    except (Exception, SystemExit) as e:
        _reply_chat(chat_id, f"ERROR: {e}")
        return

    combined = run_out.getvalue() + run_err.getvalue()

    if args.cmd == "camera" and isinstance(result, (str, Path)) and Path(result).is_file():
        _reply_chat_photo(chat_id, Path(result))
    else:
        msg = combined.strip() or "OK"
        _reply_chat(chat_id, msg if len(msg) <= 4096 else msg[:4093] + "...")


def _discover():
    global BOT_TOKEN, BOT
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        print("[notify] disabled — set TELEGRAM_BOT_TOKEN to enable")
        return

    # Validate token
    BOT_TOKEN = bot_token
    BOT = f"https://api.telegram.org/bot{BOT_TOKEN}"
    try:
        bot_info = _telegram("getMe")
    except Exception as e:
        print(f"[notify] bot token validation failed: {e}")
        return

    # Load or create state
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {"chats": {}}
    except Exception:
        state = {"chats": {}}

    # Store bot metadata
    state.pop("bot_token", None)
    state["bot"] = {"id": bot_info.get("id"), "username": bot_info.get("username")}

    # Check if chat_id is configured
    if "default" not in state.get("chats", {}):
        print(f"[notify] bot ready: @{bot_info.get('username')}")
        print(f"[notify] chat_id not set — send any message to @{bot_info.get('username')} to discover")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    listen()


# --- Entrypoint for subprocess ---


CLI_SCHEMA = {"ops": {
    "send": {"args": "message", "params": ["--profile", "--parse-mode", "--plain", "--photo", "--attach", "--buttons"]},
    "listen": {"params": ["--port"]},
    "listen-stop": {},
    "status": {},
}}


def cli(argv):
    """Parse notify command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="notify", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"], nargs="?")
    p.add_argument("args", nargs="*")
    p.add_argument("--profile", default="default", dest="notify_profile")
    p.add_argument("--parse-mode", default="HTML", dest="notify_parse_mode")
    p.add_argument("--plain", action="store_true", dest="notify_plain",
                   help="send without parse_mode")
    p.add_argument("--photo", "--attach", dest="notify_photo", default=None)
    p.add_argument("--buttons", default=None, dest="notify_buttons",
                   help='inline keyboard JSON, e.g. [[{"text":"x","url":"https://..."}]]')
    p.add_argument("--port", type=int, default=None, dest="notify_port")
    args = p.parse_args(argv)

    a = args.action
    if a == "status":
        status()
    elif a == "listen":
        try:
            listen(port=args.notify_port)
        except Exception as e:
            print(f"  ERROR: {e}")
    elif a == "listen-stop":
        try:
            listen_stop()
        except Exception as e:
            print(f"  ERROR: {e}")
    else:
        msg = " ".join(args.args)
        photo = args.notify_photo
        if not msg and not photo:
            print("  usage: notify send <message> [--profile NAME] [--parse-mode HTML|Markdown|...]")
            print("              [--plain] [--photo|--attach PATH_OR_URL] [--buttons JSON]")
            print("         notify listen [--port N]")
            print("         notify listen-stop")
            print("         notify status")
            return
        try:
            buttons = json.loads(args.notify_buttons) if args.notify_buttons else None
        except json.JSONDecodeError as e:
            print(f"  ERROR: --buttons invalid JSON: {e}")
            return
        parse_mode = None if args.notify_plain else args.notify_parse_mode
        try:
            send(
                msg,
                profile=args.notify_profile,
                parse_mode=parse_mode,
                photo=photo,
                buttons=buttons,
            )
        except Exception as e:
            print(f"  ERROR: {e}")


# Entrypoint for the webhook listener subprocess.
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "_listen":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("NOTIFY_PORT") or DEFAULT_LISTEN_PORT)
        _listen_main(port)
