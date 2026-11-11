"""
tv — LG, Samsung, Android/Google TV, and Roku control

Usage (via mediactl; operations accept --tv NAME):
  tv list | on | off | status | pair
  tv app <name>
  tv apps
  tv volume <0-100>
  tv mute [on|off|toggle]
  tv input <name>
  tv inputs | fg
  tv key <key...>
  tv keys

Help: help tv | tv --help
"""

import argparse
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
import device as _device


# ── device config ────────────────────────────────────────────────────────────

_RUNTIME_FIELDS = {"paired", "apps", "current_app", "current_app_at"}
_SMART_BRANDS = {"android", "androidtv", "google", "lg", "webos", "samsung", "tizen", "roku"}

def _read_overlay():
    if os.path.exists(".out/tvs.json"):
        try:
            data = json.loads(open(".out/tvs.json").read())
            return {name: {key: value for key, value in row.items() if key in _RUNTIME_FIELDS}
                    for name, row in data.items()}
        except Exception:
            return {}
    return {}

def _write_overlay(name, **fields):
    overlay = _read_overlay()
    entry = overlay.setdefault(name, {})
    for k, v in fields.items():
        if k in _RUNTIME_FIELDS and v is not None:
            entry[k] = v
    os.makedirs(".out", exist_ok=True)
    open(".out/tvs.json", "w").write(json.dumps(overlay, indent=2))

def _tv_entry(row, overlay=None):
    entry = {"host": str(row.get("identifier") or "")}
    brand = row.get("brand") or row.get("source")
    if brand:        entry["brand"] = str(brand).lower()
    if row.get("mac"): entry["mac"]   = row["mac"]
    extra = (overlay if overlay is not None else _read_overlay()).get(row.get("name"))
    if isinstance(extra, dict):
        entry.update({k: v for k, v in extra.items() if v is not None})
    entry.setdefault("paired", entry.get("brand") not in _SMART_BRANDS)
    return entry

def _parse_tvs():
    """Configured TVs {name: {host, brand, mac, …}} from devices.json tvs[] (the device-picker
    registry), with the optional .out/tvs.json overlay merged in."""
    overlay = _read_overlay()
    return {row["name"]: _tv_entry(row, overlay)
            for row in _device.devices_read("tvs") if row.get("name")}

def _get_device(name):
    try:
        row = _device.devices_find("tvs", name, default_first=True)
    except KeyError:
        names = [r.get("name", "") for r in _device.devices_read("tvs") if r.get("name")]
        if not names:
            raise Exception("No TVs configured — add one in the dashboard TV tile (saved to .out/devices.json).")
        raise Exception(f"Unknown TV '{name}'. Known: {', '.join(names)}")
    return row["name"], _tv_entry(row)

def _out(name, suffix):
    os.makedirs(".out", exist_ok=True)
    return f".out/tv_{name}_{suffix}"

def _ts():
    return time.strftime("%H:%M:%S")


# ── shared helpers ───────────────────────────────────────────────────────────

def _run(coro):
    def _ignore_webos_errors(loop, context):
        exc = context.get("exception")
        try:
            from aiowebostv.exceptions import WebOsTvServiceNotFoundError
            if isinstance(exc, WebOsTvServiceNotFoundError):
                return
        except ImportError:
            pass
        loop.default_exception_handler(context)

    async def _wrapped():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_ignore_webos_errors)
        return await coro

    return asyncio.run(_wrapped())

def resolve_mac(host):
    """Best-effort MAC for a LAN host, resolved from its IP via ARP. '' if unknown.
    Used so the user never has to type a MAC for Wake-on-LAN."""
    if not host:
        return ""
    ip = str(host).split(":")[0]
    try:                                  # nudge the ARP cache with one ping
        import platform, subprocess
        flag = "-n" if platform.system().lower().startswith("win") else "-c"
        subprocess.run(["ping", flag, "1", ip], capture_output=True, timeout=2)
    except Exception:
        pass
    try:
        from getmac import get_mac_address
        return get_mac_address(ip=ip) or ""
    except Exception:
        return ""

def _wol(dev, name):
    try:
        from wakeonlan import send_magic_packet
    except ImportError:
        raise Exception("Wake-on-LAN requires: pip install wakeonlan")
    mac = dev.get("mac")
    if not mac:
        raise Exception(f"TV '{name}' has no MAC configured")
    send_magic_packet(mac)
    print(f"  WoL packet sent → {name} ({mac})")


