"""
library — local libraries, collection rules, and player sync

Usage:
  library list [name|path] [--query TEXT] [--stats]
  library add <name> <type> <path> [--role library|collection|download]
              [--lists JSON] [--rule KEY=VALUE ...] [--rule-genres LIST] [--rule-quality N]
  library delete <name> [--wipe]
  library delete-entry <path>
  library rules [name|path]
  library sync-all
  library sync-library <name> [--refresh]
  library sync-player [player] [--player NAME] [--no-delete]
  library sync-lists | sync-watchlist | sync-meta
  library watchlist list|add <JSON>|remove <key>
  library sync-recommendations
  library sync-torrent [--limit N] [--link]
  library sync-folder <path> [--link]

Use either a positional player or --player for sync-player.

Help: help library | library --help
"""

import argparse
import os
import re
import json
import time
import threading
import contextvars
import shutil
import tempfile
import concurrent.futures
import io
import subprocess
import sys
from pathlib import Path

from logui import logui
import torrent

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm"}
SUB_EXT   = {".srt", ".ass", ".sub", ".vtt"}

LIBRARY_CONFIG = Path(".out") / "library.json"
RECENTLY_ADDED_PATH = "media/recently_added.json"
WATCHLIST_PATH = Path(".out/media/watchlist.json")
_WATCHLIST_LOCK = threading.RLock()
LIBRARY_DEFAULTS = {
    "refresh_interval": 60,
    "lists_refresh_interval": 3600,
    "meta_refresh_interval": 300,
    "show_active_downloads": True,
    "show_recently_downloaded": True,
    "libraries": {
        "Recently added": {"type": "", "path": RECENTLY_ADDED_PATH, "role": "file", "max": 12},
        "Movies":   {"type": "movie", "path": "Library/Movies", "rule": {}, "role": "library"},
        "TV":       {"type": "tv",    "path": "Library/TV",     "rule": {}, "role": "library"},
        "Popular":  {"type": "movie", "path": "Library/Popular", "rule": {}, "role": "collection",
                     "lists": [
                         {"source": "tmdb", "url": "https://api.themoviedb.org/3/movie/popular"},
                         {"source": "tmdb", "url": "https://api.themoviedb.org/3/tv/popular"},
                     ]},
        "Trending": {"type": "movie", "path": "Library/Trending", "rule": {}, "role": "collection",
                     "lists": [
                         {"source": "tmdb", "url": "https://api.themoviedb.org/3/trending/movie/week"},
                         {"source": "tmdb", "url": "https://api.themoviedb.org/3/trending/tv/week"},
                     ]},
    },
}

# ── Public API ─────────────────────────────────────────────────────────────────
def add_library(name, lib_type, path, rule=None, role="library", lists=None):
    """Add a directory to the library configuration, creating it if needed."""
    if not path:
        raise ValueError("library path is required")
    os.makedirs(path, exist_ok=True)
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})
    entry = {"type": lib_type, "path": path, "role": role, "lists": list(lists or []), "created": time.time()}
    if rule:
        entry["rule"] = {k: v for k, v in rule.items() if v}
    libraries[name] = entry
    _save_cfg({**cfg_data, "libraries": libraries})
    logui("library.add", name=name, type=lib_type, path=path, role=role, rule=entry.get("rule") or {})


def delete_entry(path):
    """Delete a single entry (folder) from a library."""
    path = os.path.normpath(path)
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})
    allowed = {os.path.normpath(info.get("path", "")) for info in libraries.values()}
    parent = os.path.normpath(os.path.dirname(path))
    if parent not in allowed:
        print(f"ERROR: path not inside a library: {path}"); return False
    if not os.path.isdir(path):
        print(f"ERROR: path not found: {path}"); return False
    if os.path.islink(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)
    logui("library.delete-entry", path=path)
    return True


def remove_library(name, wipe=False):
    """Remove a configured library. Optionally wipe its filesystem folder."""
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})
    if name not in libraries:
        print(f"ERROR: library not found: {name}"); return
    info = libraries[name]
    path = info.get("path", "")
    libraries = {k: v for k, v in libraries.items() if k != name}
    _save_cfg({**cfg_data, "libraries": libraries})
    logui("library.remove", name=name)
    print(f"  - {name}")
    if wipe and path and os.path.exists(path):
        shutil.rmtree(path)
        logui("library.wipe", path=path)
        print(f"  wiped: {path}")


