"""
torrent — search and qBittorrent operations

Usage:
  torrent search <query...> [--top N] [--indexer NAME] [--category NAME]
  torrent add <id|exact-title|url|magnet> [--path PATH] [--type movie|tv] [--title TITLE]
              [--year YEAR] [--label LABEL]
  torrent pause <id>
  torrent resume <id>
  torrent delete <id> [--files]
  torrent status

Use the full torrent ID from torrent search, or quote an exact release title.
Search shows 5 results by default; override with --top N.
Use --title for media lookup, or --label for a custom tag.
"""

import argparse
import json
import time
import re
import requests
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
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
    import indexer
    import media
    meta = dict(meta or {})
    identity = {} if not meta.get("label") else None
    url_or_magnet = indexer.resolve(url_or_magnet, metadata=identity)
    if identity is not None:
        identity.update({k: v for k, v in meta.items() if v})
        if identity.get("quality"):
            meta["quality"] = identity["quality"]
        title, year = identity.get("title"), identity.get("year")
        imdb = str(identity.get("imdb_id") or "")
        if imdb.isdigit() and int(imdb):
            imdb = "tt" + imdb.zfill(7)
        try:
            info = media.cached_info(f"{title} - {year}" if year else title) if title else None
            if not info and re.fullmatch(r"tt\d+", imdb):
                info = media.lookup(imdb, media_type=identity.get("type") or "movie", source="cinemeta")
            elif not info and title:
                info = media.lookup(title, media_type=identity.get("type") or "both")
            if info and (not year or str(info.get("year")) == str(year)):
                meta = {**meta, **info}
                meta["label"] = info["title"] + (f" - {info['year']}" if info.get("year") else "")
        except Exception as exc:
            print(f"  Media lookup failed: {exc}")
        if not meta.get("label"):
            print("  Media identity unresolved; adding without a normalized title tag")
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
    body = response.text.strip()
    status = "queued"
    if body not in ("", "Ok."):
        try:
            result = json.loads(body)
        except ValueError:
            raise ValueError("qBittorrent rejected the torrent") from None
        if not isinstance(result, dict):
            raise ValueError("qBittorrent returned an unrecognized add response")
        added, pending, failed = (result.get(key, 0) for key in
                                  ("success_count", "pending_count", "failure_count"))
        if failed or not (added or pending):
            raise ValueError(f"qBittorrent add incomplete: {added} added, {pending} pending, {failed} failed")
        status = "added" if added else "pending"
        if added and pending:
            status = f"{added} added, {pending} pending"
    label = (meta.get("label") or parse_qs(urlsplit(url_or_magnet).query).get("dn", [""])[0]
             or indexer.result_id({"magnetUrl": url_or_magnet}) or url_or_magnet)
    print(f"  + {status}: {label}")
    if save_path:
        print(f"  -> {save_path}")
    logui("torrent.add", id=indexer._info_hash(indexer.result_id({"magnetUrl": url_or_magnet})) or "?",
          name=label, genres=", ".join(meta.get("genres") or []) or "?",
          quality=meta.get("quality") or "?", status=status, save_path=save_path or "")

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
    try:
        torrents = _get_all()
        count = len(torrents)
        logui.print(f"  qBittorrent: connected ({count} torrent{'s' if count != 1 else ''})")
    except Exception as exc:
        torrents = []
        logui.print(f"  qBittorrent: unavailable — {_error_reason(exc)}")
    for t in torrents:
        logui.print(f"    {t.get('progress', 0) * 100:5.1f}%  "
                    f"{t.get('dlspeed', 0) / 1024:8,.0f} KiB/s  {t.get('name', '?')}")
    try:
        recent = json.loads(Path(".out/media/recently_added.json").read_text(encoding="utf-8"))["items"]
    except FileNotFoundError:
        recent = []
    logui.print(f"\n  Recently added: {len(recent)}")
    for item in recent:
        logui.print(f"    {item['name']}")


CLI_SCHEMA = {"ops": {
    "search": {"args": "query", "params": ["--top", "--indexer", "--category"]},
    "add": {"args": "id|exact-title|url|magnet", "params": ["--path", "--type", "--title", "--year", "--label"]},
    "pause": {"args": "id"},
    "resume": {"args": "id"},
    "delete": {"args": "id", "params": ["--files"]},
    "status": {},
}}


def cli(argv):
    """Parse torrent command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="torrent", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--path")
    p.add_argument("--files", action="store_true")
    p.add_argument("--type", choices=["movie", "tv"]); p.add_argument("--title")
    p.add_argument("--label")
    p.add_argument("--year")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--indexer"); p.add_argument("--category")
    args = p.parse_args(argv)

    a = args.action
    if a == "search":
        if not args.args:
            raise ValueError("query required")
        if args.top < 1:
            raise ValueError("--top must be a positive integer")
        import indexer
        indexer.search_display(" ".join(args.args), indexer_id=args.indexer,
                               categories=args.category, top=args.top)
    elif a == "add":
        if not args.args:
            raise ValueError("torrent ID, exact title, URL or magnet required")
        meta = {}
        if args.type:  meta["type"] = args.type
        if args.title: meta["title"] = args.title
        if args.year:  meta["year"] = args.year
        if args.label: meta["label"] = args.label
        torrent_add(" ".join(args.args), args.path, meta=meta or None)
    elif a == "pause":  torrent_pause(args.args[0])
    elif a == "resume": torrent_resume(args.args[0])
    elif a == "delete": torrent_delete(args.args[0], args.files)
    elif a == "status":   torrent_status()