# ── driver base ──────────────────────────────────────────────────────────────
# Canonical key vocabulary shared by every driver / the web remote:
#   up down left right ok back home exit menu info play pause
#   vol_up vol_down mute  0-9

class _Driver:
    KEYS = {}

    def __init__(self, name, dev):
        self.name, self.dev = name, dev
        self.host = dev.get("host")

    def _key(self, k):
        return self.KEYS.get(k.lower(), k.upper())

    def _na(self, op):
        print(f"  {op} — not supported on {self.dev.get('brand', '?')}")

    def power_on(self):          self._na("on")
    def power_off(self):         self._na("off")
    def send_key(self, k):       self._na("key")
    def send_keys(self, ks):
        for k in ks:
            self.send_key(k)
    def volume(self, level):     self._na("volume")
    def mute(self, state):       self._na("mute")
    def launch_app(self, app):   self._na("app")
    def list_apps(self):         self._na("apps")
    def foreground(self):        self._na("fg")
    def inputs(self):            self._na("inputs")
    def input_source(self, q):   self._na("input")
    def pair(self):              self._na("pair")
    def is_on(self):             return False
    def status(self):
        from logui import logui
        on = self.is_on()
        logui("tv.status", tv=self.name, on=on, paired=bool(self.dev.get("paired")))
        return on


# ── LG WebOS ─────────────────────────────────────────────────────────────────

class LgTV(_Driver):
    KEYS = {
        "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
        "ok": "ENTER", "enter": "ENTER", "back": "BACK", "exit": "EXIT",
        "home": "HOME", "menu": "MENU", "info": "INFO",
        "red": "RED", "green": "GREEN", "yellow": "YELLOW", "blue": "BLUE",
        "play": "PLAY", "pause": "PAUSE", "stop": "STOP", "rewind": "REWIND",
        "forward": "FASTFORWARD", "prev": "PREVIOUS", "next": "NEXT", "cc": "CC",
        "vol_up": "VOLUMEUP", "vol_down": "VOLUMEDOWN", "mute": "MUTE",
        **{str(d): str(d) for d in range(10)},
    }
    APPS = {
        "plex": "cdp-30", "youtube": "youtube.leanback.v4", "netflix": "netflix",
        "amazon": "amazon", "browser": "com.webos.app.browser", "livetv": "com.webos.app.livetv",
    }

    def _require(self):
        try:
            from aiowebostv import WebOsClient
            return WebOsClient
        except ImportError:
            raise Exception("LG TV control requires: pip install aiowebostv")

    def _keyfile(self):
        return _out(self.name, "key.txt")

    def _load_key(self):
        f = self._keyfile()
        return (open(f).read().strip() or None) if os.path.exists(f) else None

    def _save_key(self, key):
        open(self._keyfile(), "w").write(key or "")

    async def _connect(self, client_key):
        WebOsClient = self._require()
        client = WebOsClient(self.host, client_key)
        await client.connect()
        if client.client_key and client.client_key != client_key:
            self._save_key(client.client_key)
        return client

    @asynccontextmanager
    async def _session(self):
        if not self.host:
            raise Exception(f"TV '{self.name}' has no host configured (MAC-only entry)")
        client = await self._connect(self._load_key())
        try:
            yield client
        finally:
            await client.disconnect()

    def power_on(self):
        _wol(self.dev, self.name)

    def power_off(self):
        async def _():
            async with self._session() as c:
                await c.power_off(); print("  off")
        _run(_())

    def send_key(self, k):
        btn = self._key(k)
        async def _():
            async with self._session() as c:
                await c.button(btn); print(f"  key: {btn}")
        _run(_())

    def send_keys(self, ks):
        btns = [self._key(k) for k in ks]
        async def _():
            async with self._session() as c:
                for b in btns:
                    await c.button(b); print(f"  key: {b}")
        _run(_())

    def volume(self, level):
        async def _():
            async with self._session() as c:
                await c.set_volume(int(level)); print(f"  volume: {level}")
        _run(_())

    def mute(self, state):
        async def _():
            async with self._session() as c:
                if state == "toggle":
                    info = await c.get_volume(); val = not info.get("muted", False)
                else:
                    val = state == "on"
                await c.set_mute(val); print(f"  mute: {val}")
        _run(_())

    def launch_app(self, app):
        app_id = self.APPS.get(app.lower(), app)
        async def _():
            async with self._session() as c:
                await c.launch_app(app_id); print(f"  launched: {app} ({app_id})")
        _run(_())

    def list_apps(self):
        async def _():
            from logui import logui
            async with self._session() as c:
                for a in (await c.get_apps()) or []:
                    print(f"{a.get('id','?')}\t{a.get('title','?')}")
                    logui("tv.app", id=a.get("id", ""), title=a.get("title", ""))
        _run(_())

    def foreground(self):
        async def _():
            async with self._session() as c:
                res  = await c.request("ssap://com.webos.applicationManager/getForegroundAppInfo")
                apps = await c.get_apps() or []
            app_id = (res or {}).get("appId", "")
            title  = next((a.get("title", "") for a in apps if a.get("id") == app_id), app_id)
            _write_overlay(self.name, current_app=app_id, current_app_at=time.time())
            return {"id": app_id, "title": title}
        return _run(_())

    def inputs(self):
        async def _():
            async with self._session() as c:
                for i in (await c.get_inputs()) or []:
                    print(f"  {i.get('id','?'):<20} {i.get('label','?')}")
        _run(_())

    def input_source(self, q):
        async def _():
            async with self._session() as c:
                inputs = await c.get_inputs()
                match  = next((i for i in (inputs or []) if q.lower() in i.get("label", "").lower()), None)
                if not match:
                    print(f"  ERROR: input '{q}' not found"); return
                await c.set_input(match["id"]); print(f"  input: {match['label']}")
        _run(_())

    def is_on(self):
        async def _():
            if not self.host:
                return False
            try:
                client = await asyncio.wait_for(self._connect(self._load_key()), timeout=15.0)
                await client.disconnect()
                return True
            except Exception:
                return False
        return _run(_())

    def pair(self):
        if not self.host:
            raise Exception(f"TV '{self.name}' has no host configured")
        self._save_key("")          # wipe stored key → re-triggers the on-screen prompt
        _run(self._connect(None))
        print(f"  paired {self.name}")


