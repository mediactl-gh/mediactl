"""
torrent — qBittorrent operations

Usage:
  torrent list [--filter NAME]
  torrent add <url|magnet> [--path PATH] [--type TYPE] [--title TITLE]
              [--year YEAR] [--lang LANG] [--genres LIST] [--label LABEL]
  torrent pause <hash>
  torrent resume <hash>
  torrent delete <hash> [--files]
  torrent status | active
  torrent listen [--interval SECONDS]

Help: help torrent | torrent --help
"""

import argparse
import json
import time
import re
import requests
from pathlib import Path
from logui import logui
import device as _device

_session = requests.Session()
_login_config = None
_TIMEOUT = 10
_ACTIVE_F = Path(".out") / "torrent.json"


# ── Auth ───────────────────────────────────────────────────────────────────────
def configured():
    return bool((_device._registered_services("torrent").get("qbittorrent", {}).get("url") or "").strip())


def _login():
    global _login_config
    config = _device._registered_services("torrent").get("qbittorrent", {})
    url = config.get("url", "").rstrip("/")
    if not url:
        raise ValueError("qBittorrent URL missing in services.json: torrent.qbittorrent.url")
    credentials = (url, config.get("username", ""), config.get("password", ""))
    if _login_config == credentials:
        return url
    _login_config = None
    _session.cookies.clear()
    resp = _session.post(f"{url}/api/v2/auth/login", data={
        "username": credentials[1],
        "password": credentials[2],
    }, timeout=_TIMEOUT)
    # qBittorrent returns 200 + "Ok." on older versions, 204 No Content on newer ones.
    if (resp.status_code == 200 and resp.text == "Ok.") or resp.status_code == 204:
        _login_config = credentials
    else:
        raise Exception(f"qBittorrent login failed: {resp.status_code} {resp.text}")
    return url

def _get(path, **kwargs):
    url = _login()
    return _session.get(f"{url}/api/v2{path}", timeout=_TIMEOUT, **kwargs)

def _post(path, **kwargs):
    url = _login()
    return _session.post(f"{url}/api/v2{path}", timeout=_TIMEOUT, **kwargs)


# ── Operations ─────────────────────────────────────────────────────────────────
def _get_all(filter=None):
    return _get("/torrents/info", params={"filter": filter or "all"}).json()


def _error_reason(error):
    if isinstance(error, requests.Timeout):
        return "qBittorrent request timed out"
    if isinstance(error, requests.ConnectionError):
        message = str(error)
        endpoint = re.search(r"host='([^']+)', port=(\d+)", message)
        address = f" ({endpoint[1]}:{endpoint[2]})" if endpoint else ""
        detail = "connection refused" if "10061" in message or "refused" in message.lower() else "connection failed"
        return f"qBittorrent {detail}{address}"
    return re.sub(r"0x[0-9a-fA-F]+", "<address>", str(error)).strip()[:240] or type(error).__name__


def _normalize(t):
    """Normalize a qBittorrent torrent dict into our portable shape."""
    progress = t.get("progress", 0)
    return {
        "hash":     t.get("hash", ""),
        "name":     t.get("name", "?"),
        "state":    t.get("state", "?"),
        "progress": round(progress * 100, 1),
        "size":     t.get("size", 0),
        "dl_speed": t.get("dlspeed", 0),
        "up_speed": t.get("upspeed", 0),
        "eta":      t.get("eta", 0),
        "seeds":    t.get("num_seeds", 0),
        "peers":    t.get("num_leechs", 0),
        "tags":     [x.strip() for x in (t.get("tags") or "").split(",") if x.strip()],
        "path":     t.get("content_path") or t.get("save_path") or "",
    }

def _resolve_hash(name_or_hash):
    """Accept a hash or a name substring — return the hash."""
    torrents = _get_all()
    for t in torrents:
        if t["hash"] == name_or_hash:
            return t["hash"], t["name"]
    matches = [t for t in torrents if name_or_hash.lower() in t["name"].lower()]
    if not matches:
        raise Exception(f"no torrent matching: {name_or_hash}")
    if len(matches) > 1:
        print("  multiple matches:")
        for t in matches:
            print(f"    {t['hash'][:8]}  {t['name']}")
        raise Exception("be more specific")
    return matches[0]["hash"], matches[0]["name"]

def torrent_list(filter=None):
    """List torrents. filter: all|downloading|seeding|completed|paused"""
    torrents = [_normalize(t) for t in _get_all(filter)]
    if not torrents:
        print("  no torrents")
    for t in torrents:
        h = t["hash"][:8]
        print(f"  [{t['state']:<12}] {t['progress']:5.1f}%  {h}  {t['name']}")
    print(f"  ({len(torrents)} torrents)")

