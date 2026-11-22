import sys
import json
import re
import time
import webbrowser
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from pathlib import Path
from logui import logui
from player import (
    _PLAYER, _active_player_tv, _emit_status,
    _pick_title, _player_for_app, _player_info, _save_cfg,
    status as _status, sync_instances,
)


_CLIENT_ID = "mediactl"
_STATUS_TIMEOUT = 3
_PLEX_HEADERS = {
    "X-Plex-Client-Identifier": _CLIENT_ID,
    "X-Plex-Product":           "mediactl",
    "Accept":                   "application/json",
}
_PLEX_PLAYBACK_URL_CACHE = {}


def _pcfg(field, required=True):
    try:
        entries = json.loads(Path(".out/services.json").read_text(encoding="utf-8")).get("media-center") or {}
    except Exception:
        entries = {}
    server = next((info for name, info in entries.items()
                   if "server" in (info.get("capabilities") or [])
                   and (info.get("type") == "plex" or name == "plex" or name.startswith("plex-"))), {})
    value = server.get(field)
    if not value and required:
        raise ValueError(f"missing plex config: {field} — set up the now-playing tile")
    return value


def watchlist_get():
    entries, offset = [], 0
    while True:
        root = _xml(_cloud("get", "/library/sections/watchlist/all", params={
            "includeGuids": 1, "X-Plex-Container-Start": offset, "X-Plex-Container-Size": 100}))
        page = root.findall("Video") + root.findall("Directory")
        entries.extend(page)
        offset += len(page)
        if not page or offset >= int(root.get("totalSize") or offset):
            break
    return {
        el.attrib.get("ratingKey"): {
            "title": el.attrib.get("title", "?"),
            "year":  el.attrib.get("year",  "?"),
            "type":  el.attrib.get("type",  "?"),
            "ids": dict(g.get("id").split("://", 1) for g in el.findall("Guid")
                        if "://" in (g.get("id") or "")),
        }
        for el in entries
    }


# Plex providers


def play(title, year=None):
    def trace(event, **fields):
        details = "  ".join(f"{key}={value}" for key, value in fields.items())
        logui.print(f"  player.play-video  event={event}  {details}".rstrip())

    import tv
    current = _status(emit=False)
    tv_name = current.get("tv")
    if not tv_name:
        raise Exception("no TV playback target configured")
    if not current.get("tv_on"):
        raise Exception(f"TV '{tv_name}' is off or unreachable")

    trace("tv-ready", tv=tv_name)
    if not current.get("app"):
        trace("app-launch", tv=tv_name)
        tv.open_app("Plex", tv=tv_name)
        for _ in range(10):
            time.sleep(1)
            current = _status(emit=False, tv_name=tv_name)
            if current.get("app"):
                break
        else:
            raise Exception(f"Plex did not become ready on TV '{tv_name}'")
    trace("app-ready", tv=tv_name)

    server, item = _plex_find_title(title, year)
    for attempt in range(1, 6):
        current = _status(emit=False, tv_name=tv_name, server=server)
        client = current.get("_client")
        if client:
            break
        trace("player-wait", tv=tv_name, attempt=attempt)
        if attempt < 5:
            time.sleep(2)
    else:
        raise Exception(f"Plex player did not become ready on TV '{tv_name}'")

    item_rating_key = str(getattr(item, "ratingKey", "") or "")
    current_matches = (current.get("key") == item.key or
                       (item_rating_key and current.get("rating_key") == item_rating_key))
    if current_matches and current.get("state") == "paused":
        client.sendCommand("playback/play", type="video")
    elif not (current_matches and current.get("playing")):
        for attempt in range(1, 4):
            if current.get("state") not in {"playing", "paused"}:
                break
            trace("player-stop", client=client.title, attempt=attempt)
            client.sendCommand("playback/stop", type="video")
            time.sleep(1)
            current = _status(emit=False, tv_name=tv_name, server=server, client=client)
        if current.get("state") in {"playing", "paused"}:
            raise Exception("Plex player did not stop")
        trace("play", title=item.title, client=client.title)
        _plex_play_media(server, client, item)
    for attempt in range(1, 16):
        time.sleep(1)
        current = _status(emit=False, tv_name=tv_name, server=server, client=client)
        current_matches = (
            current.get("key") == item.key
            or (item_rating_key and current.get("rating_key") == item_rating_key)
        )
        if current_matches and current.get("playing"):
            _emit_status(current)
            logui("player.play-video", event="playing", title=item.title,
                  client=client.title)
            return server, client, item
    raise Exception("Plex did not start playing the video")


