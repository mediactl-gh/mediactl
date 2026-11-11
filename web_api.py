from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

import camera as _camera
import device as _device
import logui as _logui
import media as _media
import mediactl as _mediactl
import users as _users

HERE = Path(__file__).parent
EVENTS_LOG = HERE / ".out" / "events.log"


# Commands and events

def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def api_run(request: Request, user: dict = Depends(_users.users_require)):
    from web import _ctl
    body    = await request.json()
    args    = body.get("args", [])
    if not args: raise HTTPException(400, "args required")
    cmd_str = " ".join(str(a) for a in args)
    _logui.append_line(EVENTS_LOG, f"[{_now()}] run.op cmd={cmd_str}")

    if args[:2] == ["device", "discover"] or args[0] == "run":
        try:
            await asyncio.to_thread(_mediactl.dispatch, _mediactl.make_parser().parse_args(args))
        except (ValueError, argparse.ArgumentError) as e:
            raise HTTPException(400, str(e)) from e
        except SystemExit as e:
            if e.code:
                raise HTTPException(400, "invalid command arguments") from e
        except RuntimeError as e:
            raise HTTPException(500, str(e)) from e
        return JSONResponse({"ok": True, "ts": _now()})

    if args[0] == "tv":
        import tv as _tv
        if await _tv.command_async(args):
            return JSONResponse({"ok": True, "ts": _now()})

    if args[:2] == ["ir", "send"] and len(args) in (5, 6) and args[-2] == "--remote":
        import ir as _ir
        await asyncio.to_thread(_ir.send, args[-1], *args[2:-2])
        return JSONResponse({"ok": True, "ts": _now()})

    if args[0] == "library":
        import library
        try:
            if await asyncio.to_thread(library.run_sync_command, args):
                return JSONResponse({"ok": True, "ts": _now()})
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    if args[0] == "camera" and _camera.request_push_snapshot(args):
        return JSONResponse({"ok": True, "ts": _now()})

    stdout, stderr, rc = await asyncio.to_thread(_ctl, *args)
    if stdout.strip():
        for line in stdout.strip().splitlines():
            _logui.append_line(EVENTS_LOG, line.rstrip())
    if stderr.strip() and rc != 0:
        _logui.append_line(EVENTS_LOG, f"[{_now()}] run.err ! text={stderr.strip()[:200]}")
    return JSONResponse({"ok": rc == 0, "ts": _now(), "stdout": stdout, "stderr": stderr})


async def api_schema(user: dict = Depends(_users.users_require)):
    from web import _ctl
    out, _, _ = await asyncio.to_thread(_ctl, "--schema")
    try:    return JSONResponse(json.loads(out))
    except: raise HTTPException(500, "schema parse failed")


async def telegram_callback(request: Request, background_tasks: BackgroundTasks):
    import notify
    update = await request.json()
    msg = update.get("message") or update.get("channel_post") or {}
    chat = msg.get("chat", {})
    text = msg.get("text", "")
    _logui.append_line(EVENTS_LOG, f"[{_now()}] notify.incoming update_id={update.get('update_id')} chat={chat.get('title') or chat.get('id')} text={text!r}")
    background_tasks.add_task(notify.dispatch, update)
    return JSONResponse(notify.webhook_reply(update))


async def api_emit(request: Request, user: dict = Depends(_users.users_require)):
    """Generic SSE event emitter. Body: {hint: "source.event", key: val, ...}."""
    body = await request.json()
    hint = body.pop("hint", None)
    if not hint: raise HTTPException(400, "hint required")
    kv = " ".join(f'{k}={v}' for k, v in body.items() if k != "hint")
    _logui.append_line(EVENTS_LOG, f"[{_now()}] {hint}  {kv}".rstrip())
    return JSONResponse({"ok": True})


