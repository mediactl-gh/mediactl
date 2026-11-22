"""
indexer — torrent search providers

"""

import base64
import hashlib
import re
import requests
from shutil import get_terminal_size
from urllib.parse import parse_qs, quote, urlsplit
import media
import device as _device

TIMEOUT  = 10
TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
]


def _magnet(info_hash, name):
    trs = "".join(f"&tr={quote(t)}" for t in TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}{trs}"


def _info_hash(value):
    if re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return value.lower()
    if re.fullmatch(r"[A-Za-z2-7]{32}", value):
        return base64.b32decode(value.upper()).hex()
    return ""


def result_id(result):
    info_hash = _info_hash(result.get("infoHash") or "")
    if info_hash:
        return info_hash
    magnet = result.get("magnetUrl") or ""
    for topic in parse_qs(urlsplit(magnet).query).get("xt", []):
        if topic.lower().startswith("urn:btih:"):
            info_hash = _info_hash(topic[9:])
            if info_hash:
                return info_hash
    return result.get("downloadUrl") or magnet


def resolve(value, metadata=None):
    value = value.strip()
    info_hash = _info_hash(value.strip("[]")) or _info_hash(result_id({"magnetUrl": value}))
    if info_hash and metadata is not None:
        if "yts" in _enabled_providers():
            try:
                result = _resolve_yts(info_hash)
                if result:
                    metadata.update(result.get("media") or {"title": result.get("title", "")})
            except Exception as exc:
                print(f"  ! yts: {exc}")
        return value if value.lower().startswith("magnet:") else _magnet(info_hash, "")
    if value.lower().startswith(("magnet:", "http://", "https://")):
        return value
    info_hash = _info_hash(value.strip("[]"))
    if info_hash:
        return _magnet(info_hash, "")
    errors = {}
    matches = [r for r in search(value, errors=errors) if r.get("title") == value]
    if not matches:
        detail = " (search incomplete: provider errors)" if errors else ""
        raise ValueError(f"no exact torrent title matching: {value}{detail}; use a full torrent ID or URL")
    if len(matches) > 1:
        ids = ", ".join(result_id(r) for r in matches)
        raise ValueError(f"multiple torrents have that exact title; use a torrent ID: {ids}")
    result = matches[0]
    if metadata is not None:
        metadata.update(result.get("media") or {"title": result.get("title", "")})
    return result.get("magnetUrl") or result.get("downloadUrl") or _magnet(result_id(result), value)


# ── Providers ──────────────────────────────────────────────────────────────────
def _search_apibay(query, categories=None):
    response = requests.get("https://apibay.org/q.php",
                            params={"q": query}, timeout=TIMEOUT)
    response.raise_for_status()
    rows = response.json()
    out = []
    for r in rows:
        if not r.get("info_hash", "").strip("0"):   # "no results" sentinel
            continue
        out.append({"title": r.get("name", ""), "seeders": int(r.get("seeders") or 0),
                    "media": {"title": r.get("name", ""), "imdb_id": r.get("imdb")},
                    "size": int(r.get("size") or 0), "indexer": "apibay",
                    "magnetUrl": _magnet(r["info_hash"], r.get("name", ""))})
    return out