def status(tv_name, tv_info, app_id, server=None, client=None):
    import tv
    app_on = _player_for_app(app_id) == "plex"

    error = None
    sessions = []
    # A reachable TV running another app is a valid non-Plex state. Do not
    # turn a missing/unreachable Plex server into an error for that state.
    if not tv_name or app_on:
        try:
            server = server or _plexapi_server(timeout=_STATUS_TIMEOUT)
            sessions = server.sessions()
        except Exception as exc:
            error = str(exc)

    if client is None and server and app_on:
        try:
            client = _plex_client(server, tv_name=tv_name, tv_info=tv_info)
        except Exception:
            pass

    target_host = str((tv_info or {}).get("host", "")).split(":")[0]
    session = next((s for s in sessions if (
        getattr(getattr(s, "player", None), "title", None) == tv_name
        or (target_host and
            getattr(getattr(s, "player", None), "address", None) == target_host)
    )), None)
    if client:
        client_ids = {
            getattr(client, "machineIdentifier", None),
            getattr(client, "_plexapi_machine_identifier", None),
        } - {None}
        client_host, _ = _endpoint(getattr(client, "_baseurl", ""))
        session = session or next((s for s in sessions if (
            getattr(getattr(s, "player", None), "machineIdentifier", None) in client_ids
            or getattr(getattr(s, "player", None), "title", None) == client.title
            or (client_host and
                getattr(getattr(s, "player", None), "address", None) == client_host)
        )), None)
    if session is None and len(sessions) == 1:
        smart_tvs = [info for info in tv._parse_tvs().values()
                     if str(info.get("brand", "")).lower() in tv._SMART_BRANDS]
        if len(smart_tvs) == 1:
            session = sessions[0]

    timeline = None
    if client:
        try:
            timeline = client.timeline
        except Exception:
            pass

    player = getattr(session, "player", None)
    state = getattr(timeline, "state", None) or getattr(player, "state", None) or "stopped"
    playing = state == "playing"
    key = getattr(timeline, "key", None) or getattr(session, "key", None)
    rating_key = str(
        getattr(timeline, "ratingKey", None) or getattr(session, "ratingKey", None) or ""
    )
    thumb = getattr(session, "thumb", None)
    if thumb and str(thumb).startswith("/") and server:
        try:
            thumb = (f"{_plex_playback_url(server).rstrip('/')}{thumb}"
                     f"?X-Plex-Token={_pcfg('token')}")
        except Exception:
            pass
    result = {
        "player": client is not None,
        "playing": playing,
        "state": state,
        "title": getattr(session, "title", None),
        "year": getattr(session, "year", None),
        "type": getattr(session, "type", None),
        "offset": int((getattr(session, "viewOffset", None)
                       or getattr(timeline, "time", 0) or 0) / 1000),
        "duration": int((getattr(session, "duration", None)
                         or getattr(timeline, "duration", 0) or 0) / 1000),
        "grandparent": getattr(session, "grandparentTitle", None),
        "season": getattr(session, "parentIndex", None),
        "episode": getattr(session, "index", None),
        "thumb": thumb,
        "key": key,
        "rating_key": rating_key or None,
        "client": getattr(client, "title", None),
        "error": error,
        "_server": server,
        "_client": client,
        "_session": session,
    }
    return result


