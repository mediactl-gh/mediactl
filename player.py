"""
player — Plex/Jellyfin players, libraries, and playback

Usage:
  player pair --player NAME
  player status [--player NAME] [--tv NAME]
  player list [--player NAME]
  player library list|add <name> <type> <path>|remove <key> --player NAME
  player sync-player [key] [--player NAME]
  player watchlist list|add <title...>|remove <key> [--player NAME] [--year N] [--type movie|tv]
  player play <title...> --player NAME [--year N]
  player play-video <title...> [--player NAME] [--year N]
  player key play|pause|stop [client_id] --player NAME

NAME is plex, jellyfin, or a registered player name. status and sync-player
default to all backends; play-video defaults to the recent/first player.

Help: help player | player --help
"""

import argparse
import json
import re
import time
from contextvars import ContextVar
from urllib.parse import urlsplit
from pathlib import Path
from logui import logui


_SERVICES_F = Path(".out") / "services.json"
_PLAYER_STATUS_F = Path(".out") / "player.json"
_PLAYER = ContextVar("mediactl_player", default=None)
_DEFAULTS = {"jellyfin": {"url": "http://127.0.0.1:8096"}}
_PLAYER_APP_IDS = {
    "plex": {"cdp-30", "com.plexapp.android", "com.plexapp.plex"},
    "jellyfin": {"org.jellyfin.androidtv", "org.jellyfin.webos", "org.jellyfin.tizen"},
}
CLI_SCHEMA = {"ops": {
    "pair": {"params": ["--player"]},
    "status": {"params": ["--player", "--tv"]},
    "list": {"params": ["--player"]},
    "library": {"args": "list|add|remove [args ...]", "params": ["--player"]},
    "sync-player": {"args": "[key]", "params": ["--player"]},
    "watchlist": {"args": "list|add <title [- year]|Plex ID>|remove <key>", "params": ["--player", "--year", "--type"]},
    "play": {"args": "title", "params": ["--year", "--player"]},
    "play-video": {"args": "title", "params": ["--year", "--player"]},
    "key": {"args": "play|pause|stop [client_id]", "params": ["--player"]},
}}


# ── Playback Ops ──────────────────


