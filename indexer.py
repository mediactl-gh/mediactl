"""
indexer — torrent search providers

Usage:
  indexer search <query...> [--indexer NAME] [--category NAME]
                 [--grab RESULT_NUMBER] [--path PATH]

Help: help indexer | indexer --help
"""

import argparse
import re
import requests
from urllib.parse import quote
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
                    "size": int(r.get("size") or 0), "indexer": r.get("indexer", "prowlarr"),
                    "magnetUrl": r.get("magnetUrl") or "",
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
    results, seen = [], set()
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
            key = (r.get("magnetUrl", "").split("&")[0].lower()
                   or r.get("downloadUrl") or r.get("title", "").lower())
            if key in seen:
                continue
            seen.add(key)
            results.append(r)
    return sorted(results, key=lambda r: r.get("seeders", 0), reverse=True)

def result_rows(results):
    profiles = [p.strip() for p in media._config("indexer")["quality_profiles"]]
    qualities = [p.lower() for p in profiles if p and not p.startswith(("!", "-"))]
    exclusions = [re.compile(r"(?:^|[\W_])" + re.escape(p[1:].strip()) + r"(?=$|[\W_])", re.IGNORECASE)
                  for p in profiles if p.startswith(("!", "-"))]
    rows = []
    for r in results[:20]:
        size = r.get("size", 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                break
            size /= 1024
        rows.append({"title": r.get("title", "?"), "seeds": r.get("seeders", 0),
                     "size": f"{size:.1f}{unit}", "indexer": r.get("indexer", "?"),
                     "quality": (any(q in r.get("title", "").lower() for q in qualities)
                                 and not any(p.search(r.get("title", "")) for p in exclusions)),
                     "url": r.get("downloadUrl") or r.get("magnetUrl") or ""})
    return rows


def search_display(query, indexer_id=None, categories=None):
    errors = {}
    results = search(query, indexer_id, categories, errors=errors)
    if not results:
        print("  search incomplete: provider errors" if errors else "  no results")
        return results

    for i, r in enumerate(result_rows(results), 1):
        mark = "*" if r["quality"] else " "
        print(f"  [{i:>2}]{mark} {r['seeds']:>4}S  {r['size']:<9}  [{r['indexer']}]  {r['title']}")
    return results


CLI_SCHEMA = {"ops": {
    "search": {"args": "query", "params": ["--indexer", "--category", "--grab", "--path"]},
}}


def cli(argv):
    """Parse indexer command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="indexer", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--indexer"); p.add_argument("--category")
    p.add_argument("--grab", type=int); p.add_argument("--path")
    args = p.parse_args(argv)
    import torrent

    a = args.action
    if a == "search":
        if not args.args: print("ERROR: query required"); return
        results = search_display(" ".join(args.args),
                      indexer_id=args.indexer, categories=args.category)
        if args.grab and results:
            try:
                r    = results[args.grab - 1]
                link = r.get("magnetUrl") or r.get("downloadUrl")
                if link: torrent.torrent_add(link, args.path)
                else:    print("  ERROR: no download link")
            except IndexError:
                print(f"  ERROR: no result #{args.grab}")
