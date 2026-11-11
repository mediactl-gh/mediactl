"""
users — user accounts, access tags, and pairing

Usage:
  users list | codes
  users show <name|id>
  users revoke <name|id>
  users reinstate <name|id>
  users tag <name|id> <tag>
  users untag <name|id> <tag>
  users pair [--tags TAG[,TAG...]]

Pairing defaults to the user tag.

Help: help users | users --help
"""

from __future__ import annotations

import argparse

from logui import logui

import os, json, hashlib, secrets, string
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

try:
    from fastapi import Request, HTTPException, Response
    from fastapi.responses import JSONResponse
except ImportError:
    pass  # imported only when running under web.py

# ── Paths ─────────────────────────────────────────────────────────────────

HERE       = Path(__file__).parent
_OUT       = HERE / ".out"
_USERS_F   = _OUT / "users.json"
_CODES_F = _OUT / "pair_codes.json"
_BOOTSTRAP_TOKEN_F = _OUT / ".admin_token"
_COOKIE    = "mediactl_token"

# ── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def _logui(hint: str, **kwargs):
    """Write a logui line to events.log if available."""
    try:
        log = _OUT / "events.log"
        _OUT.mkdir(parents=True, exist_ok=True)
        parts = " ".join(f'{k}="{v}"' for k, v in kwargs.items())
        line  = f"[{_now_hms()}] {hint} {parts}".rstrip()
        with open(log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── users.json ─────────────────────────────────────────────────────────────

def users_load() -> list[dict]:
    try:
        return json.loads(_USERS_F.read_text(encoding="utf-8"))
    except Exception:
        return []

def users_save(users: list[dict]):
    _OUT.mkdir(parents=True, exist_ok=True)
    _USERS_F.write_text(json.dumps(users, indent=2), encoding="utf-8")

def users_find_by_token(token: str) -> Optional[dict]:
    h = _hash(token)
    for u in users_load():
        if not u.get("revoked") and u.get("token_hash") == h:
            return u
    return None

def users_find_by_id(user_id: str) -> Optional[dict]:
    for u in users_load():
        if u["id"] == user_id:
            return u
    return None

def users_find_by_name(name: str) -> Optional[dict]:
    for u in users_load():
        if u["name"] == name:
            return u
    return None

def users_find_admin() -> Optional[dict]:
    for u in users_load():
        if not u.get("revoked") and "admin" in u.get("tags", []):
            return u
    return None

def users_create(name: str, avatar: str, tags: list[str], token: str) -> dict:
    import uuid
    user = {
        "id":         str(uuid.uuid4()),
        "name":       name,
        "avatar":     avatar,
        "tags":       tags,
        "token_hash": _hash(token),
        "added_at":   _now_iso(),
        "last_seen":  _now_iso(),
        "revoked":    False,
    }
    lst = users_load()
    lst.append(user)
    users_save(lst)
    return user

def users_touch(user_id: str):
    """Update last_seen timestamp."""
    lst = users_load()
    for u in lst:
        if u["id"] == user_id:
            u["last_seen"] = _now_iso()
            break
    users_save(lst)

def users_update_token(user_id: str, plain_token: str):
    lst = users_load()
    for u in lst:
        if u["id"] == user_id:
            u["token_hash"] = _hash(plain_token)
            break
    users_save(lst)

def users_revoke(user_id: str):
    lst = users_load()
    for u in lst:
        if u["id"] == user_id:
            u["revoked"] = True
            _logui("web.user.revoked", name=u.get("name", "?"), id=user_id)
            break
    users_save(lst)

def users_any_exist() -> bool:
    return bool(users_load())


def _codes_load() -> list[dict]:
    try:
        return json.loads(_CODES_F.read_text(encoding="utf-8"))
    except Exception:
        return []


def _codes_save(codes: list[dict]):
    _OUT.mkdir(parents=True, exist_ok=True)
    _CODES_F.write_text(json.dumps(codes, indent=2), encoding="utf-8")


def users_generate_code(tags: list[str]) -> str:
    """Generate a new pairing code, replacing any existing unused code."""
    alphabet = string.ascii_uppercase + string.digits
    code     = "".join(secrets.choice(alphabet) for _ in range(6))
    _codes_save([{"code": code, "role_tags": tags, "used": False}])
    logui("web.pair.generated", tags=" ".join(tags))
    return code


def users_redeem_code(code: str, name: str, avatar: str) -> Optional[tuple[dict, str]]:
    """
    Validate code, create user, mark code used.
    Returns (user_dict, plain_token) or None if code invalid/already used.
    """
    code  = code.strip().upper()
    codes = _codes_load()
    entry = next((c for c in codes if c["code"] == code and not c.get("used")), None)
    if not entry:
        return None

    token = secrets.token_urlsafe(32)
    user  = users_create(name, avatar, entry.get("role_tags", ["user"]), token)
    logui("web.user-created", name=name, tags=" ".join(user["tags"]))

    for c in codes:
        if c["code"] == code:
            c["used"] = True
            break
    _codes_save(codes)

    logui("web.pair.redeemed", name=name, tags=" ".join(user["tags"]))
    return user, token


async def api_users_redeem(request):
    """
    Public — no auth required.
    New device submits pairing code + name + avatar, gets a token cookie back.
    """
    from web import dashboard_create
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    body   = await request.json()
    code   = body.get("code", "").strip()
    name   = body.get("name", "").strip()
    avatar = body.get("avatar", "🙂")
    if not code or not name:
        raise HTTPException(400, "code and name required")
    result = users_redeem_code(code, name, avatar)
    if not result:
        raise HTTPException(403, "invalid or already-used code")
    user_obj, token = result
    dashboard_create(user_obj)
    safe = {k: v for k, v in user_obj.items() if k != "token_hash"}
    response = JSONResponse({"ok": True, "user": safe})
    users_set_cookie(response, token)
    return response


def bootstrap_admin() -> None:
    """
    Called once at server startup.
    If no users exist, creates Admin + dashboard and writes the plain token
    to .out/.admin_token for the first loopback visit to consume.
    """
    from web import dashboard_create
    if users_any_exist():
        pass
    else:
        plain_token = secrets.token_urlsafe(32)
        user = users_create("Admin", "🖥️", ["admin"], plain_token)
        logui("web.user-created", name="Admin", tags="admin")
        users_touch(user["id"])
        dashboard_create(user)
        _OUT.mkdir(parents=True, exist_ok=True)
        _BOOTSTRAP_TOKEN_F.write_text(plain_token, encoding="utf-8")



def consume_bootstrap_token() -> Optional[str]:
    """
    Read and delete the bootstrap token. Returns the plain token or None.
    One-shot — file is removed on first successful read.
    """
    if not _BOOTSTRAP_TOKEN_F.exists():
        return None
    try:
        token = _BOOTSTRAP_TOKEN_F.read_text(encoding="utf-8").strip()
        _BOOTSTRAP_TOKEN_F.unlink()
        return token or None
    except Exception:
        return None

# ── FastAPI auth dependency ────────────────────────────────────────────────

def current_user(token: Optional[str], client_ip: str, generate: bool = False, tags: str = "user") -> tuple[dict, Optional[str]]:
    if token:
        user = users_find_by_token(token)
        if user:
            users_touch(user["id"])
            _logui("web.user-connected", name=user["name"])
            safe = {k: v for k, v in user.items() if k != "token_hash"}
            if generate:
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
                safe["pair_code"] = users_generate_code(tag_list)
            return safe, None

    bootstrap_token = consume_bootstrap_token()
    if bootstrap_token:
        user = users_find_by_token(bootstrap_token)
        if user:
            users_touch(user["id"])
            safe = {k: v for k, v in user.items() if k != "token_hash"}
            return safe, bootstrap_token

    if client_ip in ("127.0.0.1", "::1"):
        admin = users_find_admin()
        if admin:
            plain_token = secrets.token_urlsafe(32)
            users_update_token(admin["id"], plain_token)
            users_touch(admin["id"])
            safe = {k: v for k, v in admin.items() if k != "token_hash"}
            return safe, plain_token

    raise PermissionError("Unauthorized")


async def users_require(request: Request) -> dict:
    """
    FastAPI dependency. Returns the authenticated user dict or raises 401.
    """
    token = request.cookies.get(_COOKIE)
    if token:
        user = users_find_by_token(token)
        if user:
            users_touch(user["id"])
            return user
    raise HTTPException(status_code=401, detail="Unauthorized")

def users_set_cookie(response: Response, token: str):
    response.set_cookie(
        key      = _COOKIE,
        value    = token,
        httponly = True,
        samesite = "lax",
        max_age  = 60 * 60 * 24 * 365,  # 1 year
    )

def users_clear_cookie(response: Response):
    response.delete_cookie(_COOKIE)


def users_reinstate(user_id: str):
    lst = users_load()
    for u in lst:
        if u["id"] == user_id:
            u["revoked"] = False
            _logui("web.user.reinstated", name=u.get("name", "?"), id=user_id)
            break
    users_save(lst)


def users_tag(user_id: str, tag: str):
    lst = users_load()
    for u in lst:
        if u["id"] == user_id:
            if tag not in u["tags"]:
                u["tags"].append(tag)
            _logui("web.user.tagged", name=u.get("name", "?"), tag=tag)
            break
    users_save(lst)


def users_untag(user_id: str, tag: str):
    lst = users_load()
    for u in lst:
        if u["id"] == user_id:
            u["tags"] = [t for t in u["tags"] if t != tag]
            _logui("web.user.untagged", name=u.get("name", "?"), tag=tag)
            break
    users_save(lst)


# ── CLI helpers ────────────────────────────────────────────────────────────

def _find_user(name_or_id: str) -> Optional[dict]:
    """Match by id prefix or case-insensitive name."""
    lst = users_load()
    needle = name_or_id.lower()
    for u in lst:
        if u["id"].startswith(needle) or u["name"].lower() == needle:
            return u
    return None


def _fmt_user(u: dict, verbose: bool = False) -> str:
    status  = "revoked" if u.get("revoked") else "active"
    tags    = ",".join(u.get("tags", [])) or "—"
    seen    = u.get("last_seen", "never")[:16].replace("T", " ")
    line    = f"  {u['avatar']}  {u['name']:<18} [{tags}]  {status:<8}  last={seen}"
    if verbose:
        line += f"\n     id={u['id']}\n     added={u.get('added_at','?')[:16].replace('T',' ')}"
    return line


# ── CLI ops (called from mediactl.py dispatch) ────────────────────────────

def cli_list():
    lst = users_load()
    if not lst:
        print("  no users registered")
        _logui("users.list", count=0)
        return
    print(f"  {'avatar':<4} {'name':<18} {'tags':<16} {'status':<8}  last_seen")
    print("  " + "─" * 64)
    for u in lst:
        print(_fmt_user(u))
    _logui("users.list", count=len(lst))


def cli_show(name_or_id: str):
    from web import dashboard_exists
    u = _find_user(name_or_id)
    if not u:
        print(f"  user not found: {name_or_id}")
        _logui("users.show", error="not_found", query=name_or_id)
        return
    dash   = dashboard_exists(u["name"])
    status = "revoked" if u.get("revoked") else "active"
    print(f"  {u['avatar']}  {u['name']}")
    print(f"     id       : {u['id']}")
    print(f"     tags     : {', '.join(u.get('tags', [])) or '—'}")
    print(f"     status   : {status}")
    print(f"     added    : {u.get('added_at','?')[:16].replace('T',' ')}")
    print(f"     last_seen: {u.get('last_seen','?')[:16].replace('T',' ')}")
    print(f"     dashboard: {'saved' if dash else 'default'}")
    _logui("users.show", name=u["name"], id=u["id"])


def cli_revoke(name_or_id: str):
    u = _find_user(name_or_id)
    if not u:
        print(f"  user not found: {name_or_id}")
        _logui("users.revoke", error="not_found", query=name_or_id)
        return
    if u.get("revoked"):
        print(f"  {u['name']} is already revoked")
        return
    users_revoke(u["id"])
    print(f"  revoked: {u['avatar']} {u['name']}")


def cli_reinstate(name_or_id: str):
    u = _find_user(name_or_id)
    if not u:
        print(f"  user not found: {name_or_id}")
        _logui("users.reinstate", error="not_found", query=name_or_id)
        return
    if not u.get("revoked"):
        print(f"  {u['name']} is already active")
        return
    users_reinstate(u["id"])
    print(f"  reinstated: {u['avatar']} {u['name']}")


def cli_tag(name_or_id: str, tag: str):
    u = _find_user(name_or_id)
    if not u:
        print(f"  user not found: {name_or_id}")
        _logui("users.tag", error="not_found", query=name_or_id)
        return
    users_tag(u["id"], tag)
    print(f"  tagged {u['avatar']} {u['name']} +{tag}")


def cli_untag(name_or_id: str, tag: str):
    u = _find_user(name_or_id)
    if not u:
        print(f"  user not found: {name_or_id}")
        _logui("users.untag", error="not_found", query=name_or_id)
        return
    users_untag(u["id"], tag)
    print(f"  untagged {u['avatar']} {u['name']} -{tag}")


def cli_pair(tags: list[str]):
    code = users_generate_code(tags)
    print(f"  pairing code : {code}")
    print(f"  tags         : {', '.join(tags)}")
    print(f"  (code is single-use)")


def cli_codes():
    codes = [c for c in _codes_load() if not c.get("used")]
    if not codes:
        print("  no active pairing codes")
        return
    for c in codes:
        tags = ", ".join(c.get("role_tags", []))
        print(f"  {c['code']}  [{tags}]")
    _logui("users.codes", count=len(codes))


CLI_SCHEMA = {"ops": {
    "list": {},
    "show": {"args": "name|id"},
    "revoke": {"args": "name|id"},
    "reinstate": {"args": "name|id"},
    "tag": {"args": "name|id tag"},
    "untag": {"args": "name|id tag"},
    "pair": {"params": ["--tags"]},
    "codes": {},
}}


def cli(argv):
    """Parse users command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="users", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--tags", default="user", dest="tags")
    args = p.parse_args(argv)

    a = args.action
    r = args.args
    try:
        if   a == "list":      cli_list()
        elif a == "codes":     cli_codes()
        elif a == "show":
            if not r: print("  usage: users show <name|id>")
            else: cli_show(r[0])
        elif a == "revoke":
            if not r: print("  usage: users revoke <name|id>")
            else: cli_revoke(r[0])
        elif a == "reinstate":
            if not r: print("  usage: users reinstate <name|id>")
            else: cli_reinstate(r[0])
        elif a == "tag":
            if len(r) < 2: print("  usage: users tag <name|id> <tag>")
            else: cli_tag(r[0], r[1])
        elif a == "untag":
            if len(r) < 2: print("  usage: users untag <name|id> <tag>")
            else: cli_untag(r[0], r[1])
        elif a == "pair":
            tags = [t.strip() for t in args.tags.split(",") if t.strip()]
            cli_pair(tags)
    except Exception as e:
        print(f"  ERROR: {e}")
