"""
network — presence, inspection, and known devices

Usage:
  network scan [--transport any|wifi|ble] [--refresh]
  network inspect <ip> [--refresh]
  network list
  network add <name> <MAC>
  network remove <name>

--refresh forces re-inspection instead of using cached probes.

Help: help network | network --help
"""

import argparse

import os
import json
import re
import time
import socket
import struct
import concurrent.futures
import threading
from datetime import datetime
from logui import logui

KNOWN_FILE = ".out/known_devices.json"
PRESENCE_FILE = ".out/presence.json"
EDITS_FILE = ".out/network_edits.json"
_STATE_LOCK = threading.RLock()

BLE_ENABLED = True

# ── Probe configuration (top of file for readability) ────────────────────────

# How long a probe result is reused before re-running (seconds).
PROBE_TTL = 3600

# (port_signature, kind) — first match wins the device's class.
#   kind ∈ {tv, router, media, printer, computer, camera, phone, unknown}.
KIND_RULES = [
    ({8008, 8009},                       "tv"),       # Cast / Chromecast built-in
    ({8008},                             "tv"),       # Cast device w/o 8009
    ({8060},                             "tv"),       # Roku
    ({3000},                             "tv"),       # LG webOS
    ({8001},                             "tv"),       # Samsung Tizen
    ({53},                               "router"),   # DNS = home router / gateway
    ({32400},                            "media"),    # Plex / media server
    ({631, 9100},                        "printer"),
    ({445, 22, 548, 3389, 5900},         "computer"),
    ({554},                              "camera"),
    ({5060},                             "phone"),
]

# Default friendly label per kind, used when no service-discovered name,
# rDNS hostname, or NetBIOS name is available.
KIND_LABELS = {
    "tv":       "TV",
    "router":   "Router",
    "media":    "Media Server",
    "printer":  "Printer",
    "computer": "Computer",
    "camera":   "Camera",
    "phone":    "Phone",
}

# port → service name. If the port is open, the name appears in services[].
SERVICES = {
    8008:  "cast",  8060: "roku",      3000: "lg-webos",
    8001:  "samsung", 32400: "plex",     8096: "jellyfin",
    9091:  "http",
    80:    "http",  8080: "http",  8000: "http",  8888: "http",
    8443:  "https", 443:  "https",
    445:   "smb",   22:   "ssh",    548:  "afp",
    3389:  "rdp",   5900: "vnc",    631:  "ipp",    9100: "jetdirect",
    554:   "rtsp",  5060: "sip",    53:   "dns",
    1900:  "ssdp",  5353: "mdns",   3689: "daap",
}

# Union of every port referenced above — auto-derived, single place to add.
_PROBE_PORTS = sorted({p for sig, _ in KIND_RULES for p in sig} | set(SERVICES))


# ── Known devices store ────────────────────────────────────────────────────────

def _load_known():
    """Load known devices from .out/known_devices.json. Returns {name: mac}."""
    if os.path.exists(KNOWN_FILE):
        try:
            return json.loads(open(KNOWN_FILE).read())
        except Exception:
            pass
    return {}


def _save_known(devices):
    os.makedirs(".out", exist_ok=True)
    open(KNOWN_FILE, "w").write(json.dumps(devices, indent=2))


def add(name, mac):
    """Add or rename a known device. Identity is the MAC — one entry per MAC."""
    mac     = mac.upper()
    # drop any existing name(s) bound to this MAC so a rename replaces, not duplicates
    devices = {n: m for n, m in _load_known().items() if m.upper() != mac}
    devices[name] = mac
    _save_known(devices)
    logui("network.add", name=name, mac=mac)


def remove(name):
    """Remove a known device by name."""
    devices = _load_known()
    if name not in devices:
        logui.warn("network.remove", name=name, msg="not found")
        return
    del devices[name]
    _save_known(devices)
    logui("network.remove", name=name)


