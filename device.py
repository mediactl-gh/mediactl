"""Discover and control devices.

Usage:
  device discover [--ip ADDRESS] [--refresh]
  device list
  device status
  device <name> <operation> [args...]
  device <name> help

"""

from __future__ import annotations

import argparse
import importlib
import json
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
from logui import logui

HERE     = Path(__file__).parent


_OUT     = HERE / ".out"


_CAPS_F  = _OUT / "capabilities.json"


def contribute_client_devices(devices: dict, source_ip: str) -> list[str]:
    for device in devices.get("cameras", []) + devices.get("mic", []) + devices.get("speaker", []):
        if isinstance(device.get("identifier"), str) and device["identifier"].startswith("push://"):
            device["identifier"] = f"push://{source_ip}"
        device["source"] = source_ip
    return contribute_devices(devices, source_ip=source_ip)


def edit_device(body: dict, source_ip: str) -> dict:
    action = body.get("action")
    kind = body.get("kind")
    if kind not in _DEVICE_MANUAL_KINDS:
        raise ValueError("invalid kind")
    if action == "delete":
        name = body.get("name")
        if not name:
            raise ValueError("name required")
        devices_manual_delete(kind, str(name))
        return {}
    if action == "upsert":
        original = body.get("originalName") or body.get("original_name") or None
        advanced = body.get("advanced")
        if advanced is not None and not isinstance(advanced, dict):
            raise ValueError("advanced must be a JSON object")
        name = devices_manual_upsert(
            kind, str(original) if original else None, str(body.get("name") or ""),
            body.get("identifier"), str(body.get("source") or ""), advanced, source_ip)
        return {"name": name}
    raise ValueError("invalid action")


def contribute_devices(devices: dict, source_ip: str = None) -> list[str]:
    """Merge client-reported devices into shared devices.json. Returns new entry names."""
    try:
        existing = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except Exception:
        existing = {}

    added: list[str] = []
    for key in ("cameras", "mic", "speaker", "devices", "tvs"):
        incoming = devices.get(key, [])
        if not incoming:
            continue
        current = existing.get(key, [])
        names = set()
        for row in current:
            if not row.get("name") or row["name"] in names:
                row["name"] = _devices_next_default_name(current, key)
            names.add(row["name"])
        by_ident = {d["identifier"]: d for d in current if d.get("identifier") is not None}
        for d in incoming:
            ident = d.get("identifier")
            if ident is None or ident == "":
                continue
            if source_ip:
                d["source"] = source_ip
            if ident in by_ident:


                row = by_ident[ident]
                for k, v in d.items():
                    if k == "name":
                        continue
                    if row.get(k) != v:
                        row[k] = v
                continue
            d["name"] = _devices_next_default_name(current, key)
            current.append(d)
            by_ident[ident] = d
            added.append(d["name"])
        existing[key] = current

    _OUT.mkdir(parents=True, exist_ok=True)
    _DEVICES_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    if added:
        logui("web.devices.contributed", added=len(added), devices=" ".join(added), source=source_ip or "unknown")
    return added


def write_capabilities(identifier: str, caps: dict) -> None:
    """Overwrite the live capability readings for one device in capabilities.json.
    Registry (names) lives in devices.json; this holds the volatile values."""
    if not identifier:
        return
    try:
        existing = json.loads(_CAPS_F.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}

    entry = dict(caps or {})
    entry["ts"] = int(time.time())

    old = existing.get(identifier)
    existing[identifier] = entry
    if old and {k: old.get(k) for k in old if k != "ts"} == {k: entry.get(k) for k in entry if k != "ts"}:
        return

    _OUT.mkdir(parents=True, exist_ok=True)
    _CAPS_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    logui("device.capabilities", id=identifier,
           caps=" ".join(sorted(k for k in entry if k != "ts")) or "none")


_DEVICE_MANUAL_KINDS = frozenset({"cameras", "mic", "speaker", "devices", "tvs", "lights"})


_DEVICE_PREFIX = {"cameras": "camera", "mic": "mic", "speaker": "speaker", "devices": "device", "tvs": "tv", "lights": "light", "ir": "ir"}