def pair(timeout=120):
    pin = _plextv("post", "/pins", params={"strong": "true"})
    pin_id, code = pin["id"], pin["code"]
    auth_url = (f"https://app.plex.tv/auth#?clientID={_CLIENT_ID}"
                f"&code={code}&context%5Bdevice%5D%5Bproduct%5D={_CLIENT_ID}")
    logui("player.pair", authUrl=auth_url, pinId=pin_id)
    if sys.stdout.isatty():
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    token, deadline = None, time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        token = _plextv("get", f"/pins/{pin_id}").get("authToken")
        if token:
            break
    if not token:
        logui.err("player.pair", linked=False, reason="timeout"); return

    resources = _plextv("get", "/resources", token, params={"includeHttps": 1})
    servers = [server for server in resources
               if "server" in (server.get("provides") or "") and server.get("owned")]
    if not servers:
        logui.err("player.pair", linked=False, reason="no-server"); return

    server = servers[0]
    srv_url = next((connection.get("uri") for connection in server.get("connections", [])
                    if connection.get("uri")), None)
    srv_token = server.get("accessToken")
    name = server.get("name")
    if not srv_url:
        logui.err("player.pair", linked=False, reason="no-server"); return

    _save_cfg("plex", {"name": name, "url": srv_url, "token": srv_token,
                       "account_token": token})
    sync_instances()
    logui("player.linked", server=name, url=srv_url)


def _req(method, base, path, token, params=None, **kwargs):
    all_params = {"X-Plex-Token": token}
    if params:
        all_params.update(params)
    qs   = "&".join(f"{k}={requests.utils.quote(str(v), safe='')}" for k, v in all_params.items())
    url  = f"{base}{path}?{qs}"
    kwargs.setdefault("timeout", (5, 15))
    resp = getattr(requests, method)(url, headers={"Accept": "application/xml"}, **kwargs)
    resp.raise_for_status()
    return resp.text if resp.content else ""


def _local(method, path, params=None):
    return _req(method, _pcfg("url"), path, _pcfg("token"), params)


def _cloud(method, path, params=None):
    return _req(method, "https://discover.provider.plex.tv", path, _pcfg("account_token"), params)


def _xml(raw):
    return ET.fromstring(raw) if raw else ET.Element("empty")


def favorites():
    if not _pcfg("account_token", required=False):
        return {}
    try:
        return watchlist_get()
    except Exception:
        return {}


