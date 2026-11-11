"""
media — TMDB, Trakt, Cinemeta, and media metadata

Usage:
  media search <query...> [--source tmdb|trakt|cinemeta] [--type movie|tv|both] [--json]
  media info <title...> [--source tmdb|trakt|cinemeta] [--type movie|tv|both] [--refresh]

Help: help media | media --help
"""

import argparse
import os
import re
import time
import json
import hashlib
import requests
from pathlib import Path
from dotenv import load_dotenv
from logui import logui

load_dotenv(Path(__file__).with_name(".env"), interpolate=False)

_MEDIA_DIR      = Path(".out") / "media"             # one resource file per fetched list: {source}__{slug}.json
RECOMMENDATIONS = _MEDIA_DIR / "recommendations.json"
EXISTING        = _MEDIA_DIR / "existing.json"

TMDB_BASE  = "https://api.themoviedb.org/3"
TRAKT_BASE = "https://api.trakt.tv"
CINEMETA_BASE = "https://v3-cinemeta.strem.io"

TMDB_GENRES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance", 878: "Sci-Fi",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
    10759: "Action & Adventure", 10762: "Kids", 10763: "News", 10764: "Reality",
    10765: "Sci-Fi & Fantasy", 10766: "Soap", 10767: "Talk", 10768: "War & Politics",
}

MEDIA_CONFIG = Path(".out") / "media.json"


META_DIR = Path(".out") / "media_art"
TMDB_CAP = 2000
TMDB_CONCURRENCY = 4
TMDB_PAUSE = 0.25
TMDB_IMG = "https://image.tmdb.org/t/p/w300"
_TMDB_INDEX_STAMP = None
_TMDB_INDEX = {}


def _key(title, year=None):
    k = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-") or "unknown"
    return f"{k}-{year}" if year else k


def update_setting(path, value):
    _ensure_config()
    f = Path(__file__).parent / MEDIA_CONFIG
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        raise FileNotFoundError("config not found") from e
    if path == "trakt.client_id":
        if not isinstance(value, str):
            raise ValueError("Trakt client ID must be text")
        value = value.strip()
    if path.startswith("recommendations."):
        if path == "recommendations.refresh_interval":
            if type(value) is not int or value < 0:
                raise ValueError("refresh interval must be a nonnegative number of seconds")
        elif path in ("recommendations.lists", "recommendations.exclude_genres"):
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise ValueError("value must be a list of strings")
    opts = (data.get("_options") or {}).get(path)
    if opts:
        vals = value if isinstance(value, list) else [value]
        if any(v not in opts for v in vals):
            raise ValueError(f"value must be one of {opts}")
    keys = path.split(".")
    node = data
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    if not isinstance(node, dict):
        raise ValueError("bad path")
    node[keys[-1]] = value
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if path in ("recommendations.lists", "recommendations.exclude_genres"):
        import library as _library
        _library.sync_recommendations()


def config_options(data):
    data["recommendations"] = recommendations_config()
    data.setdefault("_options", {}).update({
        "recommendations.lists": ["all", *recommendation_lists()],
        "recommendations.exclude_genres": sorted(set(TMDB_GENRES.values())),
    })
    return data


def recommendations() -> list[dict]:
    import torrent as _torrent

    entries = []
    try:
        data = json.loads(RECOMMENDATIONS.read_text(encoding="utf-8"))
        entries = data.get("items", [])
    except Exception:
        import library as _library
        _library.sync_recommendations()
        data = json.loads(RECOMMENDATIONS.read_text(encoding="utf-8"))
        entries = data.get("items", [])
    exclude = excluded_genres("recommendations")
    active = set()
    try:
        torrent_state = json.loads(_torrent._ACTIVE_F.read_text(encoding="utf-8"))
        for n in torrent_state.get("items", []):
            if n.get("progress", 0) < 100 and n.get("state") not in ("uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP", "pausedUP", "error"):
                label = n.get("label") or n.get("name")
                active.add(_key(label))
    except Exception:
        pass
    existing = existing_media_titles()
    filtered = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict):
            ref = {
                "provider": "tmdb" if entry.get("tmdb") is not None else "title",
                "id": entry.get("tmdb") or _key(entry.get("title"), entry.get("year")),
                "type": entry.get("type"), "title": entry.get("title"), "year": entry.get("year"),
            }
        info = cached_info_for_ref(ref)
        if not info:
            continue
        item = dict(info)
        item.setdefault("type", ref.get("type"))
        context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        item["list"] = context.get("list") or entry.get("list") or ""
        if ({str(g).strip().lower() for g in (item.get("genres") or [])} & exclude):
            continue
        key = _key(item.get("title"))
        if not key or key == "unknown":
            continue
        if key in active:
            continue
        item["action"] = media_action(item.get("title"), existing)
        filtered.append(item)
    return filtered


