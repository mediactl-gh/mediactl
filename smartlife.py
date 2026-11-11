import json
from pathlib import Path
from logui import logui

_OUT  = Path(__file__).parent / ".out"
_FILE = _OUT / "smartlife.json"

# Home Assistant's Tuya app identity — tuya_sharing's QR flow authorizes against it.
_CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
_SCHEMA    = "haauthorize"
_SESSION_KEYS = ("t", "uid", "expire_time", "access_token",
                 "refresh_token", "terminal_id", "endpoint")

_api = None


# ── Session ────────────────────────────────────────────────────────────────────

def connected():
    return _FILE.exists()


def _load():
    return json.loads(_FILE.read_text(encoding="utf-8"))


def _save(info, user_code):
    global _api
    keep = {k: info.get(k) for k in _SESSION_KEYS}
    keep["user_code"] = user_code
    _OUT.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    _api = None


def _client():
    """CustomerApi from smartlife.json (None if not connected). Refreshed tokens persist back."""
    global _api
    if _api: return _api
    if not connected(): raise RuntimeError("Smart Life not connected — run: lights connect <user_code>")
    info = _load()
    from tuya_sharing import SharingTokenListener
    from tuya_sharing.customerapi import CustomerApi, CustomerTokenInfo
    class _Persist(SharingTokenListener):
        def update_token(self, token_info):
            info.update(token_info)
            _FILE.write_text(json.dumps(info, indent=2), encoding="utf-8")
    _api = CustomerApi(CustomerTokenInfo(info), _CLIENT_ID,
                       info["user_code"], info["endpoint"], _Persist())
    return _api


def connect(user_code):
    """QR-login lifecycle: emit token, poll until scanned, save session. Returns bool."""
    import time
    from tuya_sharing import LoginControl
    lc = LoginControl()
    r  = lc.qr_code(_CLIENT_ID, _SCHEMA, user_code)
    if not r.get("success"):
        logui.err("smartlife.error", msg=r.get("msg", "qr request failed")); return False
    token = r["result"]["qrcode"]
    # The app reads the QR as an authorize-login only with this scheme; a bare token is
    # treated as a device-pairing QR ("Scan the device's QR code"). Poll with raw token.
    logui("smartlife.qr", token=f"tuyaSmart--qrLogin?token={token}")
    for _ in range(90):
        time.sleep(2)
        ok, info = lc.login_result(token, _CLIENT_ID, user_code)
        if ok:
            _save(info, user_code)
            logui("smartlife.connected")
            return True
    logui.err("smartlife.error", msg="QR scan timed out")
    return False


# ── Ops (contract-identical to tuya.cmd / device_status / device_specs / scan) ───

def cmd(device_id, commands):
    r = _client().post(f"/v1.1/m/thing/{device_id}/commands", None, {"commands": commands})
    if not r.get("success"): raise Exception(f"cmd failed: {r.get('msg')}")


def device_specs(device_id):
    r = _client().get(f"/v1.1/m/life/{device_id}/specifications")
    if not r.get("success"): raise Exception(r.get("msg"))
    return (r.get("result") or {}).get("functions", [])


def device_status(device_id):
    r = _client().get("/v1.0/m/life/ha/devices/detail", {"devIds": device_id})
    if not r.get("success"): raise Exception(r.get("msg"))
    devs = r.get("result") or []
    if not devs: raise Exception("device not found")
    return {i["code"]: i["value"] for i in devs[0].get("status", []) if "code" in i and "value" in i}


def scan():
    api  = _client()
    out  = []
    homes = api.get("/v1.0/m/life/users/homes")
    for h in homes.get("result") or []:
        r = api.get("/v1.0/m/life/ha/home/devices", {"homeId": str(h["ownerId"])})
        out.extend(r.get("result") or [])
    return out