def select_folder(path: str = "") -> str:
    script = """
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
path = filedialog.askdirectory(parent=root, title='Select library folder', initialdir=sys.argv[1] or None, mustexist=True)
root.destroy()
print(path)
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, path],
        capture_output=True, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("Could not open native folder picker")
    return result.stdout.strip()


def run_sync_command(args) -> bool:
    if args[:1] != ["library"] or args[1:2] not in (["sync-all"], ["sync-library"], ["sync-meta"]):
        return False
    action = args[1]
    refresh = action == "sync-library" and "--refresh" in args[2:]
    op_args = [arg for arg in args[2:] if arg != "--refresh"] if refresh else args[2:]
    if len(op_args) != (1 if action == "sync-library" else 0) or args[2:].count("--refresh") > 1:
        raise ValueError("invalid library sync arguments")
    fn = {"sync-all": sync_all, "sync-library": sync_library, "sync-meta": _library_sync_meta}[action]
    fn(*op_args, **({"refresh": refresh} if action == "sync-library" else {}))
    return True


def browse(path: str, offset: int = 0, limit: int = 0, files: bool = False) -> dict:
    import media as _media
    import torrent as _torrent
    from datetime import datetime

    existing = _media.existing_media_titles()
    if os.path.isfile(path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            entries = data.get("items", []) if isinstance(data, dict) else data
            if not isinstance(entries, list):
                entries = []
            def added_time(entry):
                value = entry.get("added", 0) if isinstance(entry, dict) else 0
                try:
                    return float(value)
                except (TypeError, ValueError):
                    try:
                        return datetime.fromisoformat(str(value)).timestamp()
                    except ValueError:
                        return 0
            entries.sort(key=added_time, reverse=True)
        except Exception:
            entries = []
        total = len(entries)
        if limit > 0:
            entries = entries[offset:offset + limit]
        items = []
        for entry in entries:
            if isinstance(entry, str):
                entry = {"name": entry, "path": ""}
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "")
            info = _media.cached_info(name)
            items.append({"name": name, "path": entry.get("path", ""),
                          "added": entry.get("added"), "info": info,
                          "action": _media.media_action((info or {}).get("title") or name, existing)})
        return {"path": path, "items": items, "total": total}
    if not os.path.isdir(path):
        raise FileNotFoundError(f"path not found: {path}")
    entries = sorted((e for e in os.scandir(path) if e.is_dir() or (files and e.is_file())),
                     key=lambda e: e.stat().st_mtime, reverse=True)
    torrent_labels = {}
    if files:
        try:
            for torrent in _torrent._get_all("all"):
                tags = [tag.strip() for tag in (torrent.get("tags") or "").split(",") if tag.strip()]
                content_path = torrent.get("content_path") or ""
                if tags and content_path:
                    torrent_labels[os.path.normcase(os.path.normpath(content_path))] = tags[0]
        except Exception:
            pass
    total = len(entries)
    if limit > 0:
        entries = entries[offset:offset + limit]
    items = []
    for entry in entries:
        label = torrent_labels.get(os.path.normcase(os.path.normpath(entry.path))) if files else entry.name
        info = _media.cached_info(label) if label else None
        items.append({"name": label or entry.name, "path": entry.path,
                      "added": datetime.fromtimestamp(entry.stat().st_mtime).isoformat(), "info": info,
                      "action": "play"})
    return {"path": path, "items": items, "total": total}


def list_libraries(enrich: bool = False) -> list[dict]:
    import media as _media
    import torrent as _torrent

    raw = _load_cfg()
    libraries = raw.get("libraries") or {}

    completed_paths = []
    torrent_ok = False
    has_download = any(i.get("role") == "download" for i in libraries.values())
    if has_download:
        try:
            torrent_state = json.loads(_torrent._ACTIVE_F.read_text(encoding="utf-8"))
            torrent_ok = bool(torrent_state.get("available"))
            completed_paths = [os.path.normpath(t.get("path", ""))
                               for t in torrent_state.get("items", [])
                               if t.get("progress", 0) >= 100 and t.get("path")]
        except Exception:
            pass

    result = []
    for name in sorted(libraries):
        info = libraries[name]
        p = info.get("path", "")
        role = info.get("role", "library")
        if role == "file" and p and not os.path.isabs(p):
            p = str(Path(__file__).parent / ".out" / p)
        exists = os.path.isdir(p)
        items = empty = populated = 0
        size = 0
        preview = []
        if role == "file":
            exists = os.path.isfile(p)
            if exists and enrich:
                try:
                    data = json.loads(Path(p).read_text(encoding="utf-8"))
                    rows = data.get("items", []) if isinstance(data, dict) else data
                    items = populated = len(rows) if isinstance(rows, list) else 0
                    for row in rows[:3]:
                        if not isinstance(row, dict):
                            continue
                        row_name = row.get("name", "")
                        meta = _media.cached_info(row_name) or {}
                        row_path = row.get("path", "")
                        row_size = row.get("size")
                        if row_size is None and os.path.isdir(row_path):
                            row_size = sum(f.stat().st_size for root, _, _ in os.walk(row_path)
                                           for f in Path(root).iterdir() if f.is_file())
                        preview.append({"name": row_name, "title": meta.get("title"),
                                        "poster": meta.get("poster"), "size": row_size or 0})
                    size = os.path.getsize(p)
                except Exception as e:
                    logui.warn("library.scan", path=p, reason=str(e))
        if exists and enrich:
            if role != "file":
                try:
                    with os.scandir(p) as scan:
                        entries = list(scan)
                except OSError as e:
                    entries = []
                    logui.warn("library.scan", path=p, reason=str(e))
                for e in entries:
                    try:
                        if not e.is_dir():
                            continue
                        items += 1
                        if len(preview) < 3:
                            meta = _media.cached_info(e.name) or {}
                            if meta.get("poster"):
                                preview.append({"name": e.name, "title": meta.get("title"), "poster": meta["poster"]})
                        with os.scandir(e.path) as scan:
                            files = list(scan)
                    except OSError as exc:
                        logui.warn("library.scan", path=e.path, reason=str(exc))
                        continue
                    if files:
                        populated += 1
                    else:
                        empty += 1
                    for f in files:
                        try:
                            if f.is_file():
                                size += f.stat().st_size
                        except OSError as exc:
                            logui.warn("library.scan", path=f.path, reason=str(exc))
        rule = info.get("rule") or {}
        parts = []
        if rule.get("genres"): parts.append(",".join(rule["genres"]))
        if rule.get("quality"): parts.append(f"quality={rule['quality']}")
        entry = {
            "name": name, "path": p, "type": info.get("type", ""),
            "role": role,
            "exists": exists, "items": items, "empty": empty,
            "populated": populated, "size": size,
            "rule": rule, "rule_str": " *" if not parts else ", ".join(parts),
            "last_sync": info.get("last_sync"),
            "lists": info.get("lists", []),
            "preview": preview,
        }
        if role == "download":
            if not torrent_ok:
                entry["pending"] = None
            else:
                np = os.path.normpath(p)
                entry["pending"] = sum(1 for cp in completed_paths
                                       if cp == np or cp.startswith(np + os.sep))
        result.append(entry)
    return result


def list_path(path=None, query=None, stats=False):
    """List titles in a library path, or all configured libraries if path is None."""
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})
    if path is None:
        if not libraries:
            print("  no libraries configured"); return
        detail = []
        for name in sorted(libraries):
            info = libraries[name]
            p = info.get("path", "")
            ptype = info.get("type", "")
            exists = os.path.isdir(p)
            icon = "🎬" if ptype == "movie" else "📺" if ptype == "tv" else "📁"
            items = empty = populated = 0
            size = 0
            if exists:
                entries = [e for e in os.scandir(p) if e.is_dir()]
                items = len(entries)
                empty = sum(1 for e in entries if not any(os.scandir(e.path)))
                populated = items - empty
                size = _dir_stats(p)[1]
            rule = info.get("rule") or {}
            rule_str = _rule_str(rule)
            print(f"  {icon} {name} [{info.get('role', 'library')}] -> {p} ({items} titles, {empty} empty, {populated} populated, {_fmt_size(size)}, rule: {rule_str})")
            detail.append({"name": name, "path": p, "type": ptype, "role": info.get("role", "library"),
                           "exists": exists, "items": items, "empty": empty, "populated": populated,
                           "size": size, "rule": rule, "rule_str": rule_str})
        print(f"  ({len(libraries)} libraries)")
        return

    name, info = _resolve_library(path)
    if info is None:
        print(f"ERROR: library not found: {path}"); return
    path = info.get("path", "")
    if not os.path.exists(path):
        print(f"ERROR: path not found: {path}"); return
    entries = sorted((e for e in os.scandir(path) if e.is_dir()), key=lambda e: e.name)
    if query:
        entries = [e for e in entries if query.lower() in e.name.lower()]

    total = empty = 0
    detail = []
    for e in entries:
        is_empty = not any(os.scandir(e.path))
        total += 1
        if is_empty:
            empty += 1
        marker = "🕳️" if is_empty else "📦"
        files, size = _dir_stats(e.path) if not is_empty else (0, 0)
        print(f"  {marker} {e.name} ({files} files, {_fmt_size(size)})")
        detail.append({"name": e.name, "empty": is_empty, "files": files, "size": size})
    print(f"  ({len(entries)} titles, {empty} empty, {len(entries)-empty} populated)")
    logui("library.list", library=name, path=path, query=query or "", count=len(entries),
          titles=json.dumps([e.name for e in entries]))
    logui("library.stats", library=name, path=path, total=len(entries), empty=empty, populated=len(entries)-empty,
          titles_detail=json.dumps(detail))


def rules(name_or_path=None, apply=False, dry_run=False):
    """Preview which library would catch a path based on type (tv vs movie)."""
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})

    if name_or_path:
        name, info = _resolve_library(name_or_path)
        if info is None and not os.path.exists(name_or_path):
            print(f"ERROR: path not found: {name_or_path}"); return
        src_path = info.get("path") if info else name_or_path
        mtype = "tv" if _is_show_root(src_path) else "movie"
        target, label = resolve_target({"type": mtype})
        print(f"  type:   {mtype}")
        print(f"  target: {target or 'None'}")
        print(f"  label:  {label or 'None'}")
        return

    if not libraries:
        print("  no libraries configured"); return
    for name, info in sorted(libraries.items()):
        role = info.get("role") or "library"
        rule = info.get("rule") or {}
        rule_str = _rule_str(rule)
        lists = ",".join(l.get("source", "?") for l in info.get("lists", [])) or "-"
        print(f"  {name} [{info.get('type')}/{role}] -> {info.get('path')}  rule: {rule_str} lists=[{lists}]")


def _match_rule(item, rule):
    """Apply a collection rule to a fetched list item. Returns True if it passes."""
    if not rule:
        return True
    genres = {g.lower() for g in (rule.get("genres") or [])}
    if genres:
        item_genres = {g.lower() for g in (item.get("genres") or [])}
        if not (genres & item_genres):
            return False
    quality = str(rule.get("quality") or "").lower().rstrip("p")
    if quality:
        item_quality = str(item.get("quality") or "").lower().rstrip("p")
        if item_quality != quality:
            return False
    min_rating = rule.get("min_rating")
    if min_rating:
        try:
            if float(item.get("rating") or 0) < float(min_rating):
                return False
        except (TypeError, ValueError):
            pass
    year = item.get("year")
    try:
        y = int(year) if year else None
    except (TypeError, ValueError):
        y = None
    if rule.get("year_from") or rule.get("year_to"):
        if y is None:
            return False
        if rule.get("year_from") and y < int(rule["year_from"]):
            return False
        if rule.get("year_to") and y > int(rule["year_to"]):
            return False
    lang = rule.get("language")
    if lang:
        if (item.get("language") or item.get("lang") or "").lower() != lang.lower():
            return False
    return True


def sync_collections(items=None, library_name=None, refresh=False):
    """Fetch collection lists, linking available media and placing missing titles."""
    from media import excluded_genres, fetch_list

    exclude = excluded_genres()

    if items is None:
        cfg_data = _load_cfg()
        libraries = cfg_data.get("libraries", {})
        items = []
        from media import _list_name
        for name, info in libraries.items():
            if library_name and name != library_name:
                continue
            target = info.get("path")
            for lst in info.get("lists", []):
                source, url = lst.get("source"), lst.get("url")
                if not source or not url or not target:
                    continue
                resource = Path(".out") / "media" / f"{source}_{_list_name(url)}.json"
                try:
                    rows = (json.loads(resource.read_text(encoding="utf-8"))
                            if not refresh and resource.exists() else fetch_list(source, url, target))
                    for row in rows:
                        if isinstance(row, dict) and not row.get("target"):
                            row["target"] = target
                    items.extend(rows)
                except Exception as e:
                    logui.err("library.sync-lists", source=source, url=url, reason=str(e)[:100])
    items = [item for item in items
             if not ({str(g).strip().lower() for g in (item.get("genres") or [])} & exclude)]
    if not items:
        cfg_data = _load_cfg()
        libraries = cfg_data.get("libraries", {})
        items = []
        logui("library.sync-lists", event="start")
        for name, info in libraries.items():
            if library_name and name != library_name:
                continue
            if not info.get("lists"):
                continue
            target = info.get("path")
            if not target:
                continue
            for lst in info.get("lists", []):
                source = lst.get("source")
                url = lst.get("url")
                if not source or not url:
                    continue
                try:
                    titles = fetch_list(source, url, target)
                    items.extend(titles)
                except Exception as e:
                    logui.err("library.sync-lists", source=source, url=url, reason=str(e)[:100])
        items = [item for item in items
                 if not ({str(g).strip().lower() for g in (item.get("genres") or [])} & exclude)]
        logui("library.sync-lists", event="done", total=len(items))
        _stamp_collection_sync(cfg_data, libraries, library_name)
    if not items:
        _write_existing_titles()
        sync_recommendations()
        return [], 0, set()
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})
    rules_by_target = {os.path.normpath(info.get("path", "")): info.get("rule") or {}
                       for info in libraries.values()}
    collection_targets = {os.path.normcase(os.path.abspath(info["path"]))
                          for info in libraries.values()
                          if info.get("role") == "collection" and info.get("path")}
    new_items, skipped = [], 0
    targets = set()
    for item in items:
        target = item.get("target")
        if not target:
            skipped += 1
            continue
        targets.add(target)
        if not _match_rule(item, rules_by_target.get(os.path.normpath(target))):
            continue
        title  = re.sub(r'[<>:"/\\|?*]', "", (f"{item['title']} - {item['year']}" if item.get("year") else item["title"]).replace(":", " -")).strip(". ")
        if os.path.normcase(os.path.abspath(target)) not in collection_targets:
            skipped += 1
            continue
        media_type = "tv" if item.get("type") in ("tv", "show") else "movie"
        source = next((os.path.join(info["path"], title) for info in libraries.values()
                       if info.get("role", "library") == "library"
                       and info.get("type") == media_type and info.get("path")
                       and os.path.isdir(os.path.join(info["path"], title))), None)
        if source:
            if _link_collection(target, title, source):
                new_items.append(item)
            else:
                skipped += 1
            continue
        if _create_library_placeholder(target, title, item.get("type", "movie"),
                                       item.get("ids", {}).get("tmdb"), item.get("poster")):
            new_items.append(item)
        else:
            skipped += 1
    print(f"  added: {len(new_items)}  skipped: {skipped}")
    _stamp_collection_sync(cfg_data, libraries, library_name)
    _write_existing_titles()
    sync_recommendations()
    return new_items, skipped, targets


def _write_existing_titles():
    from media import _key
    titles = set()
    for info in _load_cfg().get("libraries", {}).values():
        if info.get("role", "library") != "library":
            continue
        path = info.get("path")
        if not path or not os.path.isdir(path):
            continue
        for entry in os.scandir(path):
            if not entry.is_dir():
                continue
            match = re.match(r"^(.*?)\s+-\s+(?:19|20)\d{2}$", entry.name.strip())
            title = match.group(1).strip() if match else entry.name.strip()
            k = _key(title)
            if k and k != "unknown":
                titles.add(k)
    (Path(".out") / "media").mkdir(parents=True, exist_ok=True)
    (Path(".out") / "media" / "existing.json").write_text(
        json.dumps({"titles": sorted(titles)}, indent=2), encoding="utf-8")


def _add_existing_title(title):
    from media import EXISTING, _key, _release_title, existing_media_titles
    titles = existing_media_titles()
    titles.add(_key(_release_title(title)))
    EXISTING.parent.mkdir(parents=True, exist_ok=True)
    EXISTING.write_text(json.dumps({"titles": sorted(titles)}, indent=2), encoding="utf-8")


def _stamp_collection_sync(cfg_data, libraries, library_name=None):
    """Record last_sync for collection-role libraries that have lists configured."""
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    changed = False
    for name, info in libraries.items():
        if library_name and name != library_name:
            continue
        if info.get("role") == "collection" and info.get("lists"):
            if info.get("last_sync") != now:
                libraries.setdefault(name, {})["last_sync"] = now
                changed = True
    if changed:
        cfg_data["libraries"] = libraries
        _save_cfg(cfg_data)


def import_path(src_path, release_name=None, link=False, torrent_hash=None):
    """Move a finished download into the appropriate library using its normalized label."""
    name = release_name or os.path.basename(src_path.rstrip("/\\"))
    is_dir = os.path.isdir(src_path)
    is_file = os.path.isfile(src_path)
    if not is_dir and not is_file:
        return False
    is_episode = is_file and re.search(r"(?i)(?:^|[. _-])s\d{1,2}e\d{1,3}(?:[. _-]|$)", os.path.basename(src_path))
    mtype = "tv" if (is_dir and _is_show_root(src_path)) or is_episode else "movie"
    meta = {"type": mtype}
    release_base = os.path.basename(src_path.rstrip("/\\"))
    release_text = f"{name} {release_base}".lower()
    quality_match = re.search(r"(?<!\d)(2160|1080|720)p?\b", release_text)
    if quality_match:
        meta["quality"] = quality_match.group(1)
    elif re.search(r"(?<!\w)4k(?!\w)", release_text):
        meta["quality"] = "2160"
    try:
        import media
        cached = media.cached_info(name)
        if isinstance(cached, dict):
            meta.update(cached)
            meta["type"] = mtype
    except Exception:
        pass
    target, rule = resolve_target(meta)
    if not target:
        logui.err("library.error", error="no-target", title=name, type=mtype)
        return False
    dest = os.path.join(target, name)
    if is_dir and os.path.normcase(os.path.abspath(src_path)) == os.path.normcase(os.path.abspath(dest)):
        return False
    try:
        os.makedirs(target, exist_ok=True)
        if link:
            try:
                if is_file:
                    os.makedirs(dest, exist_ok=True)
                    os.symlink(src_path, os.path.join(dest, name + os.path.splitext(src_path)[1]))
                else:
                    os.symlink(src_path, dest)
            except OSError as e:
                logui.err("library.error", error="link failed", title=name, reason=str(e))
                return False
        else:
            if torrent_hash:
                import torrent as _torrent
                _torrent._post("/torrents/delete", data={"hashes": torrent_hash, "deleteFiles": "false"})
            if is_file:
                os.makedirs(dest, exist_ok=True)
                dest_file = os.path.join(dest, name + os.path.splitext(src_path)[1].lower())
                if os.path.exists(dest_file):
                    os.remove(dest_file)
                shutil.move(src_path, dest_file)
            else:
                _move_dir(src_path, dest)
        for info in _load_cfg().get("libraries", {}).values():
            if (info.get("role") == "collection" and info.get("path")
                    and os.path.lexists(os.path.join(info["path"], name))):
                _link_collection(info["path"], name, dest)
        try:
            _record_recent(name, dest)
        except Exception as e:
            logui.err("library.recent", title=name, reason=str(e)[:100])
        try:
            _add_existing_title(name)
        except Exception as e:
            logui.err("library.existing", title=name, reason=str(e)[:100])
        logui("library.import", title=name, target=target, rule=rule)
        return True
    except Exception as e:
        logui.err("library.error", error="import failed", title=name, reason=str(e))
        return False


_RECENT_LOCK = threading.Lock()


def _record_recent(name, path):
    """Prepend a successful import to the configured Recently added file."""
    libraries = _load_cfg().get("libraries", {})
    for library_name, info in libraries.items():
        if (library_name.lower() != "recently added"
                or info.get("role") != "file" or not info.get("path")):
            continue
        recent_path = info["path"]
        if not os.path.isabs(recent_path):
            recent_path = os.path.join(Path(".out"), recent_path)
        try:
            limit = max(1, int(info.get("max") or 12))
        except (TypeError, ValueError):
            limit = 12
        with _RECENT_LOCK:
            try:
                with open(recent_path, encoding="utf-8") as f:
                    data = json.load(f)
                items = data.get("items", []) if isinstance(data, dict) else data
                if not isinstance(items, list):
                    items = []
            except (OSError, ValueError):
                items = []
            for item in items:
                if isinstance(item, dict):
                    item.pop("files", None)
            norm_path = os.path.normcase(os.path.normpath(path))
            items = [item for item in items if not (
                isinstance(item, dict) and (
                    os.path.normcase(os.path.normpath(item.get("path", ""))) == norm_path
                    or item.get("name") == name
                )
            )]
            size = _dir_stats(path)[1] if os.path.isdir(path) else os.path.getsize(path)
            items.insert(0, {"name": name, "path": path, "added": time.time(), "size": size})
            os.makedirs(os.path.dirname(recent_path) or ".", exist_ok=True)
            with open(recent_path, "w", encoding="utf-8") as f:
                json.dump({"items": items[:limit]}, f, indent=2)


def watchlist_get():
    """Read the local feed; malformed data must not be silently overwritten."""
    try:
        return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def watchlist_update(item=None, remove=None):
    """Merge one title or remove a consumed entry, preserving concurrent writes."""
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _WATCHLIST_LOCK, WATCHLIST_PATH.with_suffix(".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            items = watchlist_get()
            key = None
            if item is not None:
                title = str(item.get("title") or "").strip()
                if not title or title == "?":
                    raise ValueError("watchlist title is required")
                kind = {"show": "tv", "series": "tv"}.get(item.get("type"), item.get("type") or "movie")
                year = str(item.get("year") or "").replace("?", "")
                ids = {str(k).lower(): str(v) for k, v in (item.get("ids") or {}).items() if v}
                key = f"{kind}:{title.casefold()}:{year}"
                key = next((k for k, old in items.items() if old.get("type") == kind and
                            any(str(old.get("ids", {}).get(provider)) == value for provider, value in ids.items())), key)
                old = items.get(key, {})
                items[key] = {"title": title, "year": year, "type": kind,
                              "ids": {**old.get("ids", {}), **ids}}
            if remove is not None:
                items.pop(remove, None)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=WATCHLIST_PATH.parent,
                                             delete=False) as f:
                json.dump(items, f, indent=2)
            os.replace(f.name, WATCHLIST_PATH)
            return key
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def sync_watchlist():
    """Consume the local feed once titles are queued or already in the library."""
    import indexer
    import media
    items = watchlist_get()
    if not items:
        return 0

    existing = media.existing_media_titles()
    try:
        config = json.loads(media.MEDIA_CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    profiles = config.get("indexer", {}).get("quality_profiles") or media.MEDIA_DEFAULTS["indexer"]["quality_profiles"]
    if not isinstance(profiles, list) or not all(isinstance(p, str) and p.strip() for p in profiles):
        raise ValueError("quality_profiles must be a list of quality strings")
    profiles = [p.strip() for p in profiles]
    exclusions = [p[1:].strip() for p in profiles if p.startswith(("!", "-"))]
    profiles = [p for p in profiles if not p.startswith(("!", "-"))]
    if not profiles or any(not p for p in exclusions):
        raise ValueError("quality_profiles requires a preferred quality and non-empty exclusions")
    exclusions = [re.compile(r"(?:^|[\W_])" + re.escape(p) + r"(?=$|[\W_])", re.IGNORECASE)
                  for p in exclusions]
    queued = None
    added = 0
    for item_id, item in items.items():
        try:
            title, year = item.get("title"), item.get("year")
            if not title or title == "?":
                continue
            if media.media_action(title, existing) == "play":
                watchlist_update(remove=item_id)
                continue
            label = f"{title} - {year}" if year and year != "?" else title
            if queued is None:
                downloads = torrent._get_all()
                if not isinstance(downloads, list):
                    raise ValueError("cannot read torrent queue")
                queued = {tag.strip() for download in downloads
                          for tag in (download.get("tags") or "").split(",") if tag.strip()}
                queued.update(download.get("name", "") for download in downloads)
            if label in queued:
                watchlist_update(remove=item_id)
                continue
            ids = item.get("ids") or {}
            query = ids.get("imdb") or (f"{title} {year}" if year and year != "?" else title)
            errors = {}
            results = indexer.search(query, title=title, year=year, errors=errors)
            expected = re.sub(r"[\W_]+", " ", title).strip().casefold()
            if year and year != "?":
                expected += f" {year}"
            candidate = next((row for quality in profiles for row in results
                              if (re.sub(r"[\W_]+", " ", row.get("title", "")).strip().casefold() + " ")
                              .startswith(expected + " ")
                              and quality.lower() in row.get("title", "").lower()
                              and not any(pattern.search(row.get("title", "")) for pattern in exclusions)
                              and row.get("seeders", 0) > 0
                              and (row.get("magnetUrl") or row.get("downloadUrl"))), None)
            if candidate is None:
                status = "no-matching-candidate" if results else "search-failed" if errors else "no-results"
                logui("library.sync-watchlist", title=title, status=status,
                      results=len(results), failed_providers=",".join(errors))
                continue
            meta = {**item, "label": label,
                    "type": {"show": "tv", "series": "tv"}.get(item.get("type"), item.get("type"))}
            torrent.torrent_add(candidate.get("magnetUrl") or candidate["downloadUrl"], meta=meta)
            queued.add(label)
            added += 1
            watchlist_update(remove=item_id)
        except Exception as exc:
            logui.err("library.sync-watchlist", title=item.get("title"), error=str(exc))
    if added:
        logui("library.sync-watchlist", queued=added)
    return added


def _scan_players():
    try:
        import player
        player.lib_scan(all_players=True)
    except Exception as e:
        logui.err("library.sync.player", reason=str(e))


_torrent_sync_error = None


def sync_torrent(limit=None, link=False, scan=True):
    """One-shot import of all currently completed qBittorrent downloads."""
    global _torrent_sync_error
    imported = 0
    try:
        _pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        _fut = _pool.submit(torrent.completed)
        _dl = time.monotonic() + 30
        while True:
            try:
                completed = _fut.result(timeout=0.5)
                break
            except concurrent.futures.TimeoutError:
                if time.monotonic() > _dl:
                    _fut.cancel()
                    raise
        _pool.shutdown(wait=False)
    except Exception as e:
        _pool.shutdown(wait=False)
        reason = torrent._error_reason(e)
        if reason != _torrent_sync_error:
            logui.err("library.error", error="torrent list failed", reason=reason)
        _torrent_sync_error = reason
        return 0
    if _torrent_sync_error:
        logui("library.sync-torrent", status="recovered")
    _torrent_sync_error = None
    logui("library.import", status="scan", total=len(completed))
    if limit and len(completed) > limit:
        completed = completed[:limit]
    try:
        for t in completed:
            label = next((x for x in (t.get("tags") or []) if x), None)
            if import_path(t["path"], label or t["name"], link=link, torrent_hash=t["hash"]):
                imported += 1
    except KeyboardInterrupt:
        print("\n  stopped")
    if imported and scan:
        _scan_players()
    return imported


def sync_folder(path, link=False):
    imported = 0
    for name in sorted(os.listdir(path)):
        p = os.path.join(path, name)
        if os.path.isdir(p) and import_path(p, name, link=link):
            imported += 1
    if imported:
        _scan_players()


_TYPE_LABEL = {"movie": "Movies", "tv": "TV", "artist": "Music"}
def _player_app_foreground(max_age=45):
    import player
    return bool(player._active_player_tv(player.player_type(), max_age)[0])


def sync_player(library_name=None, delete=True, player=None):
    """Sync one selected backend, or all configured backends."""
    import player as player_module
    for name in ([player] if player else player_module._configured_backends()):
        token = player_module._PLAYER.set(None)
        try:
            player_module.select_player(name)
            print(f"  {player_module.player_type()}:")
            _sync_player(library_name, delete)
        except Exception as e:
            logui.err("library.sync.player", player=name, reason=str(e))
        finally:
            player_module._PLAYER.reset(token)


def _sync_player(library_name=None, delete=True):
    """Sync configured media folders; optionally remove server-only libraries."""
    import player
    from datetime import datetime
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", LIBRARY_DEFAULTS["libraries"])

    wanted = [(name, info.get("type") or "mixed", os.path.abspath(info.get("path")))
              for name, info in libraries.items()
              if info.get("role") not in {"file", "download"}
              and (info.get("type") or info.get("role") == "collection")
              and info.get("path") and (not library_name or name == library_name)]
    for name, info in libraries.items():
        if info.get("role") in {"file", "download"} or (library_name and name != library_name):
            continue
        if not info.get("path"):
            print(f"  {name}: cannot sync — folder path required")
        elif not info.get("type") and info.get("role") != "collection":
            print(f"  {name}: cannot sync — media type required")
    wanted_names = {n for n, _, _ in wanted}

    try:
        existing = {lib["title"]: lib for lib in player.libraries(counts=True)}
    except Exception as e:
        logui.err("library.sync.player", reason=str(e)); return

    before = {name: lib["count"] for name, lib in existing.items()}

    # remove player libraries not in config
    for title, lib in list(existing.items()):
        if delete and not library_name and title not in libraries:
            try:
                player.lib_remove(lib["key"], confirm=False)
                change = f"{-before[title]:+d}" if before[title] is not None else "?"
                print(f"  {title:<16} 0  ({change})  removed")
            except Exception as e:
                print(f"  failed to remove {title}: {e}")

    # add missing libraries
    for name, ltype, path in wanted:
        locations = {os.path.normcase(os.path.normpath(p)) for p in existing.get(name, {}).get("locations", [])}
        if name in existing and (locations != {os.path.normcase(os.path.normpath(path))}
                                 or existing[name].get("type") != ltype):
            try:
                player.lib_remove(existing[name]["key"], confirm=False)
                existing.pop(name, None)
            except Exception as e:
                print(f"  failed to update {name}: {e}")
                wanted_names.discard(name)
                continue
        if name in existing:
            continue
        try:
            os.makedirs(path, exist_ok=True)
            player.lib_add(name, ltype, path, refresh=False)
        except Exception as e:
            print(f"  {name}: failed — {e}")
            wanted_names.discard(name)
    try:
        key = existing.get(library_name, {}).get("key") if library_name else None
        player.lib_scan(key)
        existing = {lib["title"]: lib for lib in player.libraries(counts=True)}
    except Exception as e:
        logui.err("library.sync.player", reason=str(e)); return

    for name, lib in existing.items():
        if libraries.get(name, {}).get("role") in {"file", "download"}:
            continue
        if library_name and name != library_name:
            continue
        count = lib["count"]
        pending = "  scanning" if lib["scanning"] else ""
        previous = before.get(name, 0)
        change = f"{count - previous:+d}" if count is not None and previous is not None else "?"
        print(f"  {name:<16} {count if count is not None else '?'}  ({change}){pending}")

    # stamp last_sync for libraries now present in the player
    synced = wanted_names & existing.keys()
    if synced:
        now = datetime.now().isoformat(timespec="seconds")
        for name in synced:
            libraries[name]["last_sync"] = now
        _save_cfg(cfg_data)
    logui("library.sync.player", player=player.player_type(), synced=len(synced))


# ── Internal: config ───────────────────────────────────────────────────────────
def _load_cfg():
    if not LIBRARY_CONFIG.exists():
        _save_cfg({})
    try:
        data = json.loads(LIBRARY_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return dict(LIBRARY_DEFAULTS)
    merged = {**LIBRARY_DEFAULTS, **data}
    changed = any(key not in data for key in LIBRARY_DEFAULTS)
    recent = merged.get("libraries", {}).get("Recently added")
    if recent and recent.get("path") == "recently_added.json":
        recent["path"] = RECENTLY_ADDED_PATH
        old_path = Path(".out") / "recently_added.json"
        new_path = Path(".out") / RECENTLY_ADDED_PATH
        if old_path.exists() and not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(old_path, new_path)
        changed = True
    if changed:
        LIBRARY_CONFIG.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def _save_cfg(data):
    Path(".out").mkdir(parents=True, exist_ok=True)
    merged = {**LIBRARY_DEFAULTS, **data}
    LIBRARY_CONFIG.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def sync_torrent_download_path():
    """Read qBittorrent's save_path and add it as a download library if not already registered."""
    if not LIBRARY_CONFIG.exists():
        _save_cfg({})
    try:
        import torrent as _torrent
        client_path = _torrent._get_download_path()
        if not client_path:
            return
        cfg_data = _load_cfg()
        libraries = cfg_data.get("libraries", {})
        already = any(
            info.get("role") == "download" and os.path.normpath(info.get("path", "")) == os.path.normpath(client_path)
            for info in libraries.values()
        )
        if not already:
            base = "Downloads"
            name = base
            i = 2
            while name in libraries:
                name = f"{base} {i}"
                i += 1
            add_library(name, "", client_path, role="download")
    except Exception:
        pass