def _devices_next_default_name(entries: list[dict], kind: str) -> str:
    prefix = _DEVICE_PREFIX[kind]
    names = {d.get("name") for d in entries}
    i = 0
    while f"{prefix}-{i}" in names:
        i += 1
    return f"{prefix}-{i}"


def _devices_name_conflict(entries: list[dict], name: str, skip_idx: Optional[int]) -> bool:
    for i, d in enumerate(entries):
        if skip_idx is not None and i == skip_idx:
            continue
        if d.get("name") == name:
            return True
    return False


def _devices_ident_conflict(entries: list[dict], ident, skip_idx: Optional[int]) -> bool:
    for i, d in enumerate(entries):
        if skip_idx is not None and i == skip_idx:
            continue
        if d.get("identifier") == ident:
            return True
    return False


def devices_manual_delete(kind: str, name: str) -> None:
    if kind not in _DEVICE_MANUAL_KINDS:
        raise ValueError("invalid kind")
    try:
        existing = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except Exception:
        existing = {}
    arr = list(existing.get(kind, []))
    n = len(arr)
    arr = [d for d in arr if d.get("name") != name]
    if len(arr) == n:
        raise ValueError("entry not found")
    existing[kind] = arr
    _OUT.mkdir(parents=True, exist_ok=True)
    _DEVICES_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    logui("web.devices.manual-delete", kind=kind, name=name)


def devices_manual_upsert(
    kind: str,
    original_name: Optional[str],
    name: str,
    identifier,
    source: str,
    advanced: Optional[dict],
    source_ip: Optional[str],
) -> str:
    """Create or replace one devices.json row. Returns final entry name."""
    if kind not in _DEVICE_MANUAL_KINDS:
        raise ValueError("invalid kind")
    if identifier is None or (isinstance(identifier, str) and not identifier.strip()):
        raise ValueError("identifier is required")

    try:
        existing = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except Exception:
        existing = {}

    arr = list(existing.get(kind, []))
    adv = dict(advanced or {})
    for k in ("name", "identifier", "source"):
        adv.pop(k, None)

    src = (source or "").strip()
    if not src and source_ip:
        src = source_ip

    edit_idx: Optional[int] = None
    if original_name:
        edit_idx = next((i for i, d in enumerate(arr) if d.get("name") == original_name), None)
        if edit_idx is None:
            raise ValueError("original entry not found")

    nm = (name or "").strip()
    if edit_idx is not None:
        final_name = nm or arr[edit_idx].get("name") or _devices_next_default_name(arr, kind)
    else:
        final_name = nm or _devices_next_default_name(arr, kind)

    if _devices_name_conflict(arr, final_name, edit_idx):
        raise ValueError("name already exists")
    if _devices_ident_conflict(arr, identifier, edit_idx):
        raise ValueError("identifier already in use")

    row = {**adv, "name": final_name, "identifier": identifier, "source": src}

    if edit_idx is not None:
        arr[edit_idx] = row
    else:
        arr.append(row)

    existing[kind] = arr
    _OUT.mkdir(parents=True, exist_ok=True)
    _DEVICES_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    logui("web.devices.manual-upsert", kind=kind, name=final_name)
    return final_name


_DEVICES_F = _OUT / "devices.json"


def devices_read(bucket: str) -> list:
    """Rows of a devices.json bucket (cameras/mic/tvs/ir/…); [] if missing or unreadable."""
    try:
        return list(json.loads(_DEVICES_F.read_text(encoding="utf-8")).get(bucket, []))
    except Exception:
        return []


def devices_find(bucket: str, key, fields=("name",), default_first: bool = False) -> dict:
    """Find one bucket row by matching `key` (case-insensitive) against any of `fields`.
    key=None returns the first row when default_first, else raises. Raises KeyError on no match."""
    rows = devices_read(bucket)
    if key is None:
        if default_first and rows:
            return rows[0]
        raise KeyError(bucket)
    low = str(key).lower()
    for r in rows:
        if any(str(r.get(f, "")).lower() == low for f in fields):
            return r
    raise KeyError(key)


def _device_rows():
    try:
        data = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError(
            "devices.json is corrupt or empty; run: device discover --refresh"
        ) from None
    if not isinstance(data, dict) or any(
        not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
        for rows in data.values()
    ):
        raise ValueError("invalid device registry format")
    return [(kind, row) for kind, rows in sorted(data.items()) for row in rows]