async def api_events(user: dict = Depends(_users.users_require)):
    """
    SSE stream for tile updates.
    Tails events.log directly — same as /api/tail but parses logui lines into JSON.
    No threads, no queues, no broadcast. Just read the file.
    """
    SEP = "\n\n"

    async def gen():
        # send connected ping immediately
        yield f"data: {json.dumps({'hint':'web.connected','ts':_now()})}" + SEP

        loop    = asyncio.get_running_loop()
        pending = None   # logui event being assembled (for continuation lines)

        with open(EVENTS_LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)   # start from end — only new lines

            while True:
                line = await loop.run_in_executor(None, f.readline)

                if not line:
                    # flush pending event on idle
                    if pending:
                        yield f"data: {json.dumps(pending)}" + SEP
                        pending = None
                    await asyncio.sleep(0.05)
                    continue

                line = line.rstrip("\n")

                if line.startswith("["):
                    # new event — flush previous first
                    if pending:
                        yield f"data: {json.dumps(pending)}" + SEP
                    pending = _logui.parse_line(line)

                elif pending is not None and line.startswith("  "):
                    # continuation: "  snap: path/to/file.jpg"
                    stripped = line.strip()
                    if ": " in stripped:
                        k, _, v = stripped.partition(": ")
                        pending[k.strip()] = v.strip().replace("\\", "/")

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


async def api_tail(lines: int = 120, user: dict = Depends(_users.users_require)):
    """
    SSE stream of raw events.log lines — for the terminal tile.
    Replays last N lines on connect, then streams new ones live.
    """
    SEP = "\n\n"

    async def gen():
        loop = asyncio.get_running_loop()

        with open(EVENTS_LOG, "r", encoding="utf-8", errors="replace") as f:
            if lines > 0:
                all_lines = f.readlines()
                for l in all_lines[-lines:]:
                    yield f"data: {json.dumps({'type':'log','line':l.rstrip()})}" + SEP
            else:
                f.seek(0, 2)

            while True:
                line = await loop.run_in_executor(None, f.readline)
                if line:
                    yield f"data: {json.dumps({'type':'log','line':line.rstrip()})}" + SEP
                else:
                    await asyncio.sleep(0.05)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# Media and library

async def api_genres(user: dict = Depends(_users.users_require)):
    """TMDB genre names for library rule chips."""
    return JSONResponse(sorted(set(_media.TMDB_GENRES.values())))


async def api_media_browse(path: str, offset: int = 0, limit: int = 0, files: bool = False,
                           user: dict = Depends(_users.users_require)):
    import library
    try:
        return JSONResponse(library.browse(path, offset, limit, files))
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


async def api_media_info(provider: str, id: str, type: str = "", title: str = "", year: str = "",
                         user: dict = Depends(_users.users_require)):
    info = _media.cached_info_for_ref({
        "provider": provider, "id": id, "type": type, "title": title, "year": year,
    })
    if not info:
        raise HTTPException(404, "media info not cached")
    return JSONResponse(info)


async def api_media_torrents(query: str, user: dict = Depends(_users.users_require)):
    import indexer
    if not query.strip():
        raise HTTPException(400, "query required")
    errors = {}
    results = await asyncio.to_thread(indexer.search, query, errors=errors)
    return JSONResponse({"results": indexer.result_rows(results), "errors": errors})


async def api_media_recommendations(user: dict = Depends(_users.users_require)):
    return JSONResponse({"items": _media.recommendations()})


async def api_library_folder(path: str = "", user: dict = Depends(_users.users_require)):
    import library
    try:
        return {"path": await asyncio.to_thread(library.select_folder, path)}
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e


async def api_library(enrich: bool = False, user: dict = Depends(_users.users_require)):
    import library
    libraries = await asyncio.to_thread(library.list_libraries, enrich)
    if enrich and any(row["items"] and not row["preview"] for row in libraries):
        library._library_sync_meta()
    return JSONResponse(libraries)


# Dashboards

async def api_dashboard_get(user: dict = Depends(_users.users_require)):
    from web import dashboard_load
    return JSONResponse(dashboard_load(user["name"]) or {"tiles": []})


async def api_dashboard_post(request: Request, user: dict = Depends(_users.users_require)):
    from web import dashboard_save_layout
    data = await request.json()
    template = request.query_params.get("template", "").strip()
    try:
        dashboard_save_layout(user["name"], data, template)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return JSONResponse({"ok": True})