# ── Internal: placeholder ──────────────────────────────────────────────────────
def _place(placeholder, folder, filename):
    os.makedirs(folder, exist_ok=True)
    shutil.copy2(placeholder, os.path.join(folder, filename))


def _create_library_placeholder(target, title, media_type="movie", tmdb_id=None, poster=None):
    import media
    import requests
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    name = re.sub(r'[<>:"/\\|?*]', "", title.replace(":", " -")).strip(". ")
    root = Path(target) / name
    if not name or media_type not in {"movie", "show", "tv"}:
        return False
    if os.path.normcase(os.path.realpath(root)) != os.path.normcase(os.path.abspath(root)):
        return False
    videos = [p for p in root.rglob("*") if p.suffix.lower() in VIDEO_EXT and p.is_file()]
    if ((root / "poster.jpg").exists() and videos
            and all((p.parent / "poster.jpg").exists() for p in videos)):
        return False
    cached = media.cached_info(str(tmdb_id) if tmdb_id else title) or media.cached_info(title) or {}
    poster = poster or cached.get("poster")
    if not videos:
        seasons = []
        if media_type != "movie":
            seasons = [{"season_number": s["number"]} for s in cached.get("season_details", [])]
            if not seasons and tmdb_id:
                try:
                    seasons = media.tmdb_seasons(tmdb_id)
                except Exception:
                    pass
        videos = ([root / f"Season {s['season_number']:02d}" /
                   f"{name} - S{s['season_number']:02d}E00.mp4" for s in seasons]
                  if seasons else [root / (f"{name}.mp4" if media_type == "movie" else f"{name} - S00E00.mp4")])
    try:
        if not poster:
            raise ValueError("artwork not cached yet")
        response = requests.get(poster, timeout=15)
        response.raise_for_status()
        image = ImageOps.fit(Image.open(io.BytesIO(response.content)).convert("RGB"), (600, 900))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 744, 600, 900), fill="#111111")
        draw.multiline_text((300, 822), "AVAILABLE TO\nDOWNLOAD", anchor="mm", align="center",
                            font=ImageFont.load_default(size=46), fill="white", stroke_width=1, spacing=10)
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".placeholder-", dir=root) as pending:
            art, clip = Path(pending) / "poster.jpg", Path(pending) / "clip.mp4"
            image.save(art, quality=90)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(art),
                            "-t", "5", "-r", "24", "-an", "-c:v", "libx264", "-tune", "stillimage",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(clip)],
                           check=True, capture_output=True, timeout=45)
            for video in videos:
                _place(clip, video.parent, video.name)
            for folder in {root, *(p.parent for p in videos)}:
                shutil.copy2(art, folder / "poster.jpg")
        print(f"  + {name} (available to download)")
        return True
    except Exception as e:
        logui.err("library.placeholder", title=title, reason=str(e)[:150])
        return False