def _snapshot():
    """Poll, persist, and report the torrent client state."""
    try:
        previous = json.loads(_ACTIVE_F.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    keys = ("name", "state", "progress", "size", "dl_speed", "eta", "seeds", "peers", "path")
    out = []
    error = None
    try:
        for t in _get_all("all"):
            n = _normalize(t)
            if n["state"] != "error":
                row = {k: n[k] for k in keys}
                tag_label = next(iter(n["tags"]), "")
                row["label"] = tag_label or n["name"]
                row["poster"] = None
                try:
                    import media
                    info = media.cached_info(tag_label) if tag_label else None
                    if info:
                        row["poster"] = info.get("poster")
                except Exception:
                    pass
                out.append(row)
    except Exception as exc:
        error = _error_reason(exc)

    state = {
        "available": error is None,
        "active": len(out),
        "dl_speed": sum(item["dl_speed"] for item in out),
        "items": out,
        "error": error,
        "updated_at": int(time.time()),
    }
    Path(".out").mkdir(parents=True, exist_ok=True)
    _ACTIVE_F.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if any(previous.get(key) != value for key, value in state.items() if key != "updated_at"):
        emit = logui if state["available"] else logui.err
        emit("torrent.status", available=state["available"], active=state["active"],
             dl_speed=state["dl_speed"], error=state["error"],
             items=json.dumps(out, separators=(",", ":")))
    return state


def active(output=True):
    """Minimal JSON for current torrent-client items shown in the library tile."""
    state = _snapshot()
    if output:
        print(json.dumps(state["items"]))
    return state["items"]


def _refresh_active():
    if configured():
        _snapshot()




def completed():
    """Finished torrents, for library import. Each: hash, name, path, category, tags."""
    return [{
        "hash":     t["hash"],
        "name":     t.get("name", ""),
        "path":     t.get("content_path") or t.get("save_path") or "",
        "category": t.get("category", ""),
        "tags":     [x.strip() for x in (t.get("tags") or "").split(",") if x.strip()],
    } for t in _get_all() if t.get("progress", 0) >= 1]

def tag(hashes, label):
    _post("/torrents/addTags", data={"hashes": hashes, "tags": label})

def _get_download_path():
    """Read the torrent client's configured download folder."""
    try:
        prefs = _get("/app/preferences").json()
        return prefs.get("save_path") or ""
    except Exception:
        return ""


def torrent_add(url_or_magnet, save_path=None, meta=None):
    """Add a torrent by URL or magnet link. If meta dict is given and save_path is None, resolves download path via library rules."""
    if save_path is None and meta:
        try:
            import library
            save_path = library.resolve_download(meta)
        except Exception:
            pass
    data = {"urls": url_or_magnet}
    if save_path:
        data["savepath"] = save_path
    if meta and meta.get("label"):
        data["tags"] = meta["label"]
    response = _post("/torrents/add", data=data)
    response.raise_for_status()
    if response.text.strip() not in ("", "Ok."):
        raise ValueError(f"qBittorrent add failed: {response.text.strip()}")
    label = url_or_magnet[:80] + ('...' if len(url_or_magnet) > 80 else '')
    print(f"  + queued: {label}")
    if save_path:
        print(f"  -> {save_path}")
    logui("torrent.add", url=url_or_magnet, name=label, status="queued", save_path=save_path or "")

def torrent_add_file(filepath, save_path=None, meta=None):
    """Add a torrent from a local .torrent file. If meta dict is given and save_path is None, resolves download path via library rules."""
    if save_path is None and meta:
        try:
            import library
            save_path = library.resolve_download(meta)
        except Exception:
            pass
    with open(filepath, "rb") as f:
        files = {"torrents": f}
        data  = {"savepath": save_path} if save_path else {}
        _post("/torrents/add", data=data, files=files)
    print(f"  + queued: {filepath}")
    if save_path:
        print(f"  -> {save_path}")
    logui("torrent.add", url="", name=filepath, status="queued", save_path=save_path or "")

def torrent_pause(name_or_hash):
    h, name = _resolve_hash(name_or_hash)
    _post("/torrents/pause", data={"hashes": h})
    print(f"  paused: {name}")

def torrent_resume(name_or_hash):
    h, name = _resolve_hash(name_or_hash)
    _post("/torrents/resume", data={"hashes": h})
    print(f"  resumed: {name}")

def torrent_delete(name_or_hash, delete_files=False):
    h, name = _resolve_hash(name_or_hash)
    confirm = input(f"  Delete '{name}' {'+ files ' if delete_files else ''}? [y/N] ").strip().lower()
    if confirm != "y": print("  aborted"); return
    _post("/torrents/delete", data={"hashes": h, "deleteFiles": str(delete_files).lower()})
    print(f"  - deleted: {name}")

def torrent_status():
    """Refresh and report torrent-client availability and active items."""
    return _snapshot()


CLI_SCHEMA = {"ops": {
    "list": {"params": ["--filter", "--json"]},
    "add": {"args": "url|magnet", "params": ["--path", "--type", "--title", "--year", "--lang", "--genres", "--label"]},
    "pause": {"args": "hash"},
    "resume": {"args": "hash"},
    "delete": {"args": "hash", "params": ["--files"]},
    "status": {},
    "listen": {"params": ["--interval"]},
    "active": {},
}}


def cli(argv):
    """Parse torrent command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="torrent", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--filter", default="all"); p.add_argument("--path")
    p.add_argument("--files", action="store_true")
    p.add_argument("--json", action="store_true", dest="json_out")
    p.add_argument("--type"); p.add_argument("--title")
    p.add_argument("--label")
    p.add_argument("--lang"); p.add_argument("--genres"); p.add_argument("--year")
    p.add_argument("--interval", type=int, default=10)
    args = p.parse_args(argv)

    a = args.action
    if   a == "list":   torrent_list(args.filter)
    elif a == "active": active()
    elif a == "add":
        meta = {}
        if args.type:  meta["type"] = args.type
        if args.title: meta["title"] = args.title
        if args.year:  meta["year"] = args.year
        if args.lang:  meta["language"] = args.lang
        if args.genres: meta["genres"] = args.genres
        if args.label: meta["label"] = args.label
        torrent_add(args.args[0], args.path, meta=meta or None)
    elif a == "listen": torrent_listen(args.interval)
    elif a == "pause":  torrent_pause(args.args[0])
    elif a == "resume": torrent_resume(args.args[0])
    elif a == "delete": torrent_delete(args.args[0], args.files)
    elif a == "status":   torrent_status()