async def api_dashboard_templates_list():
    from web import dashboard_list_templates
    return JSONResponse({"templates": dashboard_list_templates()})


async def api_dashboard_template_apply(request: Request, user: dict = Depends(_users.users_require)):
    from web import dashboard_apply_template
    body = await request.json()
    name = (body.get("name") or "default").strip()
    try:
        data = dashboard_apply_template(
            user, name, body.get("from_layout"), body.get("size"), body.get("cols"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
    return JSONResponse({"ok": True, "name": name, "tiles": len(data.get("tiles", []))})


# Users and devices

async def api_me(request: Request, generate: bool = False, tags: str = "user"):
    try:
        user, token = _users.current_user(
            request.cookies.get(_users._COOKIE),
            request.client.host if request.client else "", generate, tags,
        )
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    response = JSONResponse(user)
    if token:
        _users.users_set_cookie(response, token)
    return response


async def api_logout():
    """Clear the session cookie for the current device."""
    response = JSONResponse({"ok": True})
    _users.users_clear_cookie(response)
    return response


async def api_whoami(request: Request):
    """Public — returns the client's IP as seen by the server."""
    ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    return JSONResponse({"ip": ip})


async def api_users_redeem(request: Request):
    return await _users.api_users_redeem(request)


async def api_users_devices(request: Request, user: dict = Depends(_users.users_require)):
    from web import dashboard_sync_user_devices
    added_names = _device.contribute_client_devices(await request.json(), request.client.host)
    if added_names:
        dashboard_sync_user_devices(user)
    return JSONResponse({"ok": True, "added": added_names})


async def api_devices_status(request: Request, user: dict = Depends(_users.users_require)):
    """Persist a device's live capability readings into capabilities.json,
    keyed by the server-authoritative push:// identifier."""
    body    = await request.json()
    real_ip = request.client.host if request.client else ""
    ident   = f"push://{real_ip}"
    _device.write_capabilities(ident, body.get("capabilities") or {})
    return JSONResponse({"ok": True, "identifier": ident})


async def api_devices_manual(request: Request, user: dict = Depends(_users.users_require)):
    from web import dashboard_sync_user_devices
    body = await request.json()
    real_ip = request.client.host if request.client else ""
    try:
        result = _device.edit_device(body, real_ip)
        dashboard_sync_user_devices(user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return JSONResponse({"ok": True, **result})


# Configuration

CONFIG_EDITABLE = {"media", "media_lists", "library", "llm", "services", "router", "router/router_flows_short.txt", "security", "presence"}


async def api_config_post(request: Request, user: dict = Depends(_users.users_require)):
    """Write a config value under .out/ (JSON dotted-path or raw text file)."""
    body   = await request.json()
    target = str(body.get("target", ""))
    path   = str(body.get("path", ""))
    value  = body.get("value")
    fp     = HERE / ".out" / target
    if not str(fp.resolve()).startswith(str(HERE / ".out")):
        raise HTTPException(400, "bad target")

    # ── raw-file write (.txt etc.) ──────────────────────────────────────
    if fp.suffix:
        if target not in CONFIG_EDITABLE:
            raise HTTPException(403, "target not editable")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(str(value or ""), encoding="utf-8")
        return JSONResponse({"ok": True})

    # ── JSON config (no extension) ──────────────────────────────────────
    if not target.isidentifier() or target not in CONFIG_EDITABLE:
        raise HTTPException(403, "target not editable")
    if not path:
        raise HTTPException(400, "path required")
    f = HERE / ".out" / f"{target}.json"
    if target == "presence":
        import network
        if not path.startswith("devices.") or not isinstance(value, dict):
            raise HTTPException(400, "invalid device edit")
        try:
            await asyncio.to_thread(
                network.edit_device, value.get("mac"), value.get("name"), value.get("kind"),
                value.get("ports"), value.get("services"), key=path.removeprefix("devices."))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return JSONResponse({"ok": True})
    if target == "security":
        import security
        try:
            security.update_setting(path, value)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return JSONResponse({"ok": True})
    if target == "media":
        try:
            await asyncio.to_thread(_media.update_setting, path, value)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return JSONResponse({"ok": True})
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        if target == "llm":
            import llm
            data = llm.create_config()
        else:
            raise HTTPException(404, "config not found")
    opts = (data.get("_options") or {}).get(path)
    if opts:
        vals = value if isinstance(value, list) else [value]
        if any(v not in opts for v in vals):
            raise HTTPException(400, f"value must be one of {opts}")
    keys = path.split(".")
    node = data
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    if not isinstance(node, dict):
        raise HTTPException(400, "bad path")
    node[keys[-1]] = value
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return JSONResponse({"ok": True})


async def api_config(target: str, path: Optional[str] = None):
    """Public — returns one or more .out resources.

    A comma-separated target list returns a mapping; single-target behavior is
    unchanged. ``path`` is applied to each JSON target in a list.
    """
    import json as _json
    if target == "security":
        import security as security_config
        data = security_config.settings()
        data["_options"] = security_config.CONFIG_OPTIONS
        return JSONResponse(data)
    targets = [item.strip() for item in target.split(",")]
    if not targets or any(not item for item in targets):
        raise HTTPException(400, "bad target")
    if len(targets) > 1:
        combined = {}
        for item in targets:
            item_fp = HERE / ".out" / item
            if not str(item_fp.resolve()).startswith(str(HERE / ".out")):
                raise HTTPException(400, "bad target")
            if item_fp.suffix:
                try:
                    combined[item] = item_fp.read_text(encoding="utf-8")
                except Exception:
                    combined[item] = ""
                continue
            if any(not seg.isidentifier() for seg in item.split("/")):
                raise HTTPException(400, "bad target")
            try:
                item_data = _json.loads((HERE / ".out" / f"{item}.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                item_data = _json.loads(_json.dumps(_media.MEDIA_DEFAULTS)) if item == "media" else {}
            except Exception:
                item_data = {}
            if path:
                for key in path.split("."):
                    if not isinstance(item_data, dict) or key not in item_data:
                        item_data = {}
                        break
                    item_data = item_data[key]
            combined[item] = item_data
        return JSONResponse(combined)
    fp = HERE / ".out" / target
    if not str(fp.resolve()).startswith(str(HERE / ".out")):
        raise HTTPException(400, "bad target")
    # ── file with extension: return raw text ────────────────────────────
    if fp.suffix:
        try:
            return Response(fp.read_text(encoding="utf-8"), media_type="text/plain")
        except Exception:
            return Response("", media_type="text/plain")
    # ── no extension: JSON config ───────────────────────────────────────
    if not target or any(not seg.isidentifier() for seg in target.split("/")):
        raise HTTPException(400, "bad target")
    try:
        data = _json.loads((HERE / ".out" / f"{target}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = _json.loads(_json.dumps(_media.MEDIA_DEFAULTS)) if target == "media" else {}
    except Exception:
        return JSONResponse({})
    if target == "media":
        data = _media.config_options(data)
    if path:
        for key in path.split("."):
            if not isinstance(data, dict) or key not in data:
                return JSONResponse({})
            data = data[key]
    return JSONResponse(data)


async def api_host():
    """Public — returns LAN address for QR codes."""
    import socket
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), interpolate=False)
    port = int(os.environ.get("WEB_PORT") or 7000)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"
    return JSONResponse({"url": f"https://{lan_ip}:{port}"})


# Cameras and audio

async def api_cameras(user: dict = Depends(_users.users_require)):
    import audio
    return JSONResponse({"cameras": _camera.sources(), "audio_devices": audio.sources(),
                         **_camera.push_settings()})


async def api_snapshot(camera: Optional[str] = None, user: dict = Depends(_users.users_require)):
    try:
        snapshot = _camera.latest_snapshot(camera)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return JSONResponse({"path": f"/snapshots/{snapshot.name}", "ts": _now()})


async def api_snapshots(limit: int = 40, camera: Optional[str] = None,
                        user: dict = Depends(_users.users_require)):
    return JSONResponse({"snapshots": [
        {"path": f"/snapshots/{item['name']}", **item}
        for item in _camera.snapshots(camera, limit)
    ]})


async def _handle_push(name: str, data: bytes, mode: str = "snap", score: float = 0) -> JSONResponse:
    if mode == "motion":
        _logui.append_line(EVENTS_LOG, f"[{_now()}] camera.motion cam={name} score={score}")
        if data:
            await asyncio.to_thread(_camera.store_frame, name, data, True)
        return JSONResponse({"ok": True})
    if not data:
        raise HTTPException(400, "empty body")
    stream = mode == "stream"
    frame = await asyncio.to_thread(_camera.store_frame, name, data, stream)
    snap_path = f"/stream/{name}/{frame.name}" if stream else f"/snapshots/{frame.name}"
    _logui.append_line(EVENTS_LOG, f"[{_now()}] camera.snap cam={name} snap={snap_path}")
    return JSONResponse({"ok": True, "snap": snap_path})


async def api_camera_push(name: str, request: Request, mode: str = "snap", score: float = 0,
                          user: dict = Depends(_users.users_require)):
    return await _handle_push(name, await request.body(), mode, score)


async def push_app_camera_push(name: str, request: Request, mode: str = "snap", score: float = 0):
    return await _handle_push(name, await request.body(), mode, score)


async def api_camera_request(name: str, user: dict = Depends(_users.users_require)):
    return JSONResponse({"ok": True, "viewers": _camera.request_view(name, user)})


async def api_camera_release(name: str, user: dict = Depends(_users.users_require)):
    return JSONResponse({"ok": True, "viewers": _camera.release_view(name, user)})


async def api_camera_latest(name: str, i: int = 0,
                            user: dict = Depends(_users.users_require)):
    try:
        path = await asyncio.to_thread(_camera.stream_frame, name, i)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(str(path), media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache, no-store"})


async def api_camera_forcerelease(name: str, user: dict = Depends(_users.users_require)):
    _camera.force_release(name, user)
    return JSONResponse({"ok": True})


async def api_camera_motion_start(name: str, user: dict = Depends(_users.users_require)):
    _camera.request_motion(name, user)
    return JSONResponse({"ok": True})


async def api_camera_motion_off(name: str, user: dict = Depends(_users.users_require)):
    _camera.stop_motion(name, user)
    return JSONResponse({"ok": True})


async def api_audio_push(device: str, websocket: WebSocket):
    import audio
    try:
        await _users.users_require(websocket)
        session = audio.push_open(device, websocket.client.host)
    except (HTTPException, PermissionError, ValueError):
        await websocket.close(code=1008)
        return
    loop = asyncio.get_running_loop()
    worker = None
    try:
        await websocket.accept()
        options = await websocket.receive_json()

        def ready():
            asyncio.run_coroutine_threadsafe(websocket.send_text("ready"), loop)

        async def transcribe():
            try:
                await asyncio.to_thread(audio.push_listen, session, options, ready)
            finally:
                if websocket.client_state.name == "CONNECTED" and websocket.application_state.name == "CONNECTED":
                    await websocket.close()

        worker = asyncio.create_task(transcribe())
        while True:
            audio.push_feed(session, await websocket.receive_bytes())
    except WebSocketDisconnect:
        pass
    except ValueError as error:
        _logui.append_line(EVENTS_LOG, f"[{_now()}] audio.listen-error device={device} msg={error}")
        await websocket.close(code=1008)
    finally:
        session["stop"].set()
        try:
            if worker:
                await asyncio.wait_for(worker, timeout=3)
        except Exception as error:
            _logui.append_line(EVENTS_LOG, f"[{_now()}] audio.listen-error device={device} msg={error}")
        finally:
            audio.push_close(session)


# Security

async def api_security_thumbs(session_id: str, n: int = 16, user: dict = Depends(_users.users_require)):
    import security
    return JSONResponse(security.session_thumbnails(session_id, n))


async def api_faces_promote(request: Request, user: dict = Depends(_users.users_require)):
    """Enroll an unknown guest as a named known face."""
    import security_protocol
    body = await request.json()
    guest = (body.get("guest") or "").strip()
    name = (body.get("name") or "").strip()
    try:
        dest = security_protocol.promote_guest(guest, name)
        return JSONResponse({"ok": True, "name": name, "path": str(dest)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