# ── Samsung Tizen ────────────────────────────────────────────────────────────

class SamsungTV(_Driver):
    KEYS = {
        "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
        "ok": "KEY_ENTER", "enter": "KEY_ENTER", "back": "KEY_RETURN", "exit": "KEY_EXIT",
        "home": "KEY_HOME", "menu": "KEY_MENU", "info": "KEY_INFO",
        "play": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP",
        "rewind": "KEY_REWIND", "forward": "KEY_FF", "prev": "KEY_REWIND", "next": "KEY_FF",
        "vol_up": "KEY_VOLUP", "vol_down": "KEY_VOLDOWN", "mute": "KEY_MUTE",
        **{str(d): f"KEY_{d}" for d in range(10)},
    }

    def _client(self):
        try:
            from samsungtvws import SamsungTVWS
        except ImportError:
            raise Exception("Samsung TV control requires: pip install samsungtvws")
        if not self.host:
            raise Exception(f"TV '{self.name}' has no host configured")
        return SamsungTVWS(host=self.host, port=8002,
                           token_file=_out(self.name, "token.txt"), name="mediactl")

    def power_on(self):
        _wol(self.dev, self.name)

    def power_off(self):
        self._client().send_key("KEY_POWER"); print("  off")

    def send_key(self, k):
        btn = self._key(k); self._client().send_key(btn); print(f"  key: {btn}")

    def mute(self, state):
        self._client().send_key("KEY_MUTE"); print("  mute: toggle")

    def volume(self, level):
        self._na("absolute volume (use vol_up/vol_down)")

    def launch_app(self, app):
        self._client().run_app(app); print(f"  launched: {app}")

    def list_apps(self):
        from logui import logui
        for a in (self._client().app_list() or []):
            aid, title = a.get("appId", ""), a.get("name", "")
            print(f"{aid}\t{title}"); logui("tv.app", id=aid, title=title)

    def is_on(self):
        try:
            info = self._client().rest_device_info()
            return bool(info)
        except Exception:
            return False

    def pair(self):
        # First connect raises the on-screen Allow prompt; token persists to token_file.
        self._client().send_key("KEY_HOME")
        print(f"  paired {self.name} (accept the prompt on the TV, then retry)")


