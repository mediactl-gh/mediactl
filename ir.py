"""
ir — IR remote control

Usage:
  ir remotes
  ir keys <remote>
  ir send <remote> <key> [value]
  ir keys --remote NAME
  ir send <key> [value] --remote NAME

Help: help ir | ir --help
"""

import argparse
import json
import smartlife
from pathlib import Path
from logui import logui

_OUT       = Path(__file__).parent / ".out"
_DEVICES_F = _OUT / "devices.json"
_IR_F      = _OUT / "ir.json"

_IR_PREFIX = "infrared_"
_TV_KINDS  = {"tv", "box"}            # kinds routed to tvs[] and driven by the TV tile


def _reg_row(d):
    return {"alias": d.get("name", ""), "id": str(d.get("identifier", "")),
            "category": d.get("category", ""),
            "kind": d.get("kind", "") or str(d.get("category", "")).removeprefix(_IR_PREFIX)}


def _registry():
    return [_reg_row(d) for d in _device.devices_read("ir") if d.get("identifier")]


def _resolve(remote):
    try:
        try:
            row = _device.devices_find("ir", remote)
        except KeyError:
            row = _device.devices_find("ir", remote, fields=("identifier", "category"))
        return _reg_row(row)
    except KeyError:
        if not _device.devices_read("ir"):
            logui("ir.empty")
            raise Exception("no IR remotes — connect Smart Life, then: device discover")
        raise Exception(f"'{remote}' not found — run: ir remotes")


# ── Supplement (.out/ir.json: {identifier: {keys:[...]}}) ────────────────────────

def _load_supp():
    try:
        return json.loads(_IR_F.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_supp(supp):
    _OUT.mkdir(parents=True, exist_ok=True)
    _IR_F.write_text(json.dumps(supp, indent=2), encoding="utf-8")


def _keys(dev):
    """Keys for a resolved remote — cached in ir.json, fetched from specs on miss."""
    supp = _load_supp()
    ks   = (supp.get(dev["id"]) or {}).get("keys")
    if ks:
        return ks
    ks = [f.get("code") for f in smartlife.device_specs(dev["id"]) if f.get("code")]
    supp[dev["id"]] = {"keys": ks}
    _save_supp(supp)
    return ks


# ── Ops ──────────────────────────────────────────────────────────────────────────

def remotes():
    """List discovered IR remotes from devices.json ir[]."""
    reg = _registry()
    if not reg:
        logui.print("  no IR remotes — connect Smart Life, then: device discover")
        return
    for d in reg:
        logui.print(f"  {d['alias']:<20} {d['category']:<14} {d['id']}")


def keys(remote):
    """List a remote's keys."""
    dev = _resolve(remote)
    ks  = _keys(dev)
    if not ks:
        logui.print(f"  no keys for {dev['alias']}")
        return
    for k in ks:
        logui.print(f"  {k}")


def send_code(device_id, code, value=None):
    """Fire a raw key code at an IR device by id — shared by `ir send` and the TV IR driver."""
    val = code if value is None else (int(value) if str(value).lstrip("-").isdigit() else value)
    smartlife.cmd(str(device_id), [{"code": code, "value": val}])


def send(remote, key, value=None, *args):
    """Fire a key on a remote. value defaults to the key name (STRING keys);
    pass an explicit value for ENUM keys (e.g. AC temp/mode/fan)."""
    dev = _resolve(remote)
    try:
        send_code(dev["id"], key, value)
        logui("ir.sent", remote=dev["alias"], key=key)
    except Exception as e:
        logui.err("ir.error", remote=dev["alias"], msg=str(e))


# ── Discovery hook ────────────────────────────────────────────────────────────────

def _discover(devices=None):
    """Discover Smart Life IR remotes into devices.json, routed by kind: TV-controllable →
    tvs[] (brand=ir), rest → ir[]. Idempotent by identifier; caches key lists into ir.json."""
    try:
        if not smartlife.connected():
            return
        found = [{"identifier": str(d["id"]),
                  "category": d.get("category", ""),
                  "kind": str(d.get("category", "")).removeprefix(_IR_PREFIX),
                  "description": (d.get("name") or "").strip()}
                 for d in (devices if devices is not None else smartlife.scan())
                 if str(d.get("category", "")).startswith(_IR_PREFIX)]
    except Exception:
        return
    if not found:
        return
    try:
        existing = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except Exception:
        existing = {}
    known = {str(d.get("identifier"))
             for bucket in ("ir", "tvs") for d in existing.get(bucket, [])}
    changed = False
    for d in found:
        if d["identifier"] in known:
            continue
        bucket = "tvs" if d["kind"] in _TV_KINDS else "ir"
        arr  = existing.setdefault(bucket, [])
        name = d["description"] or _device._devices_next_default_name(arr, bucket)
        row  = {"name": name, "identifier": d["identifier"], "source": "ir"}
        if bucket == "ir":                       # tvs[] reads only name/identifier/source
            row.update(category=d["category"], kind=d["kind"])
        arr.append(row)
        known.add(d["identifier"])
        logui("device.discovered", name=name, identifier=d["identifier"],
              type=("tv" if bucket == "tvs" else "ir"))
        changed = True
    if changed:
        _OUT.mkdir(parents=True, exist_ok=True)
        _DEVICES_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    _cache_keys(found)


def _cache_keys(arr):
    """Cache ir.json key lists for remotes missing them; TV-bucket kinds don't use it, skipped."""
    supp    = _load_supp()
    changed = False
    for d in arr:
        ident = str(d.get("identifier"))
        if not ident or d.get("kind") in _TV_KINDS or (supp.get(ident) or {}).get("keys"):
            continue
        try:
            supp[ident] = {"keys": [f.get("code") for f in smartlife.device_specs(ident) if f.get("code")]}
            changed = True
        except Exception:
            pass
    if changed:
        _save_supp(supp)


import device as _device


CLI_SCHEMA = {"ops": {
    "remotes": {},
    "keys": {"params": ["--remote"]},
    "send": {"args": "key value", "params": ["--remote"]},
}}


def cli(argv):
    """Parse ir command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="ir", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--remote", default=None)
    args = p.parse_args(argv)

    a = args.action; r = args.args
    remote = args.remote or (r[0] if r else None)
    kv = r if args.remote else r[1:]   # positional remote fallback consumes r[0]
    try:
        if   a == "remotes": remotes()
        elif a == "keys":    keys(remote)
        elif a == "send":    send(remote, kv[0], kv[1] if len(kv) > 1 else None)
    except Exception as e:
        print(f"  ERROR: {e}")