def play(title, year=None):
    title = str(title or "").strip()
    if not title:
        logui.err("player.play", error="title is required"); return
    try:
        if _PLAYER.get() is None:
            players = _registered_instances()
            if not players:
                raise ValueError("no discovered players; run player pair --player <plex|jellyfin>")
            try:
                states = json.loads(_PLAYER_STATUS_F.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                states = {}
            recent = {}
            for key, state in states.items():
                used_at = state.get("last_used_at") or (
                    state.get("updated_at", 0) if state.get("playing") or state.get("app") else 0)
                for name, info in players:
                    if key == name or (state.get("provider") == info.get("type")
                                       and (state.get("client") or state.get("last_used_client")) == info.get("name")):
                        recent[name] = max(recent.get(name, 0), used_at)
            select_player(max(players, key=lambda entry: recent.get(entry[0], 0))[0])

        return _backend().play(title, year)
    except Exception as e:
        logui.err("player.play", error=str(e), title=title)


def stop(client_id=None):
    _control("stop", client_id)


def pause(client_id=None):
    _control("pause", client_id)


def resume(client_id=None):
    _control("resume", client_id)


def libraries(counts=False):
    return _backend().libraries(counts=counts)


def lib_list():
    for lib in libraries():
        logui("player.lib", player=player_type(), key=lib["key"], title=lib["title"], type=lib["type"])


def lib_add(name, lib_type, path, refresh=True):
    # Plex's API uses "show"/"artist" for TV/music; accept "tv" alias from callers.
    if lib_type == "tv":
        lib_type = "show"
    _backend().lib_add(name, lib_type, path, refresh=refresh)
    logui("player.lib.add", title=name, type=lib_type, path=path)


def lib_remove(key, confirm=True):
    return _backend().lib_remove(key, confirm=confirm)


def lib_scan(key=None, all_players=False):
    """Refresh one library by key, or all libraries if key is None."""
    if all_players and _PLAYER.get() is None:
        for backend in _configured_backends():
            token = _PLAYER.set(backend)
            try:
                lib_scan(key)
            except Exception as exc:
                logui.err("player.lib.scan", player=backend, error=str(exc))
            finally:
                _PLAYER.reset(token)
        return
    sync_instances()
    return _backend().lib_scan(key)


def watchlist_get():
    return _backend("plex").watchlist_get()


def sync_watchlist():
    import library
    for name in _configured_backends():
        try:
            backend = _backend(name)
            items = backend.favorites()
        except Exception as exc:
            logui.err("player.sync-watchlist", player=name, error=str(exc))
            continue
        for key, item in items.items():
            try:
                library.watchlist_update(item=item)
                backend.watchlist_remove(key)
            except Exception as exc:
                logui.err("player.sync-watchlist", player=name, title=item.get("title"), error=str(exc))


def watchlist_show():
    items = _backend().favorites()
    if not items:
        logui("player.watchlist", empty=True); return
    for key, item in items.items():
        logui("player.watchlist", key=key, title=item["title"], year=item["year"], type=item["type"])


def watchlist_add(rating_key, year=None, media_type=None):
    rating_key = str(rating_key).strip()
    dated = re.fullmatch(r"(.+?)\s+-\s+(\d{4})", rating_key)
    if dated:
        rating_key, parsed_year = dated.groups()
        year = year if year is not None else int(parsed_year)
    rating_key = _backend().watchlist_add(rating_key, year, media_type)
    logui("player.watchlist.add", ratingKey=rating_key)


def watchlist_remove(rating_key):
    _backend().watchlist_remove(rating_key)
    logui("player.watchlist.remove", ratingKey=rating_key)


def pair(timeout=120):
    """Authorize access to the selected media player backend."""
    return _backend().pair(timeout)


def player_type(name=None):
    name = name or _PLAYER.get()
    info = _player_info(name)
    return info.get("type") if info else name or "plex"


def select_player(name=None):
    if name:
        name = name.lower()
        if name not in ("plex", "jellyfin") and _player_info(name) is None:
            raise ValueError(f"unknown player: {name}")
    _PLAYER.set(name)


def sync_instances():
    targets = [_PLAYER.get()] if _PLAYER.get() is not None else ["plex", "jellyfin"]
    for target in targets:
        selected = _PLAYER.set(target)
        try:
            instances = _backend().instances()
        except Exception as exc:
            logui.err("player.instances", error=str(exc))
        else:
            if instances is not None:
                _save_instances(instances)
        finally:
            _PLAYER.reset(selected)


def list_players(player=None):
    selected = (player or _PLAYER.get() or "").lower()
    instances = [(name, info) for name, info in _registered_instances(servers=True) + _registered_instances()
               if not selected or name.lower() == selected or info.get("type") == selected]
    result = []
    for name, info in instances:
        role = "server" if "server" in (info.get("capabilities") or []) else "player"
        row = {"id": name, "title": info.get("name") or name, "url": info.get("url"), "role": role}
        pairing = ""
        if role == "server":
            row["paired"] = bool(info.get("token"))
            pairing = "  paired" if row["paired"] else "  unpaired"
        result.append(row)
        print(f"  {name:<12} [{role}]  {row['title']}{pairing}")
    if not result:
        print("  no instances")
    return result


def play_video(title, year=None):
    if play(title, year):
        return True


def status(emit=True, tv_name=None, server=None, client=None, refresh_tv=True,
           all_players=False):
    if all_players and _PLAYER.get() is None:
        players = _registered_instances()
        targets = [name for name, _ in players]
        player_backends = {info.get("type") for _, info in players}
        targets.extend(backend for backend in _configured_backends()
                       if backend not in player_backends)
        result = {}
        for name in targets:
            token = _PLAYER.set(name)
            try:
                result[name] = status(emit=emit, tv_name=tv_name,
                                       server=server, client=client,
                                       refresh_tv=refresh_tv, all_players=False)
            except Exception as exc:
                result[name] = {"player": False, "playing": False,
                                "state": "stopped", "error": str(exc),
                                "updated_at": int(time.time())}
                changed = _persist_player_status(name, result[name], emit=emit)
                if emit and changed:
                    _emit_status(result[name])
            finally:
                _PLAYER.reset(token)
        if not targets and emit:
            logui("player.status", message="no configured players")
        return result

    # With no explicit --player, the TV's foreground app chooses the service.
    # A non-media app must not cause either Plex or Jellyfin to be queried.
    if tv_name and _PLAYER.get() is None:
        tv_on, app_id = _tv_status(tv_name, refresh_tv)
        selected = _player_for_app(app_id)
        if selected:
            token = _PLAYER.set(selected)
            try:
                return status(emit=emit, tv_name=tv_name, server=server,
                              client=client, refresh_tv=False)
            finally:
                _PLAYER.reset(token)
        result = {"tv": tv_name, "tv_on": tv_on, "app": False,
                  "app_id": app_id or None, "player": False,
                  "playing": False, "state": "stopped", "updated_at": int(time.time())}
        changed = _persist_player_status(tv_name, result, emit=emit)
        if emit and changed:
            _emit_status(result)
        return result

    import tv
    backend = player_type()
    tv_info = None
    if tv_name:
        try:
            _, tv_info = tv._get_device(tv_name)
        except Exception:
            pass
    else:
        match backend:
            case "plex":
                tv_name, tv_info = _backend("plex").play_target()
            case "jellyfin":
                tv_name, tv_info = _active_player_tv("jellyfin")
    tv_on, app_id = _tv_status(tv_name, refresh_tv)
    match backend:
        case "plex":
            result = _backend("plex").status(tv_name, tv_info, app_id, server, client)
        case "jellyfin":
            result = _backend("jellyfin").status(tv_name, tv_info)
        case _:
            return
    result.update(provider=backend, server=_pcfg("url", required=False), tv=tv_name,
                  tv_on=tv_on, app=_player_for_app(app_id) == backend,
                  app_id=app_id or None, updated_at=int(time.time()))
    changed = _persist_player_status(_PLAYER.get() or tv_name or backend, result, emit=emit)
    if emit and changed:
        _emit_status(result)
    return result


def cli(argv):
    """Parse player command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="player", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--player", type=str.lower,
                   help="backend (plex/jellyfin) or registered player name; required for pair, library, play, key; play-video defaults to recent/first player; status/sync-player default to all")
    p.add_argument("--tv", default=None, dest="tv_name")
    p.add_argument("--year", type=int)
    p.add_argument("--type", choices=["movie", "tv"], dest="media_type")
    args = p.parse_args(argv)

    a = args.action
    if a in {"pair", "library", "play", "key"} and not args.player:
        raise ValueError(f"player {a} requires --player <plex|jellyfin|player-name>")
    select_player(args.player)
    if   a == "pair":       pair()
    elif a == "status":     status(tv_name=args.tv_name, all_players=args.player is None)
    elif a == "list":       list_players()
    elif a == "library":
        op = args.args[0] if args.args else ""
        values = args.args[1:]
        if op == "list" and not values:             lib_list()
        elif op == "add" and len(values) == 3:      lib_add(*values)
        elif op == "remove" and len(values) == 1:   lib_remove(values[0])
        else:
            print("  usage: player library list | add <name> <type> <path> | remove <key>")
    elif a == "sync-player":
        if len(args.args) <= 1:
            lib_scan(args.args[0] if args.args else None, all_players=args.player is None)
        else: print("  usage: player sync-player [key]")
    elif a == "watchlist":
        op = args.args[0] if args.args else ""
        values = args.args[1:]
        if op == "list" and not values:            watchlist_show()
        elif op == "add" and values:              watchlist_add(" ".join(values), year=args.year, media_type=args.media_type)
        elif op == "remove" and len(values) == 1:  watchlist_remove(values[0])
        else:
            print("  usage: player watchlist list | add <title [- year] | Plex ID> | remove <key>")
    elif a == "play":      play(" ".join(args.args), year=args.year)
    elif a == "play-video": play_video(" ".join(args.args), year=args.year)
    elif a == "key":
        controls = {"play": resume, "pause": pause, "stop": stop}
        if 1 <= len(args.args) <= 2 and args.args[0] in controls:
            controls[args.args[0]](args.args[1] if len(args.args) == 2 else None)
        else:
            print("  usage: player key play|pause|stop [client_id]")


def _backend(name=None):
    import player_plex
    import player_jellyfin
    backend = name or player_type()
    match backend:
        case "plex":
            return player_plex
        case "jellyfin":
            return player_jellyfin
        case _:
            raise ValueError(f"unsupported media player backend: {backend}")


def _jelly_favorites():
    return _backend("jellyfin").favorites()


def _control(command, client_id=None):
    client_id = _backend().control(command, client_id)
    logui(f"player.{command}", client=client_id)


def _player_for_app(app_id):
    app_id = str(app_id or "").lower()
    return next((name for name, ids in _PLAYER_APP_IDS.items() if app_id in ids), None)


def _active_player_tv(backend, max_age=None):
    """Return (name, metadata) for a TV recently observed running this backend."""
    try:
        tvs = json.loads((Path(".out") / "tvs.json").read_text(encoding="utf-8"))
    except Exception:
        return None, None
    now = time.time()
    for name, info in tvs.items():
        if _player_for_app(info.get("current_app")) != backend:
            continue
        try:
            if max_age is None or now - float(info.get("current_app_at", 0)) <= max_age:
                return name, info
        except (TypeError, ValueError):
            pass
    return None, None


def _title_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _pick_title(items, title, year=None):
    wanted = _title_key(title)
    exact = [item for item in items if _title_key(
        item.get("Name") if isinstance(item, dict) else getattr(item, "title", "")) == wanted]
    if year is not None:
        year = int(year)
        dated = [item for item in exact if int(
            (item.get("ProductionYear") if isinstance(item, dict) else getattr(item, "year", 0)) or 0) == year]
        exact = dated
    return exact[0] if exact else None


def _tv_status(tv_name, refresh_tv=True):
    import tv
    tv_on, app_id = False, ""
    if tv_name:
        try:
            tv_on = tv._select_driver(tv_name).is_on()
            if tv_on:
                if refresh_tv:
                    tv.current_app(tv=tv_name, emit=False)
                _, info = tv._get_device(tv_name)
                app_id = str((info or {}).get("current_app", "")).lower()
        except Exception:
            pass
    return tv_on, app_id


def _emit_status(result):
    fields = ["client", "tv", "tv_on", "app", "player", "state",
              "title", "year", "grandparent", "season", "episode", "error"]
    if result.get("title") or result.get("duration"):
        fields.extend(["offset", "duration"])
    logui("player.status",
          target=_PLAYER.get() or result.get("tv") or result.get("provider"),
          **{k: result[k] for k in fields if k in result})


def _persist_player_status(name, state, emit=True):
    try:
        players = json.loads(_PLAYER_STATUS_F.read_text(encoding="utf-8"))
    except Exception:
        players = {}
    public = {key: value for key, value in state.items() if not key.startswith("_")}
    public.setdefault("updated_at", int(time.time()))
    if public.get("playing") or public.get("app"):
        public["last_used_at"] = public["updated_at"]
        public["last_used_client"] = public.get("client") or players.get(name, {}).get("last_used_client")
    elif players.get(name, {}).get("last_used_at"):
        public["last_used_at"] = players[name]["last_used_at"]
        public["last_used_client"] = players[name].get("last_used_client")
    ignored = {"updated_at", "last_used_at", "last_used_client"}
    before = {k: v for k, v in players.get(name, {}).items() if k not in ignored and not k.startswith("_")}
    after = {k: v for k, v in public.items() if k not in ignored}
    changed = before != after or players.get(name, {}).get("_pending_event", False)
    # Silent reads must not consume a change before the next live update.
    if changed and not emit:
        public["_pending_event"] = True
    players[name] = public
    Path(".out").mkdir(parents=True, exist_ok=True)
    _PLAYER_STATUS_F.write_text(json.dumps(players, indent=2), encoding="utf-8")
    return changed


def _refresh_player_status():
    sync_instances()
    import tv
    for name, info in tv._parse_tvs().items():
        if str(info.get("brand", "")).lower() in tv._SMART_BRANDS:
            status(tv_name=name)


# ── Config (.out/services.json: {media-center:{...}}) ──────────────────────────────


def _load_services():
    try:
        return json.loads(_SERVICES_F.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _registered_instances(servers=False):
    entries = (_load_services().get("media-center") or {}).items()
    result = []
    for name, info in entries:
        is_server = "server" in (info.get("capabilities") or [])
        if is_server == servers and (is_server or name.rsplit("-", 1)[-1].isdigit()):
            result.append((name, info))
    return result

def _configured_backends():
    return list(dict.fromkeys(info.get("type") for _, info in _registered_instances(servers=True)
                              if info.get("type") in ("plex", "jellyfin")))


def _player_info(name):
    name = str(name or "").lower()
    return next((info for key, info in _registered_instances() if key.lower() == name), None)


def _server_entry(entries, service, url=None):
    servers = [(name, info) for name, info in entries.items()
               if "server" in (info.get("capabilities") or [])]
    return (next(((name, info) for name, info in servers if url and info.get("url") == url), None)
            or next(((name, info) for name, info in servers
                     if info.get("type") == service or name == service or name.startswith(f"{service}-")),
                    (None, {})))


def _pcfg(field, required=True):
    service = player_type()
    _, server = _server_entry(_load_services().get("media-center") or {}, service)
    value = {**_DEFAULTS.get(service, {}), **server}.get(field)
    if not value and required:
        raise ValueError(f"missing {service} config: {field} — set up the now-playing tile")
    return value

def _save_cfg(service, section):
    _save_instances([{**section, "type": service, "capabilities": ["server"]}], pairing=True)


def _save_instances(instances, pairing=False):
    data = _load_services()
    entries = data.setdefault("media-center", {})
    for row in instances:
        client_id = row.get("client_id") or row.get("device_id")
        is_server = "server" in row["capabilities"]
        key = None
        if pairing:
            key, _ = _server_entry(entries, row["type"], row.get("url"))
        elif is_server and row.get("url"):
            host = urlsplit(row["url"]).hostname or ""
            key = next((name for name, current in entries.items()
                        if "server" in (current.get("capabilities") or []) and
                        current.get("type") == row["type"] and
                        ((current.get("url") == row["url"]) or
                         (current.get("source") and
                          (host == current["source"] or
                           host.startswith(current["source"].replace(".", "-") + "."))))), None)
        if not key and not pairing:
            key = next((name for name, current in entries.items()
                        if current.get("type") == row["type"] and
                        (current.get("client_id") == client_id or
                         current.get("clientIdentifier") == client_id or
                         current.get("device_id") == client_id)), None)
        if not key:
            index = 0
            while f"{row['type']}-{index}" in entries:
                index += 1
            key = f"{row['type']}-{index}"
        current = entries.get(key, {})
        merged = {**current, **row}
        if is_server:
            for field in (("url",) if pairing else ("url", "source")):
                if current.get(field):
                    merged[field] = current[field]
        entries[key] = merged
        if is_server and not pairing:
            for duplicate in list(entries):
                if duplicate != key and (
                        entries[duplicate].get("client_id") == client_id or
                        entries[duplicate].get("clientIdentifier") == client_id):
                    del entries[duplicate]

    Path(".out").mkdir(parents=True, exist_ok=True)
    _SERVICES_F.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if not pairing:
        logui("player.instances", count=len(instances))