def list_devices():
    """List all discovered devices from presence.json. Known (curated) ones
    are marked with `*`; column order matches other list ops."""
    devices = _load_state()
    if not devices:
        logui.print("  none discovered — run: network scan")
        return
    known_macs = {m.upper() for m in _load_known().values()}
    rows = sorted(devices.items(),
                  key=lambda kv: (not kv[1].get("home"),
                                  kv[1].get("label", kv[0]).lower()))
    for key, p in rows:
        label = p.get("label", key)
        mac   = p.get("mac", "")
        state = "home" if p.get("home") else "away"
        kind  = (p.get("probe") or {}).get("kind", "?")
        ip    = p.get("ip") or (f"{p['rssi']} dBm" if p.get("rssi") is not None else "-")
        mark  = "*" if mac.upper() in known_macs else " "
        logui.print(f"  {mark} {label:<22} {mac:<18}  {state:<5}  {kind:<10}  {ip}")


# ── ARP scan ───────────────────────────────────────────────────────────────────

def _arp_cache():
    """Read the OS ARP table → {MAC: IP}."""
    import subprocess, re, sys
    out = {}
    kw = {"capture_output": True, "text": True, "timeout": 5}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        r = subprocess.run(["arp", "-a"], **kw)
    except Exception:
        return out
    for line in r.stdout.splitlines():
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})", line)
        if not m:
            continue
        ip, mac = m.group(1), m.group(2).upper().replace("-", ":")
        if mac == "FF:FF:FF:FF:FF:FF" or ip.endswith(".255") \
           or ip.startswith(("224.", "239.", "255.")):
            continue
        out[mac] = ip
    return out


def _self():
    """This machine's (ip, mac) — ARP never sees our own host, so add it explicitly."""
    import socket, uuid
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
    except Exception:
        ip = ""
    node = uuid.getnode()
    mac  = ":".join(f"{(node >> e) & 0xff:02X}" for e in range(40, -1, -8))
    return ip, mac


def _subnet():
    """Local /24 derived from this host's IP — no config needed."""
    import ipaddress
    ip, _ = _self()
    try:
        return str(ipaddress.ip_network(ip + "/24", strict=False))
    except Exception:
        return "192.168.1.0/24"


def _arp_scan():
    """Discover {MAC: IP} via scapy ARP sweep unioned with the OS ARP cache."""
    subnet = _subnet()
    result = {}
    try:
        from scapy.layers.l2 import ARP, Ether
        from scapy.sendrecv import srp
        pkt    = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=subnet)
        ans, _ = srp(pkt, timeout=1, verbose=False)
        result = {rcv.hwsrc.upper(): rcv.psrc for _, rcv in ans}
    except ImportError:
        pass  # no scapy/Npcap — fall back to the OS ARP cache only

    for mac, ip in _arp_cache().items():
        result.setdefault(mac, ip)
    return result


def _ble_scan(timeout=5):
    """Discover nearby BLE devices → {address: {"name": str, "rssi": int}}.
    bleak is optional — without it, or with no adapter, return {} (Wi-Fi only)."""
    if os.name == "nt":
        return {}
    try:
        import asyncio
        from bleak import BleakScanner
    except ImportError:
        return {}

    async def _discover():
        found = await BleakScanner.discover(return_adv=True, timeout=timeout)
        return {d.address.upper(): {"name": d.name or "", "rssi": a.rssi}
                for d, a in found.values()}
    try:
        return asyncio.run(_discover())
    except Exception:
        return {}


# ── Service probe (top op + 2 utilities) ──────────────────────────────────────


def tcp_scan(ip, ports, timeout=0.2):
    """Parallel TCP-connect sweep. Returns sorted list of open port numbers."""
    def _try(p):
        try:
            with socket.create_connection((ip, p), timeout=timeout):
                return p
        except Exception:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as ex:
        return sorted(p for p in ex.map(_try, ports) if p)