# ── Internal: scan / list helpers ──────────────────────────────────────────────
def _dir_stats(path):
    count = total = 0
    try:
        for root, _, files in os.walk(path):
            count += len(files)
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
    except Exception:
        pass
    return count, total


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if isinstance(n, float) else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _resolve_library(name_or_path):
    """Return (name, info) for a library by name or path."""
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})
    if name_or_path in libraries:
        return name_or_path, libraries[name_or_path]
    for name, info in libraries.items():
        if os.path.normpath(info.get("path", "")) == os.path.normpath(name_or_path):
            return name, info
    return None, None


def _rule_str(rule):
    if not rule: return "*"
    parts = []
    if rule.get("genres"): parts.append("genres=" + ",".join(rule["genres"]))
    if rule.get("quality"): parts.append("quality=" + rule["quality"])
    if rule.get("min_rating"): parts.append(f"rating>={rule['min_rating']}")
    if rule.get("year_from"): parts.append(f"year>={rule['year_from']}")
    if rule.get("year_to"): parts.append(f"year<={rule['year_to']}")
    if rule.get("language"): parts.append("lang=" + rule["language"])
    return "+".join(parts) if parts else "*"


# ── Internal: import helpers ───────────────────────────────────────────────────
def _link_collection(target, name, source):
    """Point a managed collection entry at the authoritative media directory."""
    root = Path(target).resolve()
    dest = root / name
    source = Path(source).resolve()
    if dest.parent != root:
        raise ValueError("collection entry must be directly inside its library")
    if dest.resolve() == source:
        return False
    if source.is_relative_to(dest) or not source.is_dir():
        raise ValueError("collection source must be an existing separate directory")
    root.mkdir(parents=True, exist_ok=True)
    # Create the new link before removing the old entry.
    with tempfile.TemporaryDirectory(dir=root) as pending:
        staged = Path(pending) / "entry"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(source), str(staged))
        else:
            staged.symlink_to(source, target_is_directory=True)
        if dest.is_symlink():
            dest.unlink()
        elif dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists():
            dest.unlink()
        os.replace(staged, dest)
    return True


