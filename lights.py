"""
lights — smart light control

Usage (via mediactl; control operations accept --device NAME[,NAME...]):
  lights list
  lights on [name...] | off [name...] | status [name...]
  lights brightness <1-100> [name...]
  lights white [temp] [brightness] [name...]
  lights colour <hue> [saturation] [value] [name...]
  lights scene <1-8> [name...]
  lights connect <user_code>

Help: help lights | lights --help
"""

import argparse
import json
import time
import smartlife
from pathlib import Path
from logui import logui

_OUT       = Path(__file__).parent / ".out"
_DEVICES_F = _OUT / "devices.json"
_STATUS_F  = _OUT / "light.json"
_IR_STATUS = {}


def _registry():
    """devices.json lights[] -> [{"alias": name, "id": identifier}]"""
    try:
        data = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [{"alias": d.get("name", ""), "id": str(d.get("identifier", ""))}
            for d in data.get("lights", []) if d.get("identifier")]


def _devs(args, registry=None):
    """Resolve --device strictly; an omitted selector means all."""
    args = list(args)
    arg = None
    for i, a in enumerate(args):
        if a == '--device':
            if i + 1 == len(args) or not args[i + 1].strip():
                raise ValueError("--device requires a name")
            arg = args[i + 1]
            break
    reg = _registry() if registry is None else registry
    if arg is None:
        return reg
    names = [arg] if any(d["alias"] == arg for d in reg) else [n.strip() for n in arg.split(',')]
    picked = []
    for name in names:
        matches = [d for d in reg if d["alias"] == name] or [d for d in reg if d["id"] == name]
        if len(matches) != 1:
            raise ValueError(f"unknown or ambiguous device '{name}'")
        if matches[0] not in picked:
            picked.append(matches[0])
    return picked


def _send(devs, commands):
    """Send one command batch per device through the active backend."""
    for dev in devs:
        try:
            smartlife.cmd(dev["id"], commands)
        except Exception as e:
            logui.err("lights.error", name=dev["alias"], msg=str(e))


def list_lights():
    """List registered lights from devices.json."""
    reg = _registry()
    if not reg:
        logui.print("  no lights in devices.json — run: device discover  or add via the web picker")
        return
    for d in reg:
        logui.print(f"  {d['alias']:<20} {d['id']}")


def on(*args, quiet=False):
    devs = _devs(args)
    _send(devs, [{"code": "switch_led", "value": True}])
    for dev in devs:
        logui("lights.on", name=dev["alias"])


def off(*args, quiet=False):
    devs = _devs(args)
    _send(devs, [{"code": "switch_led", "value": False}])
    for dev in devs:
        logui("lights.off", name=dev["alias"])


def brightness(level, *args):
    pct  = max(1, min(100, int(level)))
    v    = int(10 + (pct / 100) * 990)
    devs = _devs(args)
    _send(devs, [{"code": "work_mode",       "value": "white"},
                 {"code": "bright_value_v2", "value": v}])
    for dev in devs:
        logui("lights.brightness", name=dev["alias"], pct=pct)


def white(temp=50, bright=100, *args, quiet=False):
    devs = _devs(args)
    _send(devs, [
        {"code": "work_mode",       "value": "white"},
        {"code": "temp_value_v2",   "value": int(int(temp) / 100 * 1000)},
        {"code": "bright_value_v2", "value": int(10 + int(bright) / 100 * 990)},
    ])
    for dev in devs:
        logui("lights.brightness", name=dev["alias"], pct=int(bright), temp=int(temp))


def colour(h, s=100, v=100, *args, quiet=False):
    devs = _devs(args)
    _send(devs, [
        {"code": "work_mode",      "value": "colour"},
        {"code": "colour_data_v2", "value": {
            "h": int(h), "s": int(int(s)/100*1000), "v": int(int(v)/100*1000)}},
    ])
    for dev in devs:
        logui("lights.on", name=dev["alias"], mode="colour", h=int(h))