def http_get(ip, port, path, timeout=0.5):
    """HTTP/1.0 GET. Returns full response bytes (headers + body) or None."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.sendall(f"GET {path} HTTP/1.0\r\nHost: {ip}:{port}\r\nConnection: close\r\n\r\n".encode())
            s.settimeout(timeout)
            data = b""
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
                if len(data) > 16384:
                    break
        return data or None
    except Exception:
        return None


def inspect(ip, prior=None, refresh=False):
    """Check ports, reusing `prior` details if ports match and it is younger
    than PROBE_TTL. Pass refresh=True to bypass the cache.
    Returns {ts, kind, hostname, ports, services[]}."""
    now = time.time()
    open_ports = tcp_scan(ip, _PROBE_PORTS)
    if (not refresh and prior and (now - prior.get("ts", 0)) < PROBE_TTL
            and open_ports == prior.get("observed_ports", prior.get("ports", []))):
        return prior

    out = {"ts": now, "kind": "unknown", "hostname": "",
           "ports": open_ports, "observed_ports": open_ports, "services": []}

    # rDNS with a hard timeout (Windows gethostbyaddr hangs ~4.5s on
    # unknown hosts while it times out DNS + NetBIOS + LLMNR in turn).
    try:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        ex.submit(lambda: None)            # keep the worker idle so the C call can run
        fut = ex.submit(socket.gethostbyaddr, ip)
        out["hostname"] = fut.result(timeout=1.0)[0]
    except Exception:
        pass
    finally:
        ex.shutdown(wait=False)

    port_set = set(open_ports)
    for sig, kind in KIND_RULES:
        if port_set & sig:
            out["kind"] = kind
            break

    services = []
    for port in open_ports:
        if port not in SERVICES:
            continue
        entry = {"service": SERVICES[port], "port": port}

        if port == 8008:                            # Cast
            body = http_get(ip, 8008, "/setup/eureka_info")
            if body:
                try:
                    ei = json.loads(body.split(b"\r\n\r\n", 1)[-1])
                    entry["name"]    = ei.get("name", "")
                    entry["version"] = ei.get("cast_build_revision", "")
                except Exception:
                    pass
            body = http_get(ip, 8008, "/ssdp/device-desc.xml")
            if body:
                xml = body.split(b"\r\n\r\n", 1)[-1].decode("utf-8", errors="replace")
                m = re.search(r"<modelName>([^<]+)</modelName>", xml)
                if m: entry["model"] = m.group(1).strip()
                m = re.search(r"<manufacturer>([^<]+)</manufacturer>", xml)
                if m: entry["manufacturer"] = m.group(1).strip()

        elif port == 32400:                         # Plex
            body = http_get(ip, 32400, "/identity")
            if body:
                try:
                    info = json.loads(body.split(b"\r\n\r\n", 1)[-1])
                    entry["name"]       = info.get("friendlyName", "")
                    entry["version"]    = info.get("version", "")
                    entry["machine_id"] = info.get("machineIdentifier", "")
                except Exception:
                    pass

        elif port in (80, 8080, 8000, 8888, 9091):  # Web (title / realm / server)
            data = http_get(ip, port, "/transmission/web/" if port == 9091 else "/")
            if data:
                sep = data.find(b"\r\n\r\n")
                hdr = (data[:sep] if sep >= 0 else data).decode("latin-1", errors="replace")
                html = (data[sep + 4:] if sep >= 0 else b"").decode("latin-1", errors="replace")
                for line in hdr.splitlines():
                    low = line.lower()
                    if low.startswith("server:"):
                        entry["server"] = line.split(":", 1)[1].strip()
                    elif low.startswith("www-authenticate:"):
                        m = re.search(r'realm="?([^",]+)"?', line, re.I)
                        if m: entry["realm"] = m.group(1).strip()
                m = re.search(r"<title>([^<]+)</title>", html, re.I)
                if m: entry["title"] = m.group(1).strip()

        services.append(entry)
    out["services"] = services
    return out


# ── Live presence state ──────────────────────────────────────────────────────────

PRESENCE_FILE = ".out/presence.json"


def _load_state():
    """Load persisted presence state. Returns {name: {home, ip, mac, last_seen, since}}."""
    if os.path.exists(PRESENCE_FILE):
        try:
            return json.loads(open(PRESENCE_FILE).read()).get("devices", {})
        except Exception:
            pass
    return {}


def _save_state(devices):
    with _STATE_LOCK:
        edits = _load_edits()
        names = {mac.upper(): name for name, mac in _load_known().items()}
        current = {}
        for key, device in devices.items():
            mac = device.get("mac", "").upper()
            device = dict(device)
            if mac in names:
                key = names[mac]
                device.update(known=True, label=key)
            if mac in edits:
                probe = dict(device.get("probe") or {})
                edit = edits[mac]
                if "kind" in edit:
                    probe["kind"] = edit["kind"]
                links = edit.get("links", {})
                removed = set(edit.get("removed_ports", []))
                probe["ports"] = sorted((set(probe.get("ports", [])) | set(edit.get("ports", [])) | {int(p) for p in links}) - removed)
                services = [s for s in probe.get("services", []) if s["port"] not in removed]
                for port, service in links.items():
                    port = int(port)
                    if port in removed:
                        continue
                    prior = next((s for s in services if s["port"] == port and s["service"] == service), {})
                    services = [s for s in services if s["port"] != port]
                    if service:
                        services.append({**prior, "service": service, "port": port})
                probe["services"] = services
                device["probe"] = probe
            if key not in current or device.get("home") or not current[key].get("home"):
                current[key] = device
        os.makedirs(".out", exist_ok=True)
        with open(PRESENCE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"updated": time.time(), "devices": current}, f, indent=2)
        os.replace(PRESENCE_FILE + ".tmp", PRESENCE_FILE)
        return current


def _presence_summary(devices):
    """Compare device changes without timestamps or fluctuating signal strength."""
    return {key: {
        **{k: v for k, v in device.items() if k not in ("last_seen", "since", "rssi", "probe")},
        "probe": {k: v for k, v in (device.get("probe") or {}).items() if k != "ts"},
    } for key, device in devices.items()}


def _load_edits():
    try:
        with open(EDITS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def edit_device(mac, name, kind, ports, services, *, key=None):
    """Save popup fields, retaining manual probe corrections across scans."""
    if not isinstance(mac, str) or not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    mac, name = mac.upper(), name.strip()
    if not isinstance(kind, str) or kind not in {*KIND_LABELS, "unknown"}:
        raise ValueError("invalid device type")
    if not isinstance(ports, list) or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
        raise ValueError("ports must be integers between 1 and 65535")
    if len(set(ports)) != len(ports):
        raise ValueError("duplicate port")
    if not isinstance(services, list):
        raise ValueError("invalid services")
    linked = {}
    for service in services:
        if (not isinstance(service, dict) or type(service.get("port")) is not int
                or service["port"] not in ports
                or not isinstance(service.get("service"), str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", service["service"])):
            raise ValueError("each service must have a valid name and port")
        if service["port"] in linked:
            raise ValueError("only one service can be linked to each port")
        linked[service["port"]] = service["service"]

    with _STATE_LOCK:
        devices = _load_state()
        if key is not None and devices.get(key, {}).get("mac", "").upper() != mac:
            raise ValueError("device changed; reopen the editor")
        match = next(((key, d) for key, d in devices.items() if d.get("mac", "").upper() == mac), None)
        if match is None:
            raise ValueError("device no longer available; scan again")
        key, device = match
        known = _load_known()
        if ((name in known and known[name].upper() != mac)
                or (name in devices and devices[name].get("mac", "").upper() != mac)):
            raise ValueError("name already belongs to another device")
        probe = device.get("probe") or {}
        edits = _load_edits()
        edit = dict(edits.get(mac, {}))
        if kind != probe.get("kind", "unknown"):
            edit["kind"] = kind
        prior_ports = set(probe.get("ports", [])) | {s["port"] for s in probe.get("services", [])}
        removed = (set(edit.get("removed_ports", [])) | (prior_ports - set(ports))) - set(ports)
        edit["removed_ports"] = sorted(removed)
        edit["ports"] = sorted((set(edit.get("ports", [])) | (set(ports) - prior_ports)) - removed)
        links = dict(edit.get("links", {}))
        previous = {s["port"]: s["service"] for s in probe.get("services", [])}
        for port in ports:
            if port not in prior_ports or linked.get(port, "") != previous.get(port, ""):
                links[str(port)] = linked.get(port, "")
        edit["links"] = {p: s for p, s in links.items() if int(p) not in removed}
        edits[mac] = edit
        os.makedirs(".out", exist_ok=True)
        with open(EDITS_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(edits, f, indent=2)
        os.replace(EDITS_FILE + ".tmp", EDITS_FILE)
        add(name, mac)
        devices.pop(key)
        devices[name] = {**device, "known": True, "label": name}
        _save_state(devices)
        return _load_state()


def refresh_device(ip):
    """Probe one IP and update its presence record without scanning other hosts."""
    import ipaddress
    ip = str(ipaddress.ip_address(ip))
    probe = inspect(ip, refresh=True)
    with _STATE_LOCK:
        devices = _load_state()
        key, previous = next(((k, d) for k, d in devices.items() if d.get("ip") == ip),
                             (ip, {"ip": ip, "mac": "", "known": False, "label": ip, "transport": "wifi"}))
        device = {**previous, "probe": probe}
        if probe.get("ports") or probe.get("hostname"):
            now = time.time()
            device.update(home=True, last_seen=now, since=previous.get("since", now) if previous.get("home") else now)
        devices[key] = device
        _save_state(devices)
        return next(d for d in _load_state().values() if d.get("ip") == ip)


# ── Vendor (offline OUI lookup) ──────────────────────────────────────────────────

_OUI_FILE = os.path.join(os.path.dirname(__file__), ".out", "oui.json")
_oui      = None


def _vendor(mac):
    """Friendly label for a MAC: vendor name, 'private device' (randomized), or ''."""
    global _oui
    octets = (mac or "").replace("-", ":").split(":")
    if len(octets) < 3:
        return ""
    try:
        if int(octets[0], 16) & 0x02:      # locally-administered bit → randomized MAC
            return "private device"
    except ValueError:
        return ""
    if _oui is None:
        if not os.path.exists(_OUI_FILE):
            import urllib.request
            os.makedirs(os.path.dirname(_OUI_FILE), exist_ok=True)
            try:    urllib.request.urlretrieve("https://linuxnet.ca/ieee/oui.json", _OUI_FILE)
            except Exception: pass
        try:    _oui = json.loads(open(_OUI_FILE, encoding="utf-8").read())
        except Exception: _oui = {}
    return _oui.get("".join(octets[:3]).upper(), "")


# ── Scan (raw primitive) ─────────────────────────────────────────────────────────

def scan(transport="any", refresh=False):
    """One sweep over Wi-Fi (ARP) and, when enabled, Bluetooth (BLE). `transport`
    ("any"|"wifi"|"ble") limits the sweep. After ARP/BLE, each visible IP is
    checked for changed ports. New, returning, or changed devices are inspected
    immediately; otherwise cached details expire after PROBE_TTL.
    Pass refresh=True to force re-inspection and overwrite the probe field in
    presence.json (default: enrich — preserve cached probes that are still fresh).
    Persists state and emits a network.scan summary when devices change."""
    do_wifi = transport in ("any", "wifi")
    do_ble  = transport in ("any", "ble") and BLE_ENABLED
    known  = _load_known()
    found  = _arp_scan() if do_wifi else {}
    sip, smac = _self()
    if do_wifi and sip and smac:
        found.setdefault(smac, sip)
    ble    = _ble_scan() if do_ble else {}
    by_mac = {mac: name for name, mac in known.items()}
    home   = {}

    now     = time.time()
    prior   = _load_state()
    devices = {}

    for mac, ip in sorted(found.items(), key=lambda kv: kv[1]):
        name   = by_mac.get(mac)
        if name:
            home[name] = ip
        vendor = _vendor(mac)
        key    = name or ip
        prev   = prior.get(key, {})
        probe  = prev.get("probe")
        changed = (not prev.get("home") or prev.get("ip") != ip
                   or prev.get("mac", "").lower() != mac.lower())
        probe = inspect(ip, prior=probe, refresh=refresh or changed)
        # label priority: service-discovered name > NetBIOS > rDNS > kind > vendor > IP
        svc_name = next((s["name"] for s in probe.get("services", []) if s.get("name")), "")
        nb       = (probe.get("netbios") or [""])[0].split("<")[0]
        kind     = probe.get("kind", "unknown")
        label    = svc_name or nb or probe.get("hostname") or KIND_LABELS.get(kind, "") or vendor or ip
        since    = prev.get("since", now) if prev.get("home") else now   # arrival = state change
        devices[key] = {"home": True, "ip": ip, "mac": mac, "known": bool(name),
                        "vendor": vendor, "label": label, "transport": "wifi",
                        "last_seen": now, "since": since,
                        "probe": probe}

    for addr, adv in sorted(ble.items(), key=lambda kv: kv[1]["name"] or kv[0]):
        if addr in found:                              # already seen over Wi-Fi
            continue
        name   = by_mac.get(addr)
        if name:
            home[name] = ""
        vendor = _vendor(addr)
        label  = name or adv["name"] or vendor or addr
        key    = name or addr
        prev   = prior.get(key, {})
        since  = prev.get("since", now) if prev.get("home") else now   # arrival = state change
        devices[key] = {"home": True, "ip": "", "mac": addr, "known": bool(name),
                        "vendor": vendor, "label": label, "transport": "ble",
                        "rssi": adv["rssi"], "last_seen": now, "since": since}

    # known devices that did NOT respond — preserve last probe so it stays visible
    for name in sorted(known):
        if name not in home:
            prev  = prior.get(name, {})
            since = now if prev.get("home") else prev.get("since", now)  # departure = state change
            devices[name] = {"home": False, "ip": "", "mac": known[name], "known": True,
                             "vendor": prev.get("vendor", ""), "label": prev.get("label", name),
                             "last_seen": prev.get("last_seen", 0), "since": since,
                             "probe": prev.get("probe")}

    current = _save_state(devices)
    if _presence_summary(current) != _presence_summary(prior):
        logui("network.scan",
              found=len(found) + len(ble),
              home=len(home),
              away=len(known) - len(home))

    return home


CLI_SCHEMA = {"ops": {
    "scan": {"params": ["--transport", "--refresh"]},
    "inspect": {"args": "ip", "params": ["--refresh"]},
    "list": {},
    "add": {"args": "name MAC"},
    "remove": {"args": "name"},
}}


def cli(argv):
    """Parse network command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="network", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("--transport", choices=["any", "wifi", "ble"], default="any")
    p.add_argument("--refresh", action="store_true",
                   help="force re-inspection; override the probe field in presence.json "
                        "(default: enrich — keep cached probes younger than PROBE_TTL)")
    p.add_argument("args", nargs="*")
    args = p.parse_args(argv)

    a = args.action
    try:
        if   a == "scan":    scan(args.transport, refresh=args.refresh)
        elif a == "inspect":
            if not args.args: print("  usage: network inspect <ip>")
            else:
                import json as _json
                print(_json.dumps(inspect(args.args[0], refresh=args.refresh), indent=2))
        elif a == "list":    list_devices()
        elif a == "add":
            if len(args.args) < 2: print("  usage: network add <name> <MAC>")
            else: add(args.args[0], args.args[1])
        elif a == "remove":
            if not args.args: print("  usage: network remove <name>")
            else: remove(args.args[0])
    except Exception as e:
        print(f"  ERROR: {e}")