def list_devices(rows=None):
    rows = _device_rows() if rows is None else rows
    if not rows:
        logui.print("  no devices configured in devices.json — run: device discover")
        return
    for kind, row in rows:
        logui.print(f"  {row.get('name', '-'):<20} {kind:<12} {str(row.get('identifier', '-')):<30} {row.get('source', '-')}")


def status(rows=None):
    rows = _device_rows() if rows is None else rows
    if not rows:
        logui.print("  no devices configured in devices.json — run: device discover")
        return
    import network
    presence = network._load_state()
    try:
        capabilities = json.loads(_CAPS_F.read_text(encoding="utf-8"))
    except FileNotFoundError:
        capabilities = {}
    if not isinstance(capabilities, dict):
        raise ValueError("invalid capabilities format")
    for kind, row in rows:
        identifier = str(row.get("identifier", ""))
        host = urlsplit(identifier).hostname if "://" in identifier else identifier
        observed = next((entry for entry in presence.values()
                         if host and host in (entry.get("ip"), entry.get("mac"))), {})
        caps = capabilities.get(identifier) or {}
        if not isinstance(caps, dict):
            raise ValueError(f"invalid capabilities for {identifier}")
        logui("device.status", name=row.get("name", "-"), type=kind,
              presence="home" if observed.get("home") is True else
                       "away" if observed.get("home") is False else "unknown",
              last_seen=observed.get("last_seen", "unknown"),
              capabilities={key: value for key, value in caps.items() if key != "ts"} or "unavailable",
              reported=caps.get("ts", "unknown"))


_SERVICE_BUCKETS = {
    "jellyfin": "media-center",
    "plex":     "media-center",
}


_TITLE_HANDLERS = {
    "qBittorrent":  ("torrent", "qbittorrent"),
    "Transmission": ("torrent", "transmission"),
}


_NETWORK_FIELDS = frozenset({"url", "source"})


_DROP_FIELDS    = frozenset({"source_ip"})


_SERVICES_F = _OUT / "services.json"


_SERVICE_DEFAULTS = {
    "torrent": {"qbittorrent": {"url": "", "username": "", "password": ""}},
    "indexer": {"prowlarr": {"url": "", "api_key": ""}},
}