# ── Google / Android TV ──────────────────────────────────────────────────────

class GoogleTV(_Driver):
    KEYS = {
        "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
        "ok": "DPAD_CENTER", "enter": "DPAD_CENTER", "back": "BACK", "exit": "BACK",
        "home": "HOME", "menu": "MENU", "info": "INFO",
        "play": "MEDIA_PLAY_PAUSE", "pause": "MEDIA_PLAY_PAUSE", "stop": "MEDIA_STOP",
        "rewind": "MEDIA_REWIND", "forward": "MEDIA_FAST_FORWARD",
        "prev": "MEDIA_PREVIOUS", "next": "MEDIA_NEXT",
        "vol_up": "VOLUME_UP", "vol_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE",
        "power": "POWER", "sleep": "SLEEP",
        **{str(d): str(d) for d in range(10)},
    }

    # The Android TV remote protocol cannot enumerate installed apps, so the picker
    # is backed by this curated catalog (title → package). Extend as needed.
    APPS = {
        "Plex":        "com.plexapp.android",
        "Jellyfin":    "org.jellyfin.androidtv",
        "YouTube":     "com.google.android.youtube.tv",
        "Spotify":     "com.spotify.tv.android",
        "Netflix":     "com.netflix.ninja",
        "Prime Video": "com.amazon.amazonvideo.livingroom",
        "Disney+":     "com.disney.disneyplus",
        "Max":         "com.wbd.stream",
        "Apple TV":    "com.apple.atve.androidtv.appletv",
        "Twitch":      "tv.twitch.android.app",
    }
    APP_LINKS = {
        "com.plexapp.android":                    "plex://home",
        "org.jellyfin.androidtv":                 "market://details?id=org.jellyfin.androidtv",
        "com.google.android.youtube.tv":          "https://www.youtube.com",
        "com.netflix.ninja":                      "https://www.netflix.com/title",
        "com.spotify.tv.android":                 "spotify://",
        "com.amazon.amazonvideo.livingroom":      "https://app.primevideo.com",
        "com.wbd.stream":                         "https://play.max.com",
    }

    def _remote(self):
        try:
            from androidtvremote2 import AndroidTVRemote
        except ImportError:
            raise Exception("Android/Google TV control requires: pip install androidtvremote2")
        if not self.host:
            raise Exception(f"TV '{self.name}' has no host configured")
        return AndroidTVRemote("mediactl", _out(self.name, "cert.pem"),
                               _out(self.name, "key.pem"), self.host)

    async def _connected(self):
        r = self._remote()
        await r.async_generate_cert_if_missing()
        try:
            await asyncio.wait_for(r.async_connect(), timeout=3)
        except Exception:
            r.disconnect()
            raise Exception(f"'{self.name}' unreachable or not paired — run: tv pair --tv {self.name}")
        return r

    def _confirm_power(self, want_on, tries=3, delay=3):
        for i in range(tries):
            if self.is_on() == want_on:
                return True
            if i < tries - 1:
                time.sleep(delay)
        return False

    def _ir_power(self, power_dev, want_on):
        import ir
        ir.send_code(power_dev, "Power")
        on = self._confirm_power(want_on)
        if not on:
            ir.send_code(power_dev, "Power")
            on = self._confirm_power(want_on)
        print(f"  IR power → {power_dev} ({'on' if on else 'off'})")

    def power_on(self):
        if self.is_on():
            return
        power_dev = self.dev.get("power_device")
        if power_dev:
            self._ir_power(power_dev, True); return
        if self.dev.get("mac"):
            _wol(self.dev, self.name)
            print("  on" if self._confirm_power(True) else "  WARNING: no response after WoL")
            return
        if not self.host:
            return
        async def _():
            r = await self._connected()
            try: r.send_key_command("POWER")
            finally: r.disconnect()
        _run(_())
        print("  on" if self._confirm_power(True) else "  WARNING: no response after power key")

    def power_off(self):
        if not self.is_on():
            return
        power_dev = self.dev.get("power_device")
        if power_dev:
            self._ir_power(power_dev, False); return
        async def _():
            r = await self._connected()
            try: r.send_key_command("POWER")
            finally: r.disconnect()
        _run(_())
        print("  off" if self._confirm_power(False) else "  WARNING: still reachable after power key")

    def send_key(self, k):
        btn = self._key(k)
        async def _():
            r = await self._connected()
            try: r.send_key_command(btn); print(f"  key: {btn}")
            finally: r.disconnect()
        _run(_())

    def mute(self, state):
        self.send_key("mute")

    def volume(self, level):
        self._na("absolute volume (use vol_up/vol_down)")

    async def _launch_app(self, remote, app):
        app_id = next((package for title, package in self.APPS.items()
                       if title.lower() == str(app).lower()), app)
        link = self.APP_LINKS.get(app_id, app_id)
        remote.send_launch_app_command(link)
        if link.startswith("market://details?"):
            # Market links assume an installed app with Open focused.
            await asyncio.sleep(3)
            if remote.current_app != "com.android.vending":
                raise Exception("Play Store did not open")
            remote.send_key_command("DPAD_CENTER")
        await asyncio.sleep(0.5)
        return app_id

    def launch_app(self, app):
        async def _():
            r = await self._connected()
            try:
                app_id = await self._launch_app(r, app)
                print(f"  launched: {app} ({app_id})")
            finally: r.disconnect()
        _run(_())

    def list_apps(self):
        from logui import logui
        apps = [{"id": pkg, "title": title} for title, pkg in self.APPS.items()]
        # save to tvs.json
        overlay = _read_overlay()
        entry = overlay.setdefault(self.name, {})
        entry["apps"] = apps
        open(".out/tvs.json", "w").write(json.dumps(overlay, indent=2))
        # print
        max_title = max(len(a["title"]) for a in apps)
        for a in apps:
            print(f"  {a['title']:<{max_title}}  {a['id']}")
        # single event
        logui("tv.apps", tv=self.name, apps=[a["title"] for a in apps])

    def foreground(self):
        async def _():
            r = await self._connected()
            try:
                for _ in range(10):                 # let the initial state arrive
                    if r.current_app:
                        break
                    await asyncio.sleep(0.1)
                app_id = r.current_app or ""
            finally:
                r.disconnect()
            title = next((t for t, p in self.APPS.items() if p == app_id), app_id)
            _write_overlay(self.name, current_app=app_id, current_app_at=time.time())
            return {"id": app_id, "title": title}
        return _run(_())

    def is_on(self):
        async def _():
            try:
                r = await self._connected()
                on = bool(r.is_on)
                r.disconnect()
                return on
            except Exception:
                return False
        return _run(_())

    def pair(self):
        # Runs as a long-running process (web: /api/start) so the on-screen code can be
        # fed back over stdin. Emits tv.pair events the dashboard listens for. The same
        # flow works from a terminal — type the code at the prompt.
        async def _():
            r = self._remote()
            await r.async_generate_cert_if_missing()
            await r.async_start_pairing()
            print(f"[{_ts()}] tv.pair status=waiting_code tv={self.name}", flush=True)
            print("  enter the code shown on the TV, then press Enter:", flush=True)
            try:
                code = input().strip()
            except EOFError:
                raise Exception(f"pairing cancelled before a code was entered (tv {self.name})")
            try:
                await r.async_finish_pairing(code)
            except Exception as e:
                msg = str(e).replace("\n", " ")
                print(f"[{_ts()}] tv.pair status=error tv={self.name} msg={msg}", flush=True)
                raise
            print(f"[{_ts()}] tv.pair status=paired tv={self.name}", flush=True)
            try:
                r.disconnect()
            except Exception:
                pass
        _run(_())