def _search_torrents_csv(query, categories=None):
    response = requests.get("https://torrents-csv.com/service/search",
                            params={"q": query}, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    out = []
    for r in data.get("torrents", []):
        out.append({"title": r.get("name", ""), "seeders": int(r.get("seeders") or 0),
                    "size": int(r.get("size_bytes") or 0), "indexer": "torrents-csv",
                    "magnetUrl": _magnet(r["infohash"], r.get("name", ""))})
    return out

def _torrent_name(data, info_hash):
    def decode(pos):
        start = pos
        token = data[pos:pos + 1]
        if token == b"i":
            end = data.index(b"e", pos)
            return int(data[pos + 1:end]), end + 1
        if token in (b"l", b"d"):
            value = [] if token == b"l" else {}
            pos += 1
            while data[pos:pos + 1] != b"e":
                item, pos = decode(pos)
                if token == b"l":
                    value.append(item)
                else:
                    begin = pos
                    value[item], pos = decode(pos)
                    if start == 0 and item == b"info":
                        if hashlib.sha1(data[begin:pos]).hexdigest() != info_hash:
                            raise ValueError("torrent hash mismatch")
            return value, pos + 1
        end = data.index(b":", pos)
        size = int(data[pos:end])
        if size < 0 or end + 1 + size > len(data):
            raise ValueError("invalid torrent metadata")
        return data[end + 1:end + 1 + size], end + 1 + size

    torrent, end = decode(0)
    if end != len(data) or not isinstance(torrent, dict):
        raise ValueError("invalid torrent metadata")
    info = torrent[b"info"]
    return (info.get(b"name.utf-8") or info[b"name"]).decode("utf-8")


def _resolve_yts(info_hash):
    base = media._config("indexer")["yts_api_url"].rstrip("/")
    response = requests.get(f"{base.split('/api/')[0]}/torrent/download/{info_hash.upper()}",
                            timeout=TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    name = _torrent_name(response.content, info_hash)
    query = re.split(r"\s*\((?:19|20)\d{2}\)", name, maxsplit=1)[0]
    return next((r for r in _search_yts(query) if result_id(r) == info_hash), None)


def _search_yts(query, categories=None):
    base = media._config("indexer")["yts_api_url"].rstrip("/")
    response = requests.get(f"{base}/list_movies.json",
                            params={"query_term": query}, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "ok":
        raise ValueError(data.get("status_message") or "YTS API error")
    out = []
    for m in data.get("data", {}).get("movies") or []:
        title = m.get("title_long") or m.get("title", "")
        for t in m.get("torrents") or []:
            name = f"{title} {t.get('quality', '')} YTS"
            out.append({"title": name, "seeders": int(t.get("seeds") or 0),
                        "media": {"title": m.get("title"), "year": m.get("year"),
                                  "imdb_id": m.get("imdb_code"), "type": "movie",
                                  "quality": t.get("quality")},
                        "size": int(t.get("size_bytes") or 0), "indexer": "yts",
                        "magnetUrl": _magnet(t["hash"], name)})
    return out

def _search_prowlarr(query, categories=None):
    config = _device._registered_services("indexer").get("prowlarr", {})
    url, key = config.get("url", "").rstrip("/"), config.get("api_key", "")
    if not url or not key:
        return []
    params = {"query": query, "type": "search", "apikey": key}
    if categories:
        params["categories"] = categories
    response = requests.get(f"{url}/api/v1/search", params=params, timeout=30)
    response.raise_for_status()
    rows = response.json()
    out = []
    for r in rows:
        out.append({"title": r.get("title", ""), "seeders": int(r.get("seeders") or 0),
                    "media": {"title": r.get("title", ""), "imdb_id": r.get("imdbId")},
                    "size": int(r.get("size") or 0), "indexer": r.get("indexer", "prowlarr"),
                    "magnetUrl": r.get("magnetUrl") or "",
                    "infoHash": r.get("infoHash") or "",
                    "downloadUrl": r.get("downloadUrl") or ""})
    return out

PROVIDERS = {
    "apibay":       _search_apibay,
    "torrents-csv": _search_torrents_csv,
    "yts":          _search_yts,
    "prowlarr":     _search_prowlarr,
}

def _enabled_providers():
    names = [n for n in media._config("indexer")["providers"] or [] if n in PROVIDERS]
    if not names:
        names = ["apibay", "torrents-csv", "yts"]
    prowlarr = _device._registered_services("indexer").get("prowlarr", {})
    if "prowlarr" in names and not (prowlarr.get("url") and prowlarr.get("api_key")):
        names.remove("prowlarr")
    return names


# ── Search ─────────────────────────────────────────────────────────────────────
def search(query, indexer_id=None, categories=None, *, title=None, year=None, errors=None):
    """Search enabled providers. Returns merged list sorted by seeders."""
    names = [indexer_id] if indexer_id in PROVIDERS else _enabled_providers()
    text_query = f"{title} {year}" if title and year and year != "?" else title
    results, seen = [], {}
    for name in names:
        try:
            provider_query = text_query if text_query and name != "yts" else query
            rows = PROVIDERS[name](provider_query, categories)
            if not rows and text_query and provider_query != text_query:
                rows = PROVIDERS[name](text_query, categories)
        except Exception as e:
            print(f"  ! {name}: {e}")
            if errors is not None:
                errors[name] = str(e)
            continue
        for r in rows:
            key = result_id(r)
            if not key:
                continue
            if key in seen:
                if (r.get("media") or {}).get("imdb_id") and not (seen[key].get("media") or {}).get("imdb_id"):
                    seen[key]["media"] = r["media"]
                continue
            seen[key] = r
            r["id"] = key
            results.append(r)
    return sorted(results, key=lambda r: r.get("seeders", 0), reverse=True)

def result_rows(results, top=20):
    profiles = [p.strip() for p in media._config("indexer")["quality_profiles"]]
    qualities = [p.lower() for p in profiles if p and not p.startswith(("!", "-"))]
    exclusions = [re.compile(r"(?:^|[\W_])" + re.escape(p[1:].strip()) + r"(?=$|[\W_])", re.IGNORECASE)
                  for p in profiles if p.startswith(("!", "-"))]
    rows = []
    for r in results[:top]:
        size = r.get("size", 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                break
            size /= 1024
        rows.append({"id": result_id(r), "title": r.get("title", "?"), "seeds": r.get("seeders", 0),
                     "size": f"{size:.1f}{unit}", "indexer": r.get("indexer", "?"),
                     "quality": (any(q in r.get("title", "").lower() for q in qualities)
                                 and not any(p.search(r.get("title", "")) for p in exclusions)),
                     "url": r.get("downloadUrl") or r.get("magnetUrl") or ""})
    return rows


def search_display(query, indexer_id=None, categories=None, top=5):
    errors = {}
    results = search(query, indexer_id, categories, errors=errors)
    if not results:
        print("  search incomplete: provider errors" if errors else "  no results")
        return results

    rows = result_rows(results, top=top)
    details_width = max((len(f"{r['size']:>9}  {r['seeds']:>7,} seeds  {r['id']}")
                         for r in rows), default=0)
    title_width = max(1, min(60, get_terminal_size((120, 24)).columns - details_width - 4))
    for r in rows:
        title = r["title"]
        if len(title) > title_width:
            title = title[:title_width - 1] + "…"
        print(f"  {title:<{title_width}}  {r['size']:>9}  {r['seeds']:>7,} seeds  {r['id']}")
    return results