def existing_media_titles():
    try:
        data = json.loads(EXISTING.read_text(encoding="utf-8"))
        return set(data.get("titles", []))
    except Exception:
        return set()


def media_action(title, existing=None):
    existing = existing if existing is not None else existing_media_titles()
    return "play" if _key(_release_title(str(title or ""))) in existing else "find"


def _release_title(name):
    s = name.strip()
    s = re.sub(r"\s+-\s+(?:19|20)\d{2}$", "", s)
    s = re.sub(r"^\S+\s+-\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = s.replace(".", " ").replace("_", " ")
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" .-")


def _evict():
    try: META_DIR.mkdir(parents=True, exist_ok=True)
    except Exception: return
    files = sorted(META_DIR.glob("tmdb-*.json"), key=lambda p: p.stat().st_mtime)
    excess = max(0, len(files) - TMDB_CAP)
    for j in files[:excess]:
        try: j.unlink()
        except Exception: pass


def _available_source(source):
    credentials = {"tmdb": _config("tmdb").get("api_key"),
                   "trakt": _config("trakt").get("client_id")}
    if source in credentials and not str(credentials[source] or "").strip():
        return next((name for name, value in credentials.items() if str(value or "").strip()), "cinemeta")
    return source


def lookup(title, refresh=False, media_type="both", source=None):
    source = _available_source(source or _config("info").get("source", "tmdb"))
    fn = {"tmdb": _tmdb_lookup, "trakt": _trakt_lookup, "cinemeta": _cinemeta_lookup}[source]
    return fn(title, refresh=refresh, media_type=media_type)


def info(title, refresh=False, media_type="both", source=None):
    """Fetch + cache metadata for a title. Prints result or error."""
    source = source or _config("info").get("source", "tmdb")
    result = lookup(title, refresh=refresh, media_type=media_type, source=source)
    if result is None:
        print("ERROR: no result")
    else:
        print(json.dumps(result, indent=2))
        source = result.get("provider") or _available_source(source)
        logui("media.info",
              id=result.get(f"{source}_id", ""), provider=source,
              type=result.get("type") or media_type,
              title=result.get("title", ""),
              action=media_action(result.get("title", "")),
              year=result.get("year", ""),
              rating=result.get("rating", 0),
              votes=result.get("vote_count", 0),
              seasons=result.get("seasons", 0),
              genres=", ".join(result.get("genres", [])),
              overview=result.get("overview", ""),
              poster=result.get("poster") or "",
              poster_path=result.get("poster_path", ""),
              lang=result.get("lang", ""), cert=result.get("cert", ""),
              runtime=result.get("runtime") or "", director="", cast="",
              trailer=result.get("trailer", ""))
    return result


def _get_cached(title):
    """Find cached metadata file by title."""
    jpath = META_DIR / f"tmdb-{_key(title)}.json"
    if jpath.exists():
        return jpath
    wanted = str(title)
    index = _tmdb_cache_index()
    return index.get(("id", wanted)) if wanted.isdigit() else index.get(("title", _key(_release_title(wanted))))


def _tmdb_cache_index():
    global _TMDB_INDEX_STAMP, _TMDB_INDEX
    try:
        files = list(META_DIR.glob("tmdb-*.json"))
        stamp = (str(META_DIR.resolve()), META_DIR.stat().st_mtime_ns, len(files))
        if stamp == _TMDB_INDEX_STAMP:
            return _TMDB_INDEX
        index = {}
        for candidate in META_DIR.glob("tmdb-*.json"):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("tmdb_id") is not None:
                index[("id", str(data["tmdb_id"]))] = candidate
            if data.get("title"):
                index[("title", _key(data["title"]))] = candidate
        _TMDB_INDEX_STAMP, _TMDB_INDEX = stamp, index
        return index
    except Exception:
        return {}


def _cache_matches(data, title, year=""):
    """Reject cache aliases that contain metadata for a different media item."""
    if not data:
        return False
    if str(title).isdigit():
        return str(data.get("tmdb_id") or "") == str(title)
    expected = _release_title(str(title)) or str(title)
    if _key(data.get("title")) != _key(expected):
        return False
    return not year or str(data.get("year") or "") == str(year)


def _tv_cache_complete(data):
    return (isinstance(data, dict)
            and isinstance(data.get("seasons"), int)
            and isinstance(data.get("season_details"), list)
            and data.get("seasons_complete") is True)


def cached_info(label):
    """Return cached metadata matched strictly from a normalized ``Title - Year`` label."""
    match = re.match(r"^(.*?)\s+-\s+((?:19|20)\d{2})$", (label or "").strip())
    title = match.group(1).strip() if match else (label or "").strip()
    year = match.group(2) if match else ""
    source = _available_source(_config("info").get("source", "tmdb"))
    if source in ("trakt", "cinemeta"):
        cached = (_trakt_cached if source == "trakt" else _cinemeta_cached)(title, year=year)
        if cached:
            return cached
    for candidate in (label, title):
        cp = _get_cached(candidate)
        if not cp:
            continue
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            if _cache_matches(data, title, year):
                return data
        except Exception:
            pass
    return _trakt_cached(title, year=year) or _cinemeta_cached(title, year=year)


