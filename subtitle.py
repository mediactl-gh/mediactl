import os
import re
import json
import requests
from pathlib import Path
from datetime import datetime
from media import _config


WYZIE_BASE = "https://sub.wyzie.ru"


# ── Config ─────────────────────────────────────────────────────────────────────
def languages():
    return _config("subtitle")["languages"]


# ── Search ─────────────────────────────────────────────────────────────────────
def search(tmdb_id, media_type="movie", season=None, episode=None, language=None):
    langs   = [language] if language else languages()
    results = []
    for lang in langs:
        params = {"id": tmdb_id, "language": lang}
        if season:  params["season"]  = season
        if episode: params["episode"] = episode
        resp = requests.get(f"{WYZIE_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        results += data if isinstance(data, list) else []
    name = re.sub(r"[^\w]", "_", str(tmdb_id))[:60]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sub = Path(".out") / "media"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"{ts}_subtitle_{name}.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results

def search_display(tmdb_id, media_type="movie", season=None, episode=None):
    results = search(tmdb_id, media_type, season, episode)
    if not results:
        print("  no subtitles found"); return results
    for i, r in enumerate(results[:20], 1):
        lang    = r.get("language", "?")
        display = r.get("display", r.get("releaseName", "?"))
        fmt     = r.get("format", "srt")
        hi      = " [HI]" if r.get("isHearingImpaired") else ""
        print(f"  [{i:>2}] [{lang}] [{fmt}]{hi}  {display}")
    return results


# ── Download ───────────────────────────────────────────────────────────────────
def download(result, dest_path):
    """Download subtitle and place it alongside the media file."""
    url  = result.get("url")
    lang = result.get("language", "und")
    fmt  = result.get("format", "srt")

    if not url:
        print("  ERROR: no download url in result"); return

    content = requests.get(url).content
    base    = os.path.splitext(dest_path)[0]
    out     = f"{base}.{lang}.{fmt}"

    with open(out, "wb") as f:
        f.write(content)
    print(f"  + {os.path.basename(out)}")
    return out


# ── Ops ────────────────────────────────────────────────────────────────────────
def subtitle_search(tmdb_id, media_type="movie", season=None, episode=None,
                    grab=None, dest=None):
    langs = ", ".join(languages())
    label = f"tmdb:{tmdb_id}"
    if season:  label += f" S{season:02d}"
    if episode: label += f"E{episode:02d}"
    print(f"  searching subtitles: {label}  [{langs}]")

    results = search_display(tmdb_id, media_type, season, episode)

    if grab is not None and results:
        try:
            result = results[int(grab) - 1]
        except IndexError:
            print(f"  ERROR: no result #{grab}"); return
        if not dest:
            print("  ERROR: --dest required to save subtitle"); return
        download(result, dest)

def subtitle_languages():
    print(f"  configured: {', '.join(languages())}  (media.json: subtitle.languages)")