def resolve_target(meta):
    """Prefer the most specific matching rule, then fall back by media type."""
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", LIBRARY_DEFAULTS["libraries"])
    mtype = meta.get("type") if meta else None

    candidates = [
        (name, info) for name, info in libraries.items()
        if info.get("role", "library") == "library" and info.get("type") == mtype
    ]
    specific = [(name, info) for name, info in candidates if info.get("rule")]
    specific.sort(key=lambda item: (
        -sum(bool(item[1].get("rule", {}).get(key)) for key in
             ("genres", "quality", "min_rating", "year_from", "year_to", "language")),
        item[0].lower(),
    ))
    for name, info in specific:
        rule = info.get("rule") or {}
        if _match_rule(meta or {}, rule):
            return info.get("path"), f"{name}:{_rule_str(rule)}"

    for name, info in sorted(candidates, key=lambda item: item[0].lower()):
        if not info.get("rule"):
            return info.get("path"), f"{name}:fallback"
    return None, None


def resolve_download(meta):
    """Pick a configured download path, or None to use the torrent client's default."""
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})
    mtype = meta.get("type") if meta else None
    by_age = sorted(libraries.items(), key=lambda x: x[1].get("created", 0), reverse=True)
    for name, info in by_age:
        if info.get("role") == "download" and info.get("path") and info.get("type") in (mtype, None, ""):
            return info.get("path")
    for name, info in by_age:
        if info.get("role") == "download" and info.get("path"):
            return info.get("path")
    return None