_GOOGLE_REMOTES = {}


async def command_async(args):
    """Handle fast Google TV commands in-process; return None for other commands."""
    if len(args) < 2 or args[0] != "tv" or args[1] not in {"connect", "key", "app", "off"}:
        return None
    action = args[1]
    values = args[2:]
    if "--tv" in values:
        i = values.index("--tv")
        values = values[:i] + values[i + 2:]
    if action in {"key", "app"} and not values:
        return None
    tv_name = args[args.index("--tv") + 1] if "--tv" in args else None
    name, dev = _get_device(tv_name)
    if (dev.get("brand") or "").lower() not in {"google", "android", "androidtv"}:
        return None

    remote = _GOOGLE_REMOTES.get(name)
    protocol = getattr(remote, "_remote_message_protocol", None)
    if remote is None or protocol is None or protocol.transport.is_closing():
        if remote:
            remote.disconnect()
        remote = GoogleTV(name, dev)._remote()
        await remote.async_generate_cert_if_missing()
        await remote.async_connect()
        remote.keep_reconnecting()
        _GOOGLE_REMOTES[name] = remote

    if action == "key":
        driver = GoogleTV(name, dev)
        for value in values:
            remote.send_key_command(driver._key(value))
    elif action == "app":
        await GoogleTV(name, dev)._launch_app(remote, values[0])
    elif action == "off":
        if remote.is_on:
            remote.send_key_command("POWER")
    await asyncio.sleep(0)
    return action