def status(*args):
    """Refresh and report the status of lights and IR remotes."""
    import ir
    registry = _registry()
    ir_devs = [{**dev, "ir": True} for dev in ir._registry()]
    selected = _devs(args, registry=registry + ir_devs)
    if not selected:
        return {}
    try:
        previous = json.loads(_STATUS_F.read_text(encoding="utf-8"))
    except Exception:
        previous = {}
    current_names = {dev["alias"] for dev in registry}
    saved = ({name: state for name, state in previous.items()
              if name in current_names} if "--device" in args else {})
    for dev in selected:
        if dev.get("ir"):
            continue
        try:
            s    = smartlife.device_status(dev["id"])
            on_  = s.get("switch_led", False)
            mode = s.get("work_mode", "?")
            kw   = {"name": dev["alias"], "available": True,
                    "on": on_, "mode": mode}
            if mode == "white":
                b = s.get("bright_value_v2", 0)
                t = s.get("temp_value_v2", 0)
                kw["bright"] = int((b - 10) / 990 * 100)
                kw["temp"]   = int(t / 1000 * 100)
            elif mode == "colour":
                c = s.get("colour_data_v2", {})
                if isinstance(c, dict):
                    kw["h"] = c.get("h", 0)
            kw["updated_at"] = int(time.time())
            saved[dev["alias"]] = kw
        except Exception:
            kw = {**previous.get(dev["alias"], {}), "name": dev["alias"],
                  "available": False, "error": "unreachable",
                  "updated_at": int(time.time())}
            saved[dev["alias"]] = kw
    _OUT.mkdir(parents=True, exist_ok=True)
    _STATUS_F.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    for name, state in saved.items():
        before = {k: v for k, v in previous.get(name, {}).items() if k != "updated_at"}
        after = {k: v for k, v in state.items() if k != "updated_at"}
        if before != after:
            emit = logui if state.get("available") else logui.err
            emit("lights.status", **state)
    # IR reports use their own event and stay out of the light-only tile cache.
    result = dict(saved)
    for dev in selected:
        if not dev.get("ir"):
            continue
        kw = {"name": dev["alias"], "kind": dev["kind"], "state": "unknown"}
        try:
            reported = smartlife.device_status(dev["id"])
            kw.update(available=True, state=reported or "unknown")
        except Exception:
            kw.update(available=False, error="unreachable")
        if _IR_STATUS.get(dev["id"]) != kw:
            emit = logui if kw["available"] else logui.err
            emit("ir.status", **kw)
            _IR_STATUS[dev["id"]] = kw
        result[dev["alias"]] = kw
    return result


_LIGHT_CATEGORIES = {"dj", "dc", "dd", "xdd", "fwd", "gyd", "tgq", "tyndj"}


def connect(user_code):
    """Smart Life QR login, then discover lights and report status."""
    if smartlife.connect(user_code):
        devices = smartlife.scan()
        _discover(devices)
        import ir
        ir._discover(devices)
        status()
        logui("lights.linked")


def _discover(devices=None):
    """Merge lights into devices.json lights[]: tuya_devices.json aliases always, plus a
    Smart Life cloud scan when connected. Merge by identifier — manual renames and picker
    rows are preserved."""
    found = []
    try:
        if smartlife.connected():
            found += [{"name": "", "identifier": str(d["id"]), "source": "smartlife",
                       "description": (d.get("name") or "").strip()}
                      for d in (devices if devices is not None else smartlife.scan())
                      if d.get("category") in _LIGHT_CATEGORIES]
    except Exception:
        pass
    if not found:
        return
    try:
        existing = json.loads(_DEVICES_F.read_text(encoding="utf-8"))
    except Exception:
        existing = {}
    arr   = list(existing.get("lights", []))
    known = {str(d.get("identifier")) for d in arr}
    added = 0
    for d in found:
        if d["identifier"] in known:
            continue
        d["name"] = d["name"] or _device._devices_next_default_name(arr, "lights")
        arr.append(d)
        known.add(d["identifier"])
        logui("device.discovered", name=d["name"], identifier=d["identifier"], type="light")
        added += 1
    if not added:
        return
    existing["lights"] = arr
    _OUT.mkdir(parents=True, exist_ok=True)
    _DEVICES_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")


import device as _device


CLI_SCHEMA = {"ops": {
    "on": {"params": ["--device"]},
    "off": {"params": ["--device"]},
    "brightness": {"args": "1-100", "params": ["--device"]},
    "colour": {"args": "h [s] [v]", "params": ["--device"]},
    "white": {"args": "[temp] [bright]", "params": ["--device"]},
    "scene": {"args": "1-8", "params": ["--device"]},
    "status": {"params": ["--device"]},
    "list": {},
    "connect": {"args": "user_code"},
}}


def cli(argv):
    """Parse lights command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="lights", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--device", default=None)
    args = p.parse_args(argv)

    a = args.action; r = args.args
    d = ['--device', args.device] if args.device else []
    try:
        if   a == "list":       list_lights()
        elif a == "connect":    connect(r[0])
        elif a == "on":         on(*r, *d)
        elif a == "off":        off(*r, *d)
        elif a == "brightness": brightness(r[0], *r[1:], *d)
        elif a == "white":      white(r[0] if r else 50, r[1] if len(r)>1 else 100, *r[2:], *d)
        elif a == "colour":     colour(r[0], r[1] if len(r)>1 else 100, r[2] if len(r)>2 else 100, *r[3:], *d)
        elif a == "scene":      scene(r[0], *r[1:], *d)
        elif a == "status":     status(*r, *d)
    except Exception as e:
        print(f"  ERROR: {e}")