def cached_info_for_ref(ref):
    """Resolve a provider-qualified media reference from local metadata only."""
    if not isinstance(ref, dict):
        return None
    provider = str(ref.get("provider") or "").lower()
    ref_id = ref.get("id")
    media_type = "tv" if ref.get("type") == "show" else ref.get("type")
    if provider == "cinemeta":
        return _cinemeta_cached(str(ref_id or ref.get("title") or ""),
                                media_type=media_type or "both", year=str(ref.get("year") or ""))
    if provider == "trakt":
        return _trakt_cached(str(ref_id or ref.get("title") or ""),
                             media_type=media_type or "both", year=str(ref.get("year") or ""))
    if provider == "tmdb" and ref_id is not None:
        cached = _get_cached(str(ref_id))
        if cached:
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
                if _cache_matches(data, str(ref_id)):
                    return data
            except Exception:
                pass
    title = str(ref.get("title") or "").strip()
    year = str(ref.get("year") or "").strip()
    info = cached_info(f"{title} - {year}" if title and year else title) if title else None
    if info and provider in ("tmdb", "trakt", "cinemeta") and info.get("provider", "tmdb") != provider:
        return None
    if info and media_type and info.get("type") != media_type:
        return None
    if info and media_type:
        info = dict(info)
        info.setdefault("type", media_type)
    return info


def _tmdb_lookup(title, refresh=False, media_type="both"):
    """Fetch + cache TMDB metadata for a title. Returns dict or None."""
    if not title: return None
    stale = None
    if not refresh:
        cached = _get_cached(title)
        if cached:
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
                if _cache_matches(data, title):
                    cached_type = data.get("type")
                    requested_type = "tv" if media_type == "tv" else "movie" if media_type == "movie" else None
                    if not requested_type or not cached_type or cached_type == requested_type:
                        needs_tv_detail = cached_type == "tv" and not _tv_cache_complete(data)
                        if media_type != "both" and not needs_tv_detail:
                            return data
                        if media_type == "both" and cached_type in ("movie", "tv") and not needs_tv_detail:
                            return data
                        stale = data
            except Exception: pass
    api_key = _config("tmdb")["api_key"]
    if not api_key:
        raise ValueError("TMDB API key missing: set TMDB_API_KEY in .env or tmdb.api_key in media.json")
    time.sleep(TMDB_PAUSE)
    endpoints = ["tv", "movie"] if media_type == "both" else ["tv" if media_type == "tv" else "movie"]
    m = None
    resolved_type = "tv" if media_type == "tv" else "movie"
    stale_type = (stale or {}).get("type")
    if media_type == "both" and stale_type in ("movie", "tv"):
        endpoints = [stale_type]
        resolved_type = stale_type
    lookup_id = (stale or {}).get("tmdb_id") if len(endpoints) == 1 else None
    lookup_id = lookup_id or (title if str(title).isdigit() else None)
    if lookup_id is not None and len(endpoints) == 1:
        m = tmdb_get(f"/{endpoints[0]}/{lookup_id}")
    elif media_type == "both":
        search_title = _release_title(title) or title
        results = [row for row in tmdb_search(search_title, "multi")
                   if row.get("media_type") in ("tv", "movie")]
        m = results[0] if results else None
        if m:
            resolved_type = m.get("media_type")
    else:
        search_title = _release_title(title) or title
        for endpoint in endpoints:
            results = tmdb_search(search_title, endpoint)
            m = results[0] if results else None
            if m:
                resolved_type = endpoint
                break
    if not m:
        if stale is not None:
            return stale
        return None
    season_details = []
    seasons_complete = True
    if resolved_type == "tv":
        m, season_details, seasons_complete = _tmdb_tv_metadata(m, api_key)
    genre_ids = m.get("genre_ids") or [g.get("id") for g in (m.get("genres") or [])]
    meta = {
        "tmdb_id":     m.get("id"),
        "type":        resolved_type,
        "title":       m.get("title") or m.get("original_title") or m.get("name") or m.get("original_name"),
        "year":        (m.get("release_date") or m.get("first_air_date") or "")[:4],
        "genres":      [TMDB_GENRES[g] for g in genre_ids if g in TMDB_GENRES],
        "rating":      round(m.get("vote_average") or 0, 1),
        "vote_count":  m.get("vote_count") or 0,
        "overview":    m.get("overview") or "",
        "poster":      f"{TMDB_IMG}{m.get('poster_path')}" if m.get("poster_path") else None,
        "poster_path": m.get("poster_path") or "",
        "backdrop":    f"https://image.tmdb.org/t/p/w780{m.get('backdrop_path')}" if m.get("backdrop_path") else None,
        "fetched":     time.time(),
    }
    if resolved_type == "tv":
        meta.update({
            "seasons": int(m.get("number_of_seasons") or len(season_details)),
            "season_details": season_details,
            "seasons_complete": seasons_complete,
        })
    canon_key = _key(meta["title"], meta["year"])
    canon_path = META_DIR / f"tmdb-{canon_key}.json"
    try:
        META_DIR.mkdir(parents=True, exist_ok=True)
        canon_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        pass
    _evict()
    return meta