# ── Roku (ECP over HTTP) ─────────────────────────────────────────────────────

class RokuTV(_Driver):
    KEYS = {
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "ok": "Select", "enter": "Select", "back": "Back", "exit": "Home",
        "home": "Home", "menu": "Info", "info": "Info",
        "play": "Play", "pause": "Play", "stop": "Stop",
        "rewind": "Rev", "forward": "Fwd", "prev": "Rev", "next": "Fwd",
        "vol_up": "VolumeUp", "vol_down": "VolumeDown", "mute": "VolumeMute",
    }

    def _base(self):
        if not self.host:
            raise Exception(f"TV '{self.name}' has no host configured")
        host = self.host if ":" in self.host else f"{self.host}:8060"
        return f"http://{host}"

    def _req(self):
        try:
            import requests
            return requests
        except ImportError:
            raise Exception("Roku control requires: pip install requests")

    def power_on(self):
        self._req().post(f"{self._base()}/keypress/PowerOn", timeout=5); print("  on")

    def power_off(self):
        self._req().post(f"{self._base()}/keypress/PowerOff", timeout=5); print("  off")

    def send_key(self, k):
        btn = self._key(k)
        self._req().post(f"{self._base()}/keypress/{btn}", timeout=5); print(f"  key: {btn}")

    def mute(self, state):
        self.send_key("mute")

    def volume(self, level):
        self._na("absolute volume (use vol_up/vol_down)")

    def launch_app(self, app):
        self._req().post(f"{self._base()}/launch/{app}", timeout=5); print(f"  launched: {app}")

    def list_apps(self):
        from logui import logui
        import re
        r = self._req().get(f"{self._base()}/query/apps", timeout=5)
        for m in re.finditer(r'id="([^"]+)"[^>]*>([^<]+)</app>', r.text):
            aid, title = m.group(1), m.group(2)
            print(f"{aid}\t{title}"); logui("tv.app", id=aid, title=title)

    def is_on(self):
        try:
            r = self._req().get(f"{self._base()}/query/device-info", timeout=5)
            return r.status_code == 200
        except Exception:
            return False


# ── IR (Smart Life infrared remote, one-way: no status/apps/pairing) ───────────

class IrTV(_Driver):
    KEYS = {
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "ok": "OK", "enter": "OK", "back": "Back", "exit": "Back",
        "home": "Home", "menu": "Menu",
        "vol_up": "Volume+", "vol_down": "Volume-",
        "ch_up": "Channel+", "ch_down": "Channel-",
    }

    def _key(self, k):
        return self.KEYS.get(k.lower(), k)

    def _send(self, code):
        import ir
        if not self.host:
            raise Exception(f"IR remote '{self.name}' has no device id")
        ir.send_code(self.host, code); print(f"  key: {code}")

    def power_on(self):    self._send("Power")
    def power_off(self):   self._send("Power")
    def send_key(self, k): self._send(self._key(k))
    def mute(self, state): self._na("mute")


# ── dispatch ─────────────────────────────────────────────────────────────────

_DRIVERS = {
    "lg": LgTV, "webos": LgTV,
    "samsung": SamsungTV, "tizen": SamsungTV,
    "google": GoogleTV, "androidtv": GoogleTV, "android": GoogleTV,
    "roku": RokuTV,
    "ir": IrTV,
}

def _select_driver(tv):
    name, dev = _get_device(tv)
    brand = (dev.get("brand") or "lg").lower()
    cls = _DRIVERS.get(brand)
    if not cls:
        known = ", ".join(sorted(set(_DRIVERS)))
        raise Exception(f"Unknown brand '{brand}' for TV '{name}'. Known: {known}")
    return cls(name, dev)