def _is_show_root(path):
    """True if the directory contains Season subfolders."""
    if not os.path.isdir(path):
        return False
    for entry in os.scandir(path):
        if entry.is_dir() and entry.name.lower().startswith("season"):
            return True
    return False


def _move_dir(src, dest):
    """Move src directory to dest, overwriting dest if it exists. Remove empty source dir."""
    if os.path.normpath(src) == os.path.normpath(dest):
        return
    if os.path.exists(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(src, dest)
    src_dir = os.path.dirname(src)
    if os.path.isdir(src_dir) and not any(os.scandir(src_dir)):
        os.rmdir(src_dir)


# ── Onboard hooks ──────────────────────────────────────────────────────────────
_library_sync_meta_lock = threading.Lock()
_SYNC_META_CONCURRENCY = 1
_SYNC_META_SLEEP = 1.0
_META_MISSING_FILE = Path(".out/media/missing.json")
_META_MAX_ATTEMPTS = 3
_META_RESET_SECONDS = 3 * 24 * 60 * 60


def _reset_metadata_missing():
    _META_MISSING_FILE.unlink(missing_ok=True)


def _library_refresh():
    """Periodic fast sync (every 30s): register download path, import completed
    torrents, and sync the configured player while its TV app is in the foreground.
    List refresh and metadata fetch have their own hooks."""
    cfg_data = _load_cfg()
    libraries = cfg_data.get("libraries", {})
    changed = False

    for name, info in LIBRARY_DEFAULTS["libraries"].items():
        if name not in libraries:
            libraries[name] = info.copy()
            changed = True

    if changed:
        cfg_data["libraries"] = libraries
        _save_cfg(cfg_data)
        logui("library.default-lists", event="seeded")
    for info in libraries.values():
        if info.get("role", "library") in {"library", "collection"} and info.get("path"):
            try:
                os.makedirs(info["path"], exist_ok=True)
            except OSError as e:
                logui.err("library.defaults", path=info["path"], reason=str(e))

    fns = [sync_torrent_download_path, sync_torrent] if torrent.configured() else []
    import player
    fns.extend((player.sync_watchlist, sync_watchlist))
    if _player_app_foreground():
        fns.append(sync_player)
    for fn in fns:
        try:
            if fn is sync_torrent:
                fn(scan=sync_player not in fns)
            else:
                fn()
        except Exception as e:
            logui.err("library.refresh", fn=fn.__name__, reason=str(e)[:100])


def sync_recommendations(cap=60):
    import media
    selected = media.recommendations_config().get("lists")
    if not isinstance(selected, list) or "all" in selected:
        selected = None
    exclude = media.excluded_genres("recommendations")
    pool = []
    for name in media.recommendation_lists():
        if selected is not None and name not in selected:
            continue
        try:
            rows = json.loads((media._MEDIA_DIR / name).read_text(encoding="utf-8"))
            if isinstance(rows, list):
                pool.extend({**item, "resource": name} for item in rows if isinstance(item, dict))
        except (OSError, ValueError):
            continue
    for info in _load_cfg().get("libraries", {}).values():
        if info.get("role") != "collection" or not os.path.isdir(info.get("path", "")):
            continue
        resources = {f"{lst['source']}_{media._list_name(lst['url'])}.json"
                     for lst in info.get("lists", []) if lst.get("source") and lst.get("url")}
        if selected is not None and not resources.intersection(selected):
            continue
        with os.scandir(info["path"]) as entries:
            for entry in entries:
                item = media.cached_info(entry.name) if entry.is_dir() else None
                if item:
                    ids = {provider: item[f"{provider}_id"] for provider in ("tmdb", "trakt", "cinemeta")
                           if item.get(f"{provider}_id")}
                    pool.append({**item, "ids": ids, "target": info["path"], "resource": ""})
    seen, feed = set(), []
    candidates = pool
    movies = [item for item in candidates if isinstance(item, dict) and item.get("type") not in ("tv", "show")]
    shows = [item for item in candidates if isinstance(item, dict) and item.get("type") in ("tv", "show")]
    ordered = [item for i in range(max(len(movies), len(shows)))
               for item in ((movies[i] if i < len(movies) else None),
                            (shows[i] if i < len(shows) else None)) if item is not None]
    for it in ordered:
        if not isinstance(it, dict):
            continue
        if ({str(g).strip().lower() for g in (it.get("genres") or [])} & exclude):
            continue
        ids = it.get("ids") if isinstance(it.get("ids"), dict) else {}
        provider = "tmdb" if ids.get("tmdb") is not None else "trakt" if ids.get("trakt") is not None else "cinemeta" if ids.get("cinemeta") else "title"
        ref_id = ids.get(provider) if provider != "title" else media._key(it.get("title"), it.get("year"))
        media_type = "tv" if it.get("type") == "show" else it.get("type")
        key = (media._key(it.get("title"), it.get("year")), media_type)
        if key in seen:
            continue
        seen.add(key)
        feed.append({
            "ref": {
                "provider": provider,
                "id": ref_id,
                "type": media_type,
                "title": it.get("title"),
                "year": it.get("year"),
            },
            "context": {"list": os.path.basename(str(it.get("target") or "")),
                        "resource": it["resource"]},
        })
        if len(feed) >= cap:
            break
    media._MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    media.RECOMMENDATIONS.write_text(json.dumps({"version": 1, "items": feed}, indent=2), encoding="utf-8")
    logui("media.recommendations", event="done", total=len(feed))


def sync_all():
    """Full on-demand sync: register download path, import completed torrents,
    sync player, refresh collection lists, and fetch missing metadata."""
    import player
    for fn in (sync_torrent_download_path, sync_torrent, player.sync_watchlist, sync_watchlist, sync_player, sync_collections, _library_sync_meta):
        try:
            if fn is sync_torrent:
                fn(scan=False)
            else:
                fn()
        except Exception as e:
            logui.err("library.refresh", fn=fn.__name__, reason=str(e)[:100])


def sync_library(library_name, refresh=False):
    info = _load_cfg().get("libraries", {}).get(library_name)
    if not info:
        raise Exception(f"library not found: {library_name}")
    role = info.get("role", "library")
    if role == "collection":
        sync_collections(library_name=library_name, refresh=refresh)
    elif role == "download":
        sync_torrent()
    elif role == "library":
        sync_torrent()
        sync_player(library_name)
    _library_sync_meta(library_name)
    logui("library.sync", library=library_name, role=role)


def _library_sync_meta_worker(_media, library_name=None):
    """Scan libraries and fetch missing metadata one-by-one."""
    provider = _media._available_source(_media._config("info").get("source", "tmdb"))
    now = time.time()
    try:
        state = json.loads(_META_MISSING_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("provider") != provider or now - state.get("reset_at", 0) >= _META_RESET_SECONDS:
        state = {"provider": provider, "reset_at": now, "items": {}}
        _reset_metadata_missing()
    attempts = state["items"]
    seen = set()
    missing = []
    list_types = {}
    for source in ("tmdb", "trakt", "cinemeta"):
        for resource in (Path(".out") / "media").glob(f"{source}_*.json"):
            try:
                for item in json.loads(resource.read_text(encoding="utf-8")):
                    title = item.get("title")
                    if not title:
                        continue
                    media_type = "tv" if item.get("type") in ("tv", "show") else "movie"
                    list_types[_media._key(title, item.get("year"))] = media_type
                    list_types.setdefault(_media._key(title), media_type)
            except Exception:
                pass
    for name, info in _load_cfg().get("libraries", {}).items():
        if library_name and name != library_name:
            continue
        p = info.get("path", "")
        default_type = info.get("type", "movie") or "movie"
        if not os.path.isdir(p):
            continue
        try:
            with os.scandir(p) as scan:
                entries = list(scan)
        except OSError as ex:
            logui.warn("library.scan", path=p, reason=str(ex))
            continue
        for e in entries:
            try:
                if not e.is_dir():
                    continue
            except OSError:
                continue
            if e.name in seen:
                continue
            seen.add(e.name)
            mt = list_types.get(_media._key(e.name), default_type)
            cached = _media.cached_info(e.name)
            key = f"{mt}:{e.name}"
            if cached and cached.get("poster") and (cached.get("type") == mt or not cached.get("type")):
                attempts.pop(key, None)
                continue
            if attempts.get(key, 0) < _META_MAX_ATTEMPTS:
                missing.append((e.name, mt))
    fetched = 0
    newly_missing = 0
    for title, mt in missing:
        key = f"{mt}:{title}"
        newly_missing += key not in attempts
        attempts[key] = attempts.get(key, 0) + 1
        try:
            _media.lookup(title, media_type=mt, refresh=True, source=provider)
            cached = _media.cached_info(title)
            if cached and cached.get("poster") and (cached.get("type") == mt or not cached.get("type")):
                attempts.pop(key, None)
                fetched += 1
        except Exception:
            pass  # Failed lookups count toward the same three-attempt limit.
        _META_MISSING_FILE.parent.mkdir(parents=True, exist_ok=True)
        _META_MISSING_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        if _SYNC_META_SLEEP:
            time.sleep(_SYNC_META_SLEEP)
    if not missing and _META_MISSING_FILE.exists():
        _META_MISSING_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if newly_missing or fetched:
        logui("library.sync-meta", event="done", fetched=fetched, missing=len(attempts))


def _library_sync_meta(library_name=None):
    """Scan library folders and fetch missing metadata."""
    import media as _media
    if not _library_sync_meta_lock.acquire(blocking=False):
        return  # previous worker still running — skip silently
    def _work():
        try:
            _library_sync_meta_worker(_media, library_name)
        except Exception as ex:
            logui.err("library.sync-meta", error=str(ex)[:100])
        finally:
            _library_sync_meta_lock.release()
    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(_work,), daemon=True).start()




CLI_SCHEMA = {"ops": {
    "list": {"args": "[name|path]", "params": ["--query", "--stats"]},
    "add": {"args": "name type path", "params": ["--role", "--lists", "--rule-genres", "--rule-quality", "--rule"]},
    "delete": {"args": "name", "params": ["--wipe"]},
    "delete-entry": {"args": "path"},
    "rules": {"args": "[name|path]"},
    "sync-library": {"args": "name", "params": ["--refresh"]},
    "sync-player": {"args": "[player]", "params": ["--player", "--no-delete"]},
    "sync-lists": {},
    "sync-watchlist": {},
    "watchlist": {"args": "list|add <JSON>|remove <key>"},
    "sync-torrent": {"params": ["--limit", "--link"]},
    "sync-folder": {"args": "path", "params": ["--link"]},
    "sync-meta": {},
    "sync-all": {},
    "sync-recommendations": {},
}}


def cli(argv):
    """Parse library command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="library", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--query")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--wipe", action="store_true", help="remove non-empty list target folders")
    p.add_argument("--no-delete", action="store_true", help="preserve server-only libraries during sync-player")
    p.add_argument("--refresh", action="store_true", help="fetch remote collection lists during sync-library")
    p.add_argument("--player", type=str.lower, help="sync-player backend (plex/jellyfin) or player name; defaults to all configured backends")
    p.add_argument("--limit", type=int, help="max torrents to import")
    p.add_argument("--link", action="store_true", help="symlink instead of move")
    p.add_argument("--role", default="library", choices=["library", "collection", "download"])
    p.add_argument("--lists", help="collection library lists as JSON array of {source,url}")
    p.add_argument("--rule-genres", help="library rule: comma-separated genre names (e.g. 'Animation,Family')")
    p.add_argument("--rule-quality", help="library rule: quality filter (720|1080|2160)")
    p.add_argument("--rule", action="append", metavar="k=v", help="rule as key=value pairs, repeatable (e.g. --rule min_rating=7 --rule year_from=2020)")
    args = p.parse_args(argv)

    a = args.action
    if   a == "list":
        list_path(args.args[0] if args.args else None, args.query, args.stats)
    elif a == "add":
        if len(args.args) < 3: print("ERROR: library add <name> <type> <path> [--role ... --rule k=v ...]"); return
        if args.role == "library" and (not args.args[1].strip() or "," in args.args[1]):
            print("ERROR: role=library needs a single type (movie|tv|artist)"); return
        rule = {}
        if args.rule:
            for kv in args.rule:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    k = k.strip()
                    # coerce types
                    if k in ("min_rating",):
                        try: rule[k] = float(v)
                        except ValueError: pass
                    elif k in ("year_from", "year_to"):
                        try: rule[k] = int(v)
                        except ValueError: pass
                    else:
                        rule[k] = v
        if args.rule_genres: rule["genres"] = [g.strip() for g in args.rule_genres.split(",") if g.strip()]
        if args.rule_quality: rule["quality"] = args.rule_quality
        lists = None
        if args.lists:
            try: lists = json.loads(args.lists)
            except Exception: pass
        add_library(args.args[0], args.args[1], args.args[2], rule=rule or None, role=args.role, lists=lists)
    elif a == "delete":
        if not args.args: print("ERROR: library delete <name> [--wipe]"); return
        remove_library(args.args[0], wipe=args.wipe)
    elif a == "delete-entry":
        if not args.args: print("ERROR: library delete-entry <path>"); return
        delete_entry(args.args[0])
    elif a == "rules":
        rules(args.args[0] if args.args else None)
    elif a == "sync-library":
        if not args.args: print("ERROR: library sync-library <name>"); return
        sync_library(args.args[0], refresh=args.refresh)
    elif a == "sync-player":
        if len(args.args) > 1 or (args.args and args.player):
            raise ValueError("use library sync-player [player] or --player <player>")
        sync_player(delete=not args.no_delete, player=args.player or (args.args[0] if args.args else None))
    elif a == "sync-lists":
        sync_collections()
    elif a == "watchlist":
        op, *values = args.args or ["list"]
        if op == "list" and not values:
            print(json.dumps(watchlist_get(), indent=2))
        elif op == "add" and len(values) == 1:
            key = watchlist_update(item=json.loads(values[0]))
            logui("library.watchlist.add", key=key)
        elif op == "remove" and len(values) == 1:
            watchlist_update(remove=values[0])
        else:
            raise ValueError("usage: library watchlist list|add <JSON>|remove <key>")
    elif a == "sync-watchlist":
        sync_watchlist()
    elif a == "sync-torrent":
        sync_torrent(limit=args.limit, link=args.link)
    elif a == "sync-folder":
        if not args.args: print("ERROR: library sync-folder <path>"); return
        sync_folder(args.args[0], link=args.link)
    elif a == "sync-meta":
        _library_sync_meta()
    elif a == "sync-all":
        sync_all()
    elif a == "sync-recommendations":
        sync_recommendations()