def _tmdb_tv_metadata(summary, api_key):
    """Fetch TV detail plus every advertised numbered season and its episodes."""
    detail = summary
    tmdb_id = summary.get("id")
    complete = True
    if tmdb_id is None:
        return detail, [], False
    if "number_of_seasons" not in detail:
        try:
            r = requests.get(f"{TMDB_BASE}/tv/{tmdb_id}", params={"api_key": api_key}, timeout=10)
            r.raise_for_status()
            detail = r.json()
        except Exception:
            complete = False
    advertised = [s for s in (detail.get("seasons") or [])
                  if isinstance(s, dict) and int(s.get("season_number") or 0) > 0]
    season_details = []
    for advertised_season in advertised:
        number = int(advertised_season.get("season_number") or 0)
        season = advertised_season
        try:
            time.sleep(TMDB_PAUSE)
            r = requests.get(f"{TMDB_BASE}/tv/{tmdb_id}/season/{number}",
                             params={"api_key": api_key}, timeout=10)
            r.raise_for_status()
            season = r.json()
        except Exception:
            complete = False
        episodes = []
        for episode in (season.get("episodes") or []):
            if not isinstance(episode, dict):
                continue
            still = episode.get("still_path")
            episodes.append({
                "id": episode.get("id"),
                "number": episode.get("episode_number"),
                "season": episode.get("season_number", number),
                "name": episode.get("name") or "",
                "air_date": episode.get("air_date") or "",
                "overview": episode.get("overview") or "",
                "runtime": episode.get("runtime"),
                "rating": round(episode.get("vote_average") or 0, 1),
                "still": f"{TMDB_IMG}{still}" if still else None,
            })
        poster = season.get("poster_path") or advertised_season.get("poster_path")
        season_details.append({
            "id": season.get("id") or advertised_season.get("id"),
            "number": number,
            "name": season.get("name") or advertised_season.get("name") or f"Season {number}",
            "air_date": season.get("air_date") or advertised_season.get("air_date") or "",
            "overview": season.get("overview") or advertised_season.get("overview") or "",
            "episode_count": season.get("episode_count") or advertised_season.get("episode_count") or len(episodes),
            "poster": f"{TMDB_IMG}{poster}" if poster else None,
            "episodes": episodes,
        })
    expected = int(detail.get("number_of_seasons") or 0)
    if expected and len(season_details) != expected:
        complete = False
    return detail, season_details, complete


MEDIA_DEFAULTS = {
    "tmdb":    {"api_key": ""},
    "subtitle": {"languages": ["en"]},
    "trakt":   {"client_id": ""},
    "search":  {"source": "tmdb", "limit": 20, "exclude_genres": []},
    "recommendations": {"lists": ["all"], "exclude_genres": [], "refresh_interval": 6*3600},
    "info":    {"source": "tmdb", "poster_size": "w185"},
    "indexer": {"providers": ["apibay", "torrents-csv", "yts", "prowlarr"],
                "yts_api_url": "https://yts.gg/api/v2",
                "quality_profiles": ["1080p", "720p", "!CAM", "!CAMRip", "!HDCAM"]},
    "torrent": {"client": "qbittorrent", "refresh_rate": 30},
    "_options": {
        "search.source":          ["tmdb", "trakt", "cinemeta"],
        "info.source":            ["tmdb", "trakt", "cinemeta"],
        "info.poster_size":       ["w92", "w154", "w185", "w342"],
        "indexer.providers":      ["apibay", "torrents-csv", "yts", "prowlarr"],
        "torrent.client":         ["qbittorrent", "transmission"],
    },
}