def libraries(counts=False):
    result = []
    for el in _xml(_local("get", "/library/sections")).findall("Directory"):
        item = dict(el.attrib)
        if item.get("type") == "show":
            item["type"] = "tv"
        elif item.get("type") == "movie" and item.get("agent") == "com.plexapp.agents.none":
            item["type"] = "mixed"
        item["locations"] = [loc.get("path") for loc in el.findall("Location") if loc.get("path")]
        if counts:
            data = _xml(_local("get", f"/library/sections/{item['key']}/all", params={
                "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 0, "includeCollections": 0}))
            item["count"] = int(data.attrib["totalSize"])
            item["scanning"] = item.get("refreshing") == "1"
        result.append(item)
    return result


def play_target():
    """Return the TV to use for Plex playback, preferring a foreground Plex TV."""
    active = _active_player_tv("plex")
    if active[0]:
        return active
    try:
        # Keep playback's implicit target consistent with the TV CLI: the first
        # configured TV is the default when no TV was specified.
        import tv
        return tv._get_device(None)
    except Exception:
        return None, None


def _plextv(method, path, token=None, params=None):
    p = dict(params or {})
    if token:
        p["X-Plex-Token"] = token
    resp = getattr(requests, method)(f"https://plex.tv/api/v2{path}",
                                     params=p, headers=_PLEX_HEADERS)
    resp.raise_for_status()
    return resp.json()


def _endpoint(value):
    parsed = urlsplit(str(value or ""))
    return parsed.hostname, parsed.port


def _plex_clients(server, clients=None):
    """Return controllable Plex clients using PlexAPI plus LAN discovery."""
    clients = list(clients if clients is not None else server.clients())
    try:
        from plexapi.gdm import GDM
        players = []
        for entry in GDM().all(scan_for_clients=True):
            data = entry.get("data") or {}
            host = (entry.get("from") or (None,))[0]
            identifier = data.get("Resource-Identifier")
            if data.get("Content-Type") != "plex/media-player" or not host or not identifier:
                continue
            try:
                port = int(data.get("Port") or 32433)
            except (TypeError, ValueError):
                port = 32433
            players.append({"machineIdentifier": identifier, "address": host, "port": port})
    except Exception:
        players = []

    by_endpoint = {(p["address"], p["port"]): p for p in players}
    known = set()
    for client in clients:
        endpoint = _endpoint(getattr(client, "_baseurl", ""))
        known.add(endpoint)
        player = by_endpoint.get(endpoint)
        if player:
            client._plexapi_machine_identifier = client.machineIdentifier
            client.machineIdentifier = player["machineIdentifier"]

    if players:
        try:
            from plexapi.client import PlexClient
        except ImportError:
            return clients
        for player in players:
            endpoint = (player["address"], player["port"])
            if endpoint in known:
                continue
            try:
                clients.append(PlexClient(
                    server=server,
                    baseurl=f"http://{player['address']}:{player['port']}",
                    identifier=player["machineIdentifier"],
                    token=_pcfg("token"),
                    timeout=3,
                ))
                known.add(endpoint)
            except Exception:
                continue
    return clients


def _plex_playback_url(server):
    cache_key = getattr(server, "machineIdentifier", None) or str(server._baseurl)
    if cache_key in _PLEX_PLAYBACK_URL_CACHE:
        return _PLEX_PLAYBACK_URL_CACHE[cache_key]

    configured = _pcfg("playback_url", required=False)
    if configured:
        value = configured.rstrip("/")
        _PLEX_PLAYBACK_URL_CACHE[cache_key] = value
        return value

    base = str(server._baseurl).rstrip("/")
    parsed = urlsplit(base)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        _PLEX_PLAYBACK_URL_CACHE[cache_key] = base
        return base

    account_token = _pcfg("account_token", required=False)
    if account_token:
        try:
            resources = _plextv("get", "/resources", account_token, params={"includeHttps": 1})
            for resource in resources:
                resource_id = resource.get("clientIdentifier") or resource.get("machineIdentifier")
                if resource_id != server.machineIdentifier:
                    continue
                connections = resource.get("connections") or []
                for connection in connections:
                    uri = connection.get("uri")
                    uri_host = urlsplit(uri or "").hostname
                    if uri and connection.get("local") and not connection.get("relay") and \
                            uri_host not in {"127.0.0.1", "localhost", "::1"}:
                        value = uri.rstrip("/")
                        _PLEX_PLAYBACK_URL_CACHE[cache_key] = value
                        return value
        except Exception:
            pass

    raise Exception(
        "Plex playback needs a LAN server URL; set playback_url for the Plex server "
        "to e.g. http://192.168.1.35:32400"
    )


def _plex_play_media(server, client, item):
    playback_url = urlsplit(_plex_playback_url(server))
    media_type = getattr(item, "listType", None) or "video"
    if media_type == "audio":
        media_type = "music"

    playqueue = server.createPlayQueue(item)
    token = _pcfg("token")
    client.sendCommand(
        "playback/playMedia",
        providerIdentifier="com.plexapp.plugins.library",
        machineIdentifier=server.machineIdentifier,
        protocol=playback_url.scheme or "http",
        address=playback_url.hostname,
        port=playback_url.port or (443 if playback_url.scheme == "https" else 32400),
        key=item.key or playqueue.selectedItem.key,
        containerKey=f"/playQueues/{playqueue.playQueueID}?window=100&own=1",
        type=media_type,
        token=token,
        offset=0,
    )


def _plex_control(client_id, command, media_type="video"):
    server = _plexapi_server()
    client = _plex_client(server, client_id)
    client.sendCommand(f"playback/{command}", type=media_type)
    expected = {"stop": "stopped", "pause": "paused", "play": "playing"}[command]
    tv_name, _ = _active_player_tv("plex")
    for _ in range(5):
        time.sleep(0.2)
        current = _status(emit=False, tv_name=tv_name, server=server,
                         client=client, refresh_tv=False)
        if current.get("state") == expected:
            break
    _emit_status(current)


def _plexapi_server(timeout=None):
    try:
        from plexapi.server import PlexServer
    except ImportError:
        raise Exception("Plex playback requires: pip install plexapi")
    return PlexServer(_pcfg("url"), _pcfg("token"), timeout=timeout)


def _plex_client(server, client_id=None, tv_name=None, tv_info=None):
    client_id = client_id or (_player_info(_PLAYER.get()) or {}).get("client_id")
    clients = server.clients()
    if client_id:
        match = next((c for c in clients if client_id in (
            c.machineIdentifier, getattr(c, "_plexapi_machine_identifier", None), c.title)), None)
        if match:
            return match
    clients = _plex_clients(server, clients)
    if client_id:
        match = next((c for c in clients if client_id in (
            c.machineIdentifier, getattr(c, "_plexapi_machine_identifier", None), c.title)), None)
        if match:
            return match
        raise Exception(f"Plex player '{client_id}' is not available for remote control")

    if tv_name is None:
        tv_name, tv_info = _active_player_tv("plex")
    host = str((tv_info or {}).get("host", "")).split(":")[0]
    for client in clients:
        base = str(getattr(client, "_baseurl", ""))
        if client.title == tv_name or (host and host in base):
            return client
    if len(clients) == 1:
        return clients[0]
    if not clients:
        raise Exception(
            "Plex is open but is not advertising as a controllable player; "
            "enable Remote Control / Advertise as Player in the TV Plex app"
        )
    if not tv_name:
        raise Exception("Plex is not the current foreground TV app")
    raise Exception("multiple Plex players are available and none matches the foreground TV")


def _plex_find_title(title, year=None):
    server = _plexapi_server()
    candidates = [item for item in server.library.search(title=title)
                  if getattr(item, "TYPE", "") in {"movie", "show", "season", "episode"}]
    item = _pick_title(candidates, title, year)
    if not item:
        suffix = f" ({year})" if year else ""
        raise Exception(f"'{title}{suffix}' was not found in the Plex library")
    if getattr(item, "TYPE", "") in {"show", "season"}:
        next_item = None
        if getattr(item, "TYPE", "") == "show":
            try:
                next_item = item.onDeck()
            except Exception:
                pass
        episodes = [] if next_item else item.episodes()
        item = next_item or next((ep for ep in episodes if not getattr(ep, "isWatched", False)), None)
        item = item or (episodes[0] if episodes else None)
        if not item:
            raise Exception(f"'{title}' has no playable episodes in Plex")
    return server, item


def watchlist_add(rating_key, year=None, media_type=None):
    plex_id = re.fullmatch(r"(?:plex://(?:movie|show)/)?([0-9a-fA-F]{24})", rating_key)
    if plex_id:
        rating_key = plex_id[1]
    elif rating_key.startswith("tmdb://"):
        if not re.fullmatch(r"tmdb://\d+", rating_key) or media_type not in {"movie", "tv"}:
            raise ValueError("TMDB IDs require --type movie|tv")
        root = _xml(_req("get", "https://metadata.provider.plex.tv", "/library/metadata/matches",
                         _pcfg("account_token"), {"guid": rating_key,
                                                "type": 1 if media_type == "movie" else 2}))
        matches = {el.get("ratingKey") for el in root
                   if el.tag in {"Video", "Directory"} and el.get("ratingKey")}
        if len(matches) != 1:
            raise ValueError(f"no unique Plex match for {rating_key}")
        rating_key = matches.pop()
    else:
        response = requests.get("https://discover.provider.plex.tv/library/search",
            params={"query": rating_key, "limit": 30, "includeMetadata": 1,
                    "searchTypes": {"movie": "movies", "tv": "tv"}.get(media_type, "movies,tv"),
                    "searchProviders": "discover"},
            headers={**_PLEX_HEADERS, "X-Plex-Token": _pcfg("account_token")}, timeout=(5, 15))
        response.raise_for_status()
        matches = {}
        for group in response.json().get("MediaContainer", {}).get("SearchResults", []):
            if group.get("id") != "external":
                continue
            for result in group.get("SearchResult", []):
                item = result.get("Metadata") or {}
                if item.get("type") not in {"movie", "show"}:
                    continue
                if item.get("title", "").casefold() != rating_key.casefold():
                    continue
                if year is not None and str(item.get("year")) != str(year):
                    continue
                key = item.get("ratingKey") or item.get("guid", "").rsplit("/", 1)[-1]
                if key:
                    matches[str(key)] = item
        if not matches:
            raise ValueError(f"no exact Plex match for {rating_key}" + (f" - {year}" if year else ""))
        if len(matches) > 1:
            choices = "; ".join(f"{item['title']} - {item.get('year', '?')} [{key}]"
                                for key, item in matches.items())
            raise ValueError(f"multiple matches; use title - year or Plex ID: {choices}")
        rating_key = next(iter(matches))
    _cloud("put", "/actions/addToWatchlist", params={"ratingKey": rating_key})
    return rating_key


def instances():
    token = _pcfg("account_token", required=False)
    if not token:
        return
    resources = _plextv("get", "/resources", token, params={"includeHttps": 1}) or []
    from plexapi.myplex import MyPlexAccount
    devices = MyPlexAccount(token=token).devices()
    instances = []
    resource_by_id = {}
    for resource in resources:
        client_id = resource.get("clientIdentifier") or resource.get("machineIdentifier")
        if client_id:
            resource_by_id[client_id] = resource

    for device in devices:
        product = str(getattr(device, "product", "") or "")
        device_provides = str(getattr(device, "provides", "") or "")
        if not (product.startswith("Plex for ") or "server" in device_provides or
                "player" in device_provides):
            continue
        client_id = getattr(device, "clientIdentifier", None)
        resource = resource_by_id.get(client_id, {})
        raw_provides = resource.get("provides") or device_provides
        if isinstance(raw_provides, str):
            capabilities = [v.strip() for v in raw_provides.split(",") if v.strip()]
        else:
            capabilities = [str(v) for v in (raw_provides or []) if str(v)]

        row = {
            "type": "plex",
            "name": resource.get("name") or getattr(device, "name", None) or client_id,
            "client_id": client_id,
            "product": resource.get("product") or product,
            "platform": resource.get("platform") or getattr(device, "platform", None),
            "capabilities": capabilities,
            "account_device": True,
        }
        last_seen = getattr(device, "lastSeenAt", None)
        if last_seen:
            row["last_seen_at"] = (last_seen.isoformat() if hasattr(last_seen, "isoformat")
                                    else str(last_seen))
        if "presence" in resource:
            row["presence"] = str(resource.get("presence")).lower() in {"1", "true", "yes"}

        connection = None
        for candidate in resource.get("connections") or []:
            uri = candidate.get("uri")
            if not uri:
                continue
            local = str(candidate.get("local", "")).lower() in {"1", "true", "yes"}
            relay = str(candidate.get("relay", "")).lower() in {"1", "true", "yes"}
            if local and not relay:
                connection = candidate
                break
            connection = connection or candidate
        if connection and connection.get("uri"):
            row["url"] = connection["uri"].rstrip("/")
            host = urlsplit(connection["uri"]).hostname
            if host and re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
                row["source"] = host

        instances.append(row)

    return instances


def lib_add(name, lib_type, path, refresh=True):
    agents   = {"movie": "tv.plex.agents.movie",   "show": "tv.plex.agents.series", "artist": "tv.plex.agents.music"}
    scanners = {"movie": "Plex Movie",             "show": "Plex TV Series",        "artist": "Plex Music"}
    _local("post", "/library/sections", params={
        "name":     name,
        "type":     "movie" if lib_type == "mixed" else lib_type,
        "agent":    "com.plexapp.agents.none" if lib_type == "mixed" else agents.get(lib_type, "tv.plex.agents.movie"),
        "scanner":  "Plex Video Files Scanner" if lib_type == "mixed" else scanners.get(lib_type, "Plex Movie"),
        "location": path,
        "language": "en-US",
    })


def lib_remove(key, confirm=True):
    libs = {lib["key"]: lib["title"] for lib in libraries()}
    if key not in libs:
        logui.err("player.lib.remove", msg=f"key '{key}' not found"); return
    if confirm and input(f"  Delete [{key}] '{libs[key]}'? [y/N] ").strip().lower() != "y":
        logui("player.lib.remove", aborted=True); return
    _local("delete", f"/library/sections/{key}")
    logui("player.lib.remove", key=key, title=libs[key])


def lib_scan(key=None):
    if key:
        _local("get", f"/library/sections/{key}/refresh")
        logui("player.lib.scan", player="plex", key=key)
    else:
        for lib in libraries():
            _local("get", f"/library/sections/{lib['key']}/refresh")
        logui("player.lib.scan", player="plex", scope="all")


def watchlist_remove(rating_key):
    _cloud("put", "/actions/removeFromWatchlist", params={"ratingKey": rating_key})


def control(command, client_id=None):
    _plex_control(client_id, "play" if command == "resume" else command)
    return client_id