# ── CLI ops (called from mediactl.py) ────────────────────────────────────────

def list_tvs():
    """List all configured TVs."""
    devices = _parse_tvs()
    if not devices:
        print("  (no TVs configured)"); return
    max_name = max(len(n) for n in devices)
    for name, dev in devices.items():
        brand = dev.get("brand", "lg")
        parts = [p for p in (dev.get("host"), dev.get("mac")) if p]
        descriptor = "  ".join(parts) if parts else "(no address)"
        print(f"    {name:<{max_name}}  [{brand}]  {descriptor}")

def on(tv):                         _select_driver(tv).power_on()
def off(tv):                        _select_driver(tv).power_off()
def open_app(name, tv):             _select_driver(tv).launch_app(name)
def list_apps(tv):                  _select_driver(tv).list_apps()
def volume(level, tv):              _select_driver(tv).volume(level)
def mute(state="toggle", tv=None):  _select_driver(tv).mute(state)
def input_source(name, tv):         _select_driver(tv).input_source(name)
def inputs_list(tv):                _select_driver(tv).inputs()
def current_app(tv, emit=True):
    result = _select_driver(tv).foreground()
    if emit and result is not None:
        from logui import logui
        print(json.dumps(result))
        logui("tv.fg", **result)
    return result

def key(names, tv):                 _select_driver(tv).send_keys(names)
def keys(tv):
    names = list(_select_driver(tv).KEYS)
    width = max((len(name) for name in names), default=0) + 2
    for i in range(0, len(names), 3):
        print("  " + "".join(f"{name:<{width}}" for name in names[i:i + 3]).rstrip())
def status(tv):
    if tv is None:
        tv = next((name for name, info in _parse_tvs().items()
                   if info.get("brand") in _SMART_BRANDS), None)
    return _select_driver(tv).status()
def pair(tv):
    name, _ = _get_device(tv)
    try:
        _select_driver(tv).pair()
        _write_overlay(name, paired=True)
        try:
            _select_driver(tv).list_apps()
        except Exception as e:
            from logui import logui
            logui("tv.apps", tv=name, error=str(e))
    except Exception:
        _write_overlay(name, paired=False)
        raise
CLI_SCHEMA = {"ops": {
    "list": {},
    "on": {"params": ["--tv"]},
    "off": {"params": ["--tv"]},
    "app": {"args": "name", "params": ["--tv"]},
    "apps": {"params": ["--tv"]},
    "volume": {"args": "0-100", "params": ["--tv"]},
    "mute": {"params": ["--tv"]},
    "input": {"args": "name", "params": ["--tv"]},
    "inputs": {"params": ["--tv"]},
    "fg": {"params": ["--tv"]},
    "key": {"args": "up|down|left|right|ok|back|home|play|pause|...", "params": ["--tv"]},
    "keys": {"params": ["--tv"]},
    "status": {"params": ["--tv"]},
    "pair": {"params": ["--tv"]},
}}


def cli(argv):
    """Parse tv command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="tv", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--tv", default=None, dest="tv_name")
    args = p.parse_args(argv)

    a = args.action; r = args.args; t = args.tv_name
    try:
        if   a == "list":      list_tvs()
        elif a == "on":        on(tv=t)
        elif a == "off":       off(tv=t)
        elif a == "app":       open_app(r[0], tv=t) if r else print("  usage: tv app <name>")
        elif a == "apps":      list_apps(tv=t)
        elif a == "volume":    volume(r[0], tv=t) if r else print("  usage: tv volume <0-100>")
        elif a == "mute":      mute(r[0] if r else "toggle", tv=t)
        elif a == "input":     input_source(r[0], tv=t) if r else print("  usage: tv input <name>")
        elif a == "inputs":    inputs_list(tv=t)
        elif a == "fg":        current_app(tv=t)
        elif a == "key":       key(r, tv=t) if r else print("  usage: tv key <key> [key ...]")
        elif a == "keys":      keys(tv=t)
        elif a == "status":    status(tv=t)
        elif a == "pair":      pair(tv=t)
    except Exception as e:
        print(f"  ERROR: {e}")