def _registered_services(bucket):
    try:
        data = json.loads(_SERVICES_F.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get(bucket, {})


def _service_name(service, source, current):
    for name, info in current.items():
        if info.get("type") == service and info.get("source") == source and name.startswith(f"{service}-"):
            return name
    used = set(current)
    index = 0
    while f"{service}-{index}" in used:
        index += 1
    name = f"{service}-{index}"
    used.add(name)
    return name


def _update_services_json(services: dict):
    """Merge discovered services into .out/services.json. Structure mirrors
    devices.json: {<bucket>: {<name>: {url, source_ip, ...}}}. Network-managed
    fields (url, source_ip) are refreshed from the probe; all other fields
    are user-edited and preserved across re-discoveries."""
    try:
        data = json.loads(_SERVICES_F.read_text(encoding="utf-8"))
    except Exception:
        data = {"torrent": {}, "media-center": {}}
    previous = json.dumps(data, sort_keys=True)
    for bucket, entries in _SERVICE_DEFAULTS.items():
        for name, defaults in entries.items():
            entry = data.setdefault(bucket, {}).setdefault(name, {})
            for key, value in defaults.items():
                entry.setdefault(key, value)
    for bucket, entries in services.items():
        data.setdefault(bucket, {})
        for name, fresh in entries.items():
            cur = data[bucket].get(name, {})
            merged = {}
            for k, v in fresh.items():
                if k in _NETWORK_FIELDS:
                    merged[k] = v
                elif k in _DROP_FIELDS:
                    continue
                else:
                    merged[k] = cur.get(k, v)
            for k, v in cur.items():
                if k in _DROP_FIELDS or k in _NETWORK_FIELDS:
                    continue
                if k not in merged:
                    merged[k] = v
            data[bucket][name] = merged
    if json.dumps(data, sort_keys=True) == previous:
        return False
    _SERVICES_F.parent.mkdir(parents=True, exist_ok=True)
    _SERVICES_F.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logui("network.services-updated", names=" ".join(
        f"{b}.{n}" for b, ents in services.items() for n in ents
    ))
    return True


_TV_BRAND_ORDER = [
    ("lg-webos", "webos"),
    ("samsung",  "samsung"),
    ("roku",     "roku"),
    ("cast",     "google"),
]


def _device_role(probe):
    """Return the devices.json bucket for this device, or None to skip.
    Priority: TV > camera > speaker > router > media server > (skip).
    One device → one bucket → one row in devices.json."""
    svcs = {s["service"] for s in probe.get("services", [])}
    for svc, brand in _TV_BRAND_ORDER:
        if svc in svcs:
            return "tvs", brand
    if "rtsp"             in svcs: return "cameras", None
    if "daap"             in svcs: return "speaker", None
    if probe.get("kind") == "router":                    return "devices", None
    if svcs & {"plex", "jellyfin"}:                      return "devices", None
    return None, None


def _upsert_network(discovered):
    """Apply network discoveries through the shared device/service upserts."""
    found    = {"tvs": [], "cameras": [], "speaker": [], "devices": []}
    services = {"torrent": {}, "media-center": {}}
    registered = _registered_services("media-center")
    overrides = {}
    for device in devices_read("devices"):
        host = device.get("source_ip") or str(device.get("identifier", "")).removeprefix("push://")
        overrides.setdefault(host, {}).update(device.get("service_overrides") or {})
    for _, p in discovered.items():
        if not p.get("home") or not p.get("ip"):
            continue
        ip, probe = p["ip"], p.get("probe") or {}
        effective = {f"{s['service']}:{s['port']}": s for s in probe.get("services", [])}
        effective.update(overrides.get(ip, {}))
        probe = {**probe, "services": [s for s in effective.values() if s]}
        bucket, brand = _device_role(probe)
        if bucket and ip != "127.0.0.1":
            row = {"identifier": ip, "source_ip": ip}
            if bucket == "tvs":
                row["source"] = brand or "google"
            else:
                row["source"] = "auto"
            found[bucket].append(row)
        for s in probe.get("services", []):
            fresh = {"url": s.get("url") or f"http://{ip}:{s['port']}"}
            if (b := _SERVICE_BUCKETS.get(s["service"])):
                name = _service_name(s["service"], ip, registered)
                fresh.update({"type": s["service"], "name": probe.get("hostname") or ip,
                              "source": ip, "capabilities": ["server"]})
                registered[name] = {**registered.get(name, {}), **fresh}
                services[b].setdefault(name, {}).update(fresh)
            elif s["service"] == "http":
                for marker, (b, name) in _TITLE_HANDLERS.items():
                    if marker in s.get("title", "") or marker in s.get("server", ""):
                        services[b].setdefault(name, {}).update(fresh)
                        break
    changed = False
    if any(found.values()):
        previous = _DEVICES_F.read_bytes() if _DEVICES_F.exists() else None
        contribute_devices(found)
        changed = _DEVICES_F.read_bytes() != previous
    if any(services.values()):
        changed = _update_services_json(services) or changed
    if changed:
        logui("network.discover", tvs=len(found.get("tvs", [])),
              cameras=len(found.get("cameras", [])), devices=len(found.get("devices", [])))


_HOOKS = {
    "camera": ("_discover",),
    "audio": ("_discover",),
    "lights": ("_discover",),
    "weather": ("_discover",),
    "ir": ("_discover",),
    "notify": ("_discover",),
}


_completed: set[str | None] = set()
_lock = threading.RLock()
_local_probe = None


def _refresh_network(ip=None, scan=True, refresh=False):
    global _local_probe
    with _lock:
        import network
        if ip is not None:
            device = network.refresh_device(ip)
            _upsert_network({device["ip"]: device})
            return device
        if scan:
            network.scan(refresh=refresh)
        discovered = network._load_state()
        _local_probe = network.inspect("127.0.0.1", prior=_local_probe, refresh=refresh)
        local_probe = {**_local_probe,
                       "services": [s for s in _local_probe["services"] if s["port"] in (8080, 9091)]}
        discovered["127.0.0.1"] = {"home": True, "ip": "127.0.0.1", "probe": local_probe}
        _upsert_network(discovered)


def _run(ip=None, refresh=False):
    complete = True
    try:
        result = _refresh_network(ip=ip, refresh=refresh)
    except Exception as e:
        if ip is not None:
            raise
        complete = False
        logui.warn("device.discover.error", module="network", error=str(e))
    else:
        if ip is not None:
            return result
    for name, hooks in _HOOKS.items():
        for hook in hooks:
            try:
                deferred = getattr(importlib.import_module(name), hook)()
                if callable(deferred):
                    deferred()
            except Exception as e:
                complete = False
                logui.warn("device.discover.error", module=name, error=str(e))
    return complete


def discover(ip=None, refresh=False):
    with _lock:
        if ip in _completed and not refresh:
            return True
        _completed.discard(ip)
        result = _run(ip=ip, refresh=refresh)
        if result is False:
            return False
        _completed.add(ip)
        return True


CLI_SCHEMA = {"ops": {
    "discover": {"params": ["--ip", "--refresh"]},
    "list": {},
    "status": {},
}}


_ROUTES = {
    "tvs": ("tv", "--tv"),
    "lights": ("lights", "--device"),
    "ir": ("ir", "--remote"),
    "cameras": ("camera", "--camera"),
    "mic": ("audio", "--device"),
    "speaker": ("audio", "--out-device"),
    "devices": ("network", None),
}
_TARGET_FLAGS = frozenset(flag for _, flag in _ROUTES.values() if flag)


def _target_cli(name, argv):
    rows = [(kind, row) for kind, row in _device_rows()
            if str(row.get("name", "")).casefold() == name.casefold()]
    if len(rows) != 1:
        raise ValueError(f"unknown or ambiguous device '{name}'; use device list")
    kind, row = rows[0]
    name = row["name"]
    if kind not in _ROUTES:
        raise ValueError(f"unsupported device type '{kind}'")
    module_name, flag = _ROUTES[kind]
    module = importlib.import_module(module_name)
    ops = {op: spec for op, spec in module.CLI_SCHEMA["ops"].items()
           if flag in spec.get("params", [])}
    if kind == "devices":
        ops = {"inspect": {"params": ["--refresh"]}}
    ops = {"list": {}, "status": {}, **ops}
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        for op, spec in ops.items():
            params = " ".join(p for p in spec.get("params", []) if p != flag)
            print(f"  device {name!r} {op} {spec.get('args', '')} {params}".rstrip())
        return
    op, *tail = argv
    if op not in ops:
        raise ValueError(f"unsupported operation '{op}' for '{name}'; use device {name!r} help")
    allowed = set(ops[op].get("params", [])) - _TARGET_FLAGS
    if any(t.startswith("--") and t.split("=", 1)[0] not in allowed for t in tail):
        raise ValueError(f"unsupported option for '{op}'; device target is fixed")
    if op == "list" or (op == "status" and kind not in ("tvs", "lights")):
        if tail:
            raise ValueError(f"device {name} {op} takes no arguments")
        return (list_devices if op == "list" else status)([(kind, row)])
    if row.get("identifier") is None or row["identifier"] == "":
        raise ValueError(f"device '{name}' has no identifier")
    if kind == "devices":
        if any(t != "--refresh" for t in tail):
            raise ValueError("device inspect only accepts --refresh")
        identifier = str(row["identifier"])
        host = urlsplit(identifier).hostname if "://" in identifier else identifier
        if not host or host.startswith("-"):
            raise ValueError(f"device '{name}' has no usable network address")
        return module.cli([op, host, *tail])
    return module.cli([*argv, f"{flag}={name}"])


def cli(argv):
    if argv and argv[0] not in (*CLI_SCHEMA["ops"], "help", "--help", "-h"):
        return _target_cli(argv[0], argv[1:])
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="device", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("--ip")
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args(argv)
    if (args.ip is not None or args.refresh) and args.action != "discover":
        raise ValueError("--ip and --refresh require device discover")
    if args.action == "discover":
        if not discover(ip=args.ip, refresh=args.refresh):
            raise RuntimeError("discovery incomplete; see device discovery errors and retry")
    elif args.action == "list":
        list_devices()
    elif args.action == "status":
        status()
