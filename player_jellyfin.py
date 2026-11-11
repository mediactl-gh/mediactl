import json
import re
import time
import requests
from pathlib import Path
from logui import logui
from player import (
    _PLAYER, _active_player_tv, _emit_status,
    _pick_title, _player_info, _save_cfg, _title_key, status as _status,
    sync_instances,
)


_CLIENT_ID = "mediactl"
_STATUS_TIMEOUT = 3


def _pcfg(field, required=True):
    try:
        entries = json.loads(Path(".out/services.json").read_text(encoding="utf-8")).get("media-center") or {}
    except Exception:
        entries = {}
    server = next((info for name, info in entries.items()
                   if "server" in (info.get("capabilities") or [])
                   and (info.get("type") == "jellyfin" or name == "jellyfin" or name.startswith("jellyfin-"))), {})
    value = {"url": "http://127.0.0.1:8096", **server}.get(field)
    if not value and required:
        raise ValueError(f"missing jellyfin config: {field} — set up the now-playing tile")
    return value


# Jellyfin providers


def play(title, year=None):
    item = _jelly_find_title(title, year)
    client_id = _jelly_client_id()
    _jreq("post", f"/Sessions/{client_id}/Playing", params={"itemIds": item["Id"], "playCommand": "PlayNow"})
    logui("player.play", event="sent", backend="jellyfin", title=item.get("Name", title),
          year=item.get("ProductionYear"), client=client_id)
    for _ in range(10):
        time.sleep(1)
        current = _status(emit=False)
        if current.get("playing") and _title_key(current.get("title")) == _title_key(item.get("Name", title)):
            _emit_status(current)
            logui("player.play-video", event="playing", title=current.get("title"),
                  client=current.get("client"))
            return True
    raise Exception("Jellyfin did not start playing the video")