def _ensure_config():
    if MEDIA_CONFIG.exists():
        try:
            data = json.loads(MEDIA_CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        changed = False
        for name in ("recommendations", "trakt", "tmdb", "indexer", "subtitle"):
            section = data.setdefault(name, {})
            if not isinstance(section, dict):
                continue
            missing = [key for key in MEDIA_DEFAULTS[name] if key not in section]
            section.update({key: MEDIA_DEFAULTS[name][key] for key in missing})
            changed = changed or bool(missing)
        options = data.setdefault("_options", {})
        for key in ("search.source", "info.source"):
            if options.get(key) != MEDIA_DEFAULTS["_options"][key]:
                options[key] = MEDIA_DEFAULTS["_options"][key].copy()
                changed = True
        if not changed:
            return
    else:
        data = MEDIA_DEFAULTS
    MEDIA_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    MEDIA_CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _config(section):
    try:
        data = json.loads(MEDIA_CONFIG.read_text(encoding="utf-8"))
        config = {**MEDIA_DEFAULTS[section], **data.get(section, {})}
    except (OSError, ValueError, TypeError, AttributeError):
        config = dict(MEDIA_DEFAULTS[section])
    if section == "tmdb":
        config["api_key"] = os.environ.get("TMDB_API_KEY", "").strip() or config.get("api_key", "")
    return config


def recommendations_config():
    try:
        data = json.loads(MEDIA_CONFIG.read_text(encoding="utf-8"))
        return {**MEDIA_DEFAULTS["recommendations"], **data.get("recommendations", {})}
    except (OSError, ValueError, TypeError):
        return dict(MEDIA_DEFAULTS["recommendations"])


def excluded_genres(section="search"):
    try:
        data = json.loads(MEDIA_CONFIG.read_text(encoding="utf-8"))
        genres = data.get(section, {}).get("exclude_genres", []) or []
        if isinstance(genres, str):
            genres = genres.split(",")
        return {str(g).strip().lower() for g in genres if str(g).strip()}
    except (OSError, ValueError, TypeError):
        return set()


def recommendation_lists():
    """List resource files, excluding generated feeds and metadata caches."""
    return sorted(f.name for source in ("tmdb", "trakt", "cinemeta")
                  for f in _MEDIA_DIR.glob(f"{source}_*.json"))


def recommendations_interval():
    try:
        return max(0, int(recommendations_config()["refresh_interval"]))
    except (TypeError, ValueError, OverflowError):
        return MEDIA_DEFAULTS["recommendations"]["refresh_interval"]



# --- Fetch lists -> resource files (used by library.py sync-lists) ------------------
def _list_name(url):
    """Keep list IDs and distinguish filtered versions of the same endpoint."""
    base, _, query = url.split("://", 1)[-1].partition("?")
    parts = base.split("/")[1:]
    if parts and parts[0] in ("3", "4"): parts.pop(0)
    name = re.sub(r"[^\w]", "_", "_".join(parts))[:60] or "list"
    return name + ("_" + hashlib.sha256(query.encode()).hexdigest()[:12] if query else "")

def _write_list_file(source, url, titles):
    """One resource file per fetched list, under .out/media/{source}_{listname}.json."""
    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (_MEDIA_DIR / f"{source}_{_list_name(url)}.json").write_text(
        json.dumps(titles, indent=2), encoding="utf-8")

# --- Trakt client ---------------------------------------------------------------

def trakt_get(url: str):
    client_id = _config("trakt").get("client_id")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ValueError("Trakt is not configured. Set trakt.client_id in media settings.")
    resp = requests.get(url, headers={
        "Content-Type":      "application/json",
        "trakt-api-version": "2",
        "trakt-api-key":     client_id.strip(),
    }, timeout=10)
    if resp.status_code == 403:
        raise ValueError("Trakt rejected the client ID (403). Check trakt.client_id in media settings and your Trakt app approval.")
    resp.raise_for_status()
    return resp.json()

def trakt_search(query: str, media_type="movie") -> list:
    return trakt_get(f"{TRAKT_BASE}/search/{media_type}?query={requests.utils.quote(query)}&extended=full")

def trakt_detail(trakt_id: str, media_type="movie") -> dict:
    return trakt_get(f"{TRAKT_BASE}/{media_type}s/{trakt_id}?extended=full")

def trakt_seasons(trakt_id: str) -> list:
    return trakt_get(f"{TRAKT_BASE}/shows/{trakt_id}/seasons?extended=full,episodes")


def _trakt_cached(title, media_type="both", year=""):
    """Read Trakt metadata without confusing its IDs with TMDB IDs."""
    wanted = str(title)
    for path in META_DIR.glob("trakt-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if media_type != "both" and data.get("type") != media_type:
                continue
            matches = str(data.get("trakt_id")) == wanted if wanted.isdigit() else \
                      _key(data.get("title")) == _key(_release_title(wanted))
            if matches and (not year or str(data.get("year")) == year):
                return data
        except (OSError, ValueError, AttributeError):
            pass
    return None


def _trakt_lookup(title, refresh=False, media_type="both"):
    """Fetch + cache Trakt movie or show metadata in the shared media shape."""
    if not title: return None
    if str(title).isdigit() and media_type == "both":
        raise ValueError("Specify --type movie or tv when looking up a Trakt ID.")
    cached = _trakt_cached(title, media_type)
    if cached and not refresh and (cached.get("type") != "tv" or _tv_cache_complete(cached)):
        return cached
    kind = "show" if media_type == "tv" else "movie"
    trakt_id = title if str(title).isdigit() else None
    if trakt_id is None:
        results = trakt_search(_release_title(str(title)) or title,
                               "movie,show" if media_type == "both" else kind)
        results = [row for row in results if row.get("type") in ("movie", "show")]
        if not results: return None
        kind = results[0]["type"]
        trakt_id = results[0][kind]["ids"]["trakt"]
    detail = trakt_detail(trakt_id, kind)
    ids = detail.get("ids") or {}
    meta = {
        "provider": "trakt", "trakt_id": ids.get("trakt", trakt_id),
        "tmdb_id": ids.get("tmdb"), "type": "tv" if kind == "show" else "movie",
        "title": detail.get("title") or "", "year": str(detail.get("year") or ""),
        "genres": detail.get("genres") or [], "rating": round(detail.get("rating") or 0, 1),
        "vote_count": detail.get("votes") or 0, "overview": detail.get("overview") or "",
        "runtime": detail.get("runtime"), "lang": detail.get("language") or "",
        "cert": detail.get("certification") or "", "trailer": detail.get("trailer") or "",
        "poster": None, "poster_path": "", "fetched": time.time(),
    }
    if kind == "show":
        seasons = []
        for season in trakt_seasons(trakt_id):
            number = season.get("number") or 0
            if number <= 0: continue
            episodes = [{
                "id": (episode.get("ids") or {}).get("trakt"),
                "number": episode.get("number"), "season": number,
                "name": episode.get("title") or "", "air_date": (episode.get("first_aired") or "")[:10],
                "overview": episode.get("overview") or "", "runtime": episode.get("runtime"),
                "rating": round(episode.get("rating") or 0, 1), "still": None,
            } for episode in season.get("episodes", [])]
            seasons.append({
                "id": (season.get("ids") or {}).get("trakt"), "number": number,
                "name": season.get("title") or f"Season {number}",
                "air_date": (season.get("first_aired") or "")[:10],
                "overview": season.get("overview") or "", "poster": None,
                "episode_count": season.get("episode_count") or len(episodes), "episodes": episodes,
            })
        meta.update(seasons=len(seasons), season_details=seasons, seasons_complete=True)
    META_DIR.mkdir(parents=True, exist_ok=True)
    (META_DIR / f"trakt-{meta['type']}-{meta['trakt_id']}.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    return meta


# --- TMDB client ------------------------------------------------------------------

def tmdb_get(path: str, params=None) -> dict:
    p = params or {}
    p["api_key"] = _config("tmdb")["api_key"]
    if not p["api_key"]:
        raise ValueError("TMDB API key missing: set TMDB_API_KEY in .env or tmdb.api_key in media.json")
    try:
        resp = requests.get(path if path.startswith(("http://", "https://")) else f"{TMDB_BASE}{path}", params=p, timeout=10)
    except requests.RequestException as e:
        raise ValueError(f"TMDB request failed ({type(e).__name__})") from None
    if resp.status_code in (401, 403):
        raise ValueError(f"TMDB rejected the API key (HTTP {resp.status_code})")
    if not resp.ok:
        raise ValueError(f"TMDB request failed (HTTP {resp.status_code})")
    return resp.json()

def tmdb_search(query: str, media_type="movie") -> list:
    return tmdb_get(f"/search/{media_type}",
                    {"query": query, "include_adult": "false"}).get("results", [])

def tmdb_detail(tmdb_id: int, media_type="movie") -> dict:
    return tmdb_get(f"/{media_type}/{tmdb_id}",
                    {"append_to_response": "credits,videos,release_dates,content_ratings"})

def tmdb_seasons(tmdb_id: int) -> list:
    return tmdb_get(f"/tv/{tmdb_id}").get("seasons", [])

def tmdb_list(url: str) -> list:
    data = tmdb_get(url)
    return data.get("items", []) if "/list/" in url else data.get("results", [])


def cinemeta_get(path: str) -> dict:
    resp = requests.get(path if path.startswith(("http://", "https://")) else f"{CINEMETA_BASE}{path}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def cinemeta_search(query: str, media_type="movie") -> list:
    kinds = ("movie", "series") if media_type == "both" else ("series" if media_type == "tv" else "movie",)
    rows = []
    for kind in kinds:
        rows.extend(cinemeta_get(f"/catalog/{kind}/top/search={requests.utils.quote(query, safe='')}.json").get("metas") or [])
    return rows


def cinemeta_detail(imdb_id: str, media_type="movie") -> dict:
    kind = "series" if media_type == "tv" else "movie"
    return cinemeta_get(f"/meta/{kind}/{imdb_id}.json").get("meta") or {}


def _cinemeta_cached(title, media_type="both", year=""):
    wanted = str(title)
    for path in META_DIR.glob("cinemeta-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if media_type != "both" and data.get("type") != media_type:
                continue
            matches = data.get("cinemeta_id") == wanted if re.fullmatch(r"tt\d+", wanted) else \
                      _key(data.get("title")) == _key(_release_title(wanted))
            if matches and (not year or str(data.get("year")) == year):
                return data
        except (OSError, ValueError, AttributeError):
            pass
    return None


def _cinemeta_meta(detail):
    imdb_id = detail.get("imdb_id") or detail.get("id")
    runtime = re.search(r"\d+", str(detail.get("runtime") or ""))
    try:
        rating = round(float(detail.get("imdbRating") or 0), 1)
    except (TypeError, ValueError):
        rating = 0
    meta = {
        "provider": "cinemeta", "cinemeta_id": imdb_id, "imdb_id": imdb_id,
        "type": "tv" if detail.get("type") == "series" else "movie",
        "title": detail.get("name") or "", "year": str(detail.get("releaseInfo") or detail.get("released") or "")[:4],
        "genres": detail.get("genres") or [], "rating": rating, "vote_count": 0,
        "overview": detail.get("description") or "", "runtime": int(runtime[0]) if runtime else None,
        "poster": detail.get("poster"), "poster_path": "", "backdrop": detail.get("background"),
        "fetched": time.time(),
    }
    if meta["type"] == "tv" and isinstance(detail.get("videos"), list):
        seasons = {}
        for video in detail["videos"]:
            number, episode = video.get("season"), video.get("episode")
            if not isinstance(number, int) or number <= 0 or not isinstance(episode, int) or episode <= 0:
                continue
            seasons.setdefault(number, []).append({
                "id": video.get("id"), "number": episode, "season": number,
                "name": video.get("title") or "", "air_date": (video.get("released") or "")[:10],
                "overview": video.get("overview") or "", "runtime": meta["runtime"],
                "rating": 0, "still": video.get("thumbnail"),
            })
        details = []
        for number, episodes in sorted(seasons.items()):
            episodes.sort(key=lambda episode: episode["number"])
            details.append({
                "id": f"{imdb_id}:{number}", "number": number, "name": f"Season {number}",
                "air_date": episodes[0]["air_date"], "overview": "", "poster": None,
                "episode_count": len(episodes), "episodes": episodes,
            })
        meta.update(seasons=len(details), season_details=details, seasons_complete=True)
    return meta


def _cinemeta_lookup(title, refresh=False, media_type="both"):
    if not title: return None
    year_match = re.search(r"\s+-\s+((?:19|20)\d{2})$", str(title))
    year = year_match[1] if year_match else ""
    cached = _cinemeta_cached(title, media_type, year)
    if cached and not refresh and (cached.get("type") != "tv" or _tv_cache_complete(cached)):
        return cached
    imdb_id = str(title) if re.fullmatch(r"tt\d+", str(title)) else None
    if imdb_id and media_type == "both":
        raise ValueError("Specify --type movie or tv when looking up a Cinemeta ID.")
    if not imdb_id:
        results = cinemeta_search(_release_title(str(title)) or str(title), media_type)
        if year:
            results = [row for row in results if str(row.get("releaseInfo") or row.get("released") or "")[:4] == year]
        if not results: return None
        selected = next((row for row in results if _key(row.get("name")) == _key(_release_title(str(title)))), results[0])
        imdb_id = selected.get("imdb_id") or selected["id"]
        media_type = "tv" if selected.get("type") == "series" else "movie"
    detail = cinemeta_detail(imdb_id, media_type)
    if not detail: return None
    meta = _cinemeta_meta(detail)
    META_DIR.mkdir(parents=True, exist_ok=True)
    (META_DIR / f"cinemeta-{meta['type']}-{meta['cinemeta_id']}.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    return meta


# --- Ops ------------------------------------------------------------------------

def search(query, source=None, media_type="both", json_out=False):
    source = _available_source(source or _config("search").get("source", "tmdb"))
    exclude = excluded_genres()
    try:
        search_config = json.loads(MEDIA_CONFIG.read_text(encoding="utf-8"))
        limit = max(1, int(search_config.get("search", {}).get("limit", 20)))
    except Exception:
        limit = 20

    rows = []
    if source == "tmdb":
        results = tmdb_search(query, "multi" if media_type == "both" else media_type)
        existing = existing_media_titles()
        for r in results:
            mt = r.get("media_type") or media_type
            if mt not in ("movie", "tv"):
                continue
            genres = [TMDB_GENRES[g] for g in r.get("genre_ids", []) if g in TMDB_GENRES]
            if any(g.lower() in exclude for g in genres):
                continue
            name = r.get("title") or r.get("name")
            year = (r.get("release_date") or r.get("first_air_date") or "")[:4]
            rows.append({"id": r["id"], "provider": "tmdb", "ids": {"tmdb": r["id"]},
                         "title": name, "year": year, "type": mt,
                         "action": media_action(name, existing),
                         "rating": round(r.get("vote_average") or 0, 1),
                         "genres": genres})
            if not json_out: print(f"  [{r['id']}]  {name} ({year})")
            if len(rows) >= limit:
                break

    elif source == "trakt":
        mt = {"tv": "show", "both": "movie,show"}.get(media_type, media_type)
        results = trakt_search(query, mt)
        existing = existing_media_titles()
        for r in results:
            obj    = r.get(r.get("type") or media_type, r)
            genres = obj.get("genres") or []
            if any(g.lower() in exclude for g in genres):
                continue
            ids  = obj.get("ids", {})
            name = obj.get("title")
            year = str(obj.get("year", ""))
            rows.append({"id": ids.get("trakt"), "provider": "trakt", "ids": ids,
                         "title": name, "year": year, "rating": round(obj.get("rating") or 0, 1),
                         "action": media_action(name, existing),
                         "type": "tv" if r.get("type") == "show" else "movie",
                         "genres": genres})
            if not json_out:
                print(f"  [{ids.get('trakt')}]  {name} ({year})  {', '.join(genres)}")
            if len(rows) >= limit:
                break
    elif source == "cinemeta":
        existing = existing_media_titles()
        for result in cinemeta_search(query, media_type):
            row = _cinemeta_meta(result)
            if any(genre.lower() in exclude for genre in row["genres"]):
                continue
            row.update(id=row["cinemeta_id"], ids={"cinemeta": row["cinemeta_id"], "imdb": row["imdb_id"]},
                       action=media_action(row["title"], existing))
            rows.append(row)
            if not json_out: print(f"  [{row['id']}]  {row['title']} ({row['year']})")
            if len(rows) >= limit:
                break
    if json_out: print(json.dumps(rows))
    return rows


def fetch_list(source, url, target=None):
    """Cache an unfiltered list; collection and recommendation consumers apply their filters."""
    print(f"  {url}")
    titles = _fetch_trakt(url, target) if source == "trakt" else \
             _fetch_tmdb(url, target)  if source == "tmdb"  else \
             _fetch_cinemeta(url, target) if source == "cinemeta" else []
    _write_list_file(source, url, titles)
    logui.debug("media.fetch", source=source, list=_list_name(url), titles=len(titles))
    print(f"  fetched {len(titles)} titles")
    return titles

def _trakt_unwrap(item, url):
    mtype = "show" if "show" in item or "/shows" in url else "movie"
    return mtype, item.get(mtype, item)

def _fetch_trakt(url, target):
    titles  = []
    for item in trakt_get(url):
        mtype, obj = _trakt_unwrap(item, url)
        titles.append({
            "title":  obj.get("title", "Unknown"),
            "year":   str(obj.get("year", "")),
            "type":   mtype,
            "target": target,
            "ids":    obj.get("ids", {}),
            "genres": obj.get("genres") or [],
        })
    return titles

def _fetch_tmdb(url, target):
    titles = []
    for item in tmdb_list(url):
        name   = item.get("title") or item.get("name") or "Unknown"
        date   = item.get("release_date") or item.get("first_air_date") or ""
        mtype  = "show" if "/tv" in url else "movie"
        poster = item.get("poster_path")
        titles.append({
            "title":    name,
            "year":     date[:4],
            "type":     mtype,
            "target":   target,
            "ids":      {"tmdb": item.get("id")},
            "poster":   f"https://image.tmdb.org/t/p/w185{poster}" if poster else None,
            "overview": item.get("overview") or "",
            "rating":   round(item.get("vote_average") or 0, 1),
            "genres":   [TMDB_GENRES[g] for g in (item.get("genre_ids") or []) if g in TMDB_GENRES],
        })
    return titles


def _fetch_cinemeta(url, target):
    titles = []
    for item in cinemeta_get(url).get("metas") or []:
        meta = _cinemeta_meta(item)
        meta.update(target=target, ids={"cinemeta": meta["cinemeta_id"], "imdb": meta["imdb_id"]})
        titles.append(meta)
    return titles


CLI_SCHEMA = {"ops": {
    "search": {"args": "query", "params": ["--source", "--type"]},
    "info": {"args": "title", "params": ["--source", "--type", "--refresh"]},
}}


def cli(argv):
    """Parse media command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="media", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--source", default=None, choices=["tmdb", "trakt", "cinemeta"])
    p.add_argument("--json", action="store_true", dest="json_out")
    p.add_argument("--type",   default=None, choices=["movie", "tv", "both"])
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args(argv)

    a, q = args.action, " ".join(args.args)
    if   a == "search": search(q, args.source, args.type or "both", json_out=args.json_out)
    elif a == "info":   info(q, refresh=args.refresh, media_type=args.type or "both", source=args.source)