def status(tv_name, tv_info):
    error = None
    configured = bool(_pcfg("token", required=False))
    try:
        data = _jreq("get", "/Sessions", timeout=(_STATUS_TIMEOUT, _STATUS_TIMEOUT)) or [] if configured else []
    except Exception as exc:
        data = []
        error = str(exc)
    sessions = [s for s in data if s.get("NowPlayingItem")]
    host = str((tv_info or {}).get("host", "")).split(":")[0]
    session = next((s for s in sessions if (
        s.get("DeviceName") == tv_name or (host and host in str(s.get("RemoteEndPoint", "")))
    )), None)
    if session is None and not tv_name and len(sessions) == 1:
        session = sessions[0]
    try:
        target = (_player_info(_PLAYER.get()) or {}) if configured else {}
        device_id = target.get("device_id")
        clients = [s for s in data if s.get("SupportsRemoteControl") is True or
                   (device_id and s.get("DeviceId") == device_id)]
    except Exception as exc:
        clients = []
        error = str(exc)
    player = next((c for c in clients if (
        c.get("DeviceName", "?") == tv_name or (host and host in str(c.get("RemoteEndPoint", "")))
    )), None)
    if player is None and not tv_name and len(clients) == 1:
        player = clients[0]
    playback = (session or {}).get("PlayState") or {}
    state = "stopped"
    if session:
        state = "paused" if playback.get("IsPaused") else "playing"
    result = {
        "player": bool(player),
        "client": player.get("DeviceName", "?") if player else None,
        "client_id": player.get("Id") if player else None,
        "playing": state == "playing",
        "state": state,
        "error": error if configured else "Jellyfin is not configured",
    }
    if session:
        item = session["NowPlayingItem"]
        result.update({
            "id": session.get("Id"), "client": session.get("DeviceName"),
            "address": session.get("RemoteEndPoint"), "user": session.get("UserName"),
            "title": item.get("Name", "?"), "type": (item.get("Type") or "").lower(),
            "year": item.get("ProductionYear"),
            "offset": int((playback.get("PositionTicks") or 0) // 10_000_000),
            "duration": int((item.get("RunTimeTicks") or 0) // 10_000_000),
            "grandparent": item.get("SeriesName"), "season": item.get("ParentIndexNumber"),
            "episode": item.get("IndexNumber"),
            "thumb": f"{_pcfg('url').rstrip('/')}/Items/{item['Id']}/Images/Primary?api_key={_pcfg('token')}" if item.get("Id") else None,
        })
    return result


def _jreq(method, path, params=None, body=None, timeout=None):
    response = requests.request(method, f"{_pcfg('url').rstrip('/')}{path}",
                                params=params, json=body, timeout=timeout or (5, 15),
                                headers={"X-Emby-Token": _pcfg("token"), "Accept": "application/json"})
    response.raise_for_status()
    if method != "delete" and response.content:
        return response.json()


def favorites():
    try:
        data = _jreq("get", f"/Users/{_pcfg('user_id')}/Items",
                     params={"Filters": "IsFavorite", "Recursive": "true", "IncludeItemTypes": "Movie,Series",
                             "Fields": "ProviderIds"})
        return {it.get("Id"): {"title": it.get("Name", "?"), "year": it.get("ProductionYear", "?"),
                               "type": (it.get("Type") or "").lower(),
                               "ids": {key.lower(): value for key, value in (it.get("ProviderIds") or {}).items()}}
                for it in (data or {}).get("Items", [])}
    except Exception:
        return {}


def libraries(counts=False):
    data = _jreq("get", "/Library/VirtualFolders")
    result = []
    for v in data or []:
        lib_type = v.get("CollectionType") or "mixed"
        item = {"key": v.get("Name"), "title": v.get("Name", "?"),
                "type": {"movies": "movie", "tvshows": "tv", "music": "artist"}.get(lib_type, lib_type),
                "locations": v.get("Locations") or []}
        if counts:
            item["count"] = None
            if v.get("ItemId"):
                items = _jreq("get", "/Items", params={"parentId": v["ItemId"], "userId": _pcfg("user_id"),
                             "recursive": "false", "limit": 0, "enableTotalRecordCount": "true"})
                item["count"] = items["TotalRecordCount"]
            item["scanning"] = v.get("RefreshStatus") in {"Active", "Queued"}
        result.append(item)
    return result


def _jelly_find_title(title, year=None):
    data = _jreq("get", f"/Users/{_pcfg('user_id')}/Items", params={
        "SearchTerm": title, "Recursive": "true", "Limit": 50,
        "IncludeItemTypes": "Movie,Series,Episode", "Fields": "ProductionYear,UserData",
    })
    item = _pick_title((data or {}).get("Items", []), title, year)
    if not item:
        suffix = f" ({year})" if year else ""
        raise Exception(f"'{title}{suffix}' was not found in the Jellyfin library")
    if item.get("Type") == "Series":
        eps = _jreq("get", f"/Users/{_pcfg('user_id')}/Items", params={
            "ParentId": item["Id"], "Recursive": "true", "Limit": 100,
            "IncludeItemTypes": "Episode", "SortBy": "ParentIndexNumber,IndexNumber",
            "SortOrder": "Ascending", "Fields": "UserData",
        })
        episodes = (eps or {}).get("Items", [])
        item = next((ep for ep in episodes if not (ep.get("UserData") or {}).get("Played")), None)
        item = item or (episodes[0] if episodes else None)
        if not item:
            raise Exception(f"'{title}' has no playable episodes in Jellyfin")
    return item


def _jelly_client_id():
    tv_name, tv_info = _active_player_tv("jellyfin")
    target = _player_info(_PLAYER.get()) or {}
    sessions = [s for s in (_jreq("get", "/Sessions") or []) if s.get("Client") != "mediactl"]
    device_id = target.get("device_id")
    if device_id:
        match = next((s for s in sessions if s.get("DeviceId") == device_id), None)
        if match:
            return match.get("Id")
    host = str((tv_info or {}).get("host", "")).split(":")[0]
    if target.get("tv") == tv_name or not target.get("tv"):
        tv_sessions = [s for s in sessions if "tv" in
                       f"{s.get('Client', '')} {s.get('DeviceName', '')}".lower()]
        if len(tv_sessions) == 1:
            return tv_sessions[0].get("Id")
    for session in sessions:
        if session.get("DeviceName") == tv_name or (host and host in str(session.get("RemoteEndPoint", ""))):
            return session.get("Id")
    if not sessions:
        raise Exception("Jellyfin TV player is not available for remote control")
    if tv_name:
        raise Exception("no Jellyfin player matches the foreground TV")
    if len(sessions) == 1:
        return sessions[0].get("Id")
    raise Exception("multiple Jellyfin players are available; open Jellyfin on the target TV")


def watchlist_add(rating_key, year=None, media_type=None):
    if not re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F-]{36}", rating_key):
        rating_key = _jelly_find_title(rating_key, year)["Id"]
    _jreq("post", f"/Users/{_pcfg('user_id')}/FavoriteItems/{rating_key}")
    return rating_key


def pair(timeout=120):
    base = _pcfg("url", required=True).rstrip("/")
    auth = 'MediaBrowser Client="mediactl", Device="mediactl", DeviceId="mediactl", Version="1.0"'
    headers = {"Authorization": auth, "Accept": "application/json"}
    try:
        enabled = requests.get(f"{base}/QuickConnect/Enabled", headers=headers,
                               timeout=(5, 15))
        enabled.raise_for_status()
        if not enabled.json():
            logui.err("player.pair", backend="jellyfin", linked=False,
                      reason="quick-connect-disabled"); return
        r = requests.post(f"{base}/QuickConnect/Initiate", headers=headers,
                          json={}, timeout=(5, 15))
        r.raise_for_status()
        request = r.json()
        secret, code = request.get("Secret"), request.get("Code")
        if not secret or not code:
            raise ValueError("invalid quick-connect response")
        logui("player.pair", backend="jellyfin", code=code)

        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(1)
            r = requests.get(f"{base}/QuickConnect/Connect", params={"Secret": secret},
                             headers=headers, timeout=(5, 15))
            r.raise_for_status()
            if r.json().get("Authenticated"):
                break
        else:
            logui.err("player.pair", backend="jellyfin", linked=False,
                      reason="quick-connect-expired"); return

        r = requests.post(f"{base}/Users/AuthenticateWithQuickConnect",
                          json={"Secret": secret}, headers=headers, timeout=(5, 15))
        r.raise_for_status()
    except Exception:
        logui.err("player.pair", backend="jellyfin", linked=False,
                  reason="server-unavailable"); return
    data  = r.json()
    token = data.get("AccessToken")
    uid   = (data.get("User") or {}).get("Id")
    if not token or not uid:
        logui.err("player.pair", backend="jellyfin", linked=False,
                  reason="auth-failed"); return
    _save_cfg("jellyfin", {"url": base, "token": token, "user_id": uid})
    sync_instances()
    logui("player.linked", backend="jellyfin", server=data.get("ServerName") or base, url=base)


def instances():
    if not _pcfg("token", required=False):
        return
    return [{
        "type": "jellyfin",
        "name": session.get("DeviceName") or session["DeviceId"],
        "device_id": session["DeviceId"],
        "product": session.get("Client"),
        "capabilities": ["player"],
    } for session in (_jreq("get", "/Sessions") or [])
      if session.get("DeviceId") and session.get("SupportsRemoteControl") is True
      and session.get("Client") != _CLIENT_ID]


def lib_add(name, lib_type, path, refresh=True):
    coll = {"movie": "movies", "show": "tvshows", "artist": "music", "mixed": None}.get(lib_type, "movies")
    _jreq("post", "/Library/VirtualFolders",
           params={"name": name, "collectionType": coll, "paths": path, "refreshLibrary": str(refresh).lower()},
           body={"LibraryOptions": {}})


def lib_remove(key, confirm=True):
    _jreq("delete", "/Library/VirtualFolders", params={"name": key})   # `key` = library name
    logui("player.lib.remove", key=key)


def lib_scan(key=None):
    _jreq("post", "/Library/Refresh")
    logui("player.lib.scan", player="jellyfin", scope="all")


def watchlist_remove(rating_key):
    _jreq("delete", f"/Users/{_pcfg('user_id')}/FavoriteItems/{rating_key}")


def control(command, client_id=None):
    client_id = client_id or _jelly_client_id()
    action = {"stop": "Stop", "pause": "Pause", "resume": "Unpause"}[command]
    _jreq("post", f"/Sessions/{client_id}/Playing/{action}")
    return client_id
