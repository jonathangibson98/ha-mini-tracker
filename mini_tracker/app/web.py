"""aiohttp apps: the admin app (behind HA ingress) and the optional read-only public app."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import time
import uuid
from xml.sax.saxutils import escape

from aiohttp import web

from ctx import VERSION, Ctx
from importers import extract_fixes

log = logging.getLogger("mini_tracker.web")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
ADMIN_ALLOW = {"172.30.32.2", "127.0.0.1", "::1"} | set(filter(None, os.environ.get("MT_ADMIN_ALLOW", "").split(",")))
SVG_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'"


# ------------------------------------------------------------------ helpers


def jerr(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def qint(request: web.Request, name: str, default: int) -> int:
    try:
        return int(float(request.query.get(name, default)))
    except (TypeError, ValueError):
        return default


def dev_slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "device"


def ext_id(name: str) -> str:
    return "ext." + dev_slug(name)


def places_public(ctx: Ctx) -> list[dict]:
    return [{"id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"], "radius": p["radius"],
             "icon": p.get("icon", ""), "color": p.get("color", "#03a9f4"), "source": p.get("source", "custom")}
            for p in ctx.all_places()]


def enabled_devs(ctx: Ctx) -> set[str]:
    return {d for d, v in ctx.settings.data["devices"].items() if v.get("enabled")}


# ------------------------------------------------------------------ middlewares


@web.middleware
async def security_headers(request: web.Request, handler):
    resp = await handler(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif request.path.startswith("/static/"):
        resp.headers.setdefault("Cache-Control", "public, max-age=3600")
    return resp


@web.middleware
async def admin_guard(request: web.Request, handler):
    """The admin app is only reachable through HA ingress (the Supervisor proxy)."""
    if request.path == "/health":
        return await handler(request)
    if request.remote not in ADMIN_ALLOW and not os.environ.get("MT_DEV"):
        return web.Response(status=403, text="Forbidden: use Home Assistant ingress")
    return await handler(request)


@web.middleware
async def read_only_guard(request: web.Request, handler):
    if request.method not in ("GET", "HEAD") and request.path != "/api/ingest":
        return web.Response(status=405, text="Read-only")
    return await handler(request)


# ------------------------------------------------------------------ shared handlers


async def index(request: web.Request) -> web.Response:
    mode = request.app["mode"]
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fp:
        html = fp.read().replace("{{MODE}}", mode).replace("{{V}}", VERSION)
    return web.Response(text=html, content_type="text/html",
                        headers={"Cache-Control": "no-cache"})


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def api_state(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    return web.json_response({
        "now": int(time.time()), "night": ctx.is_night(), "version": VERSION, "ha": ctx.ha.connected,
        "mode": request.app["mode"], "settings": ctx.settings.public_view(),
        "devices": ctx.devices_view(), "places": places_public(ctx),
    })


async def api_history(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    want = [d for d in request.query.get("dev", "").split(",") if d]
    devs = [d for d in want if d in enabled_devs(ctx)]
    if not devs:
        return web.json_response({"devices": {}})
    now = int(time.time())
    end = qint(request, "end", now)
    start = qint(request, "start", end - 86400)
    span = max(1, end - start)
    tol = float(request.query.get("tol", 3 if span <= 86400 * 1.5 else 8 if span <= 86400 * 8 else 20))
    mx = max(100, min(qint(request, "max", 6000), 20000))
    return web.json_response({"devices": await ctx.history(devs, start, end, tol, mx)})


async def api_timeline(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    dev = request.query.get("dev", "")
    if dev not in enabled_devs(ctx):
        return jerr("unknown device", 404)
    now = int(time.time())
    end = qint(request, "end", now)
    start = qint(request, "start", end - 86400)
    return web.json_response(await ctx.timeline(dev, start, end))


async def api_place_stats(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    dev = request.query.get("dev", "")
    if dev not in enabled_devs(ctx):
        return jerr("unknown device", 404)
    return web.json_response(await ctx.place_stats(dev, qint(request, "days", 30)))


async def api_places(request: web.Request) -> web.Response:
    return web.json_response(places_public(request.app["ctx"]))


async def api_icons(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    icons = await asyncio.to_thread(ctx.list_icons)
    return web.json_response({"folder": ctx.settings.data["icons_folder"], "icons": icons,
                              "exists": os.path.isdir(ctx.icons_dir())})


async def api_icon(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    path = ctx.icon_path(request.match_info["name"])
    if not path:
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Content-Type": "image/svg+xml", "Content-Security-Policy": SVG_CSP,
                                           "Cache-Control": "no-cache"})


async def api_picture(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    path = ctx.picture_path(request.match_info["file"])
    if not path:
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400",
                                           "Content-Security-Policy": SVG_CSP})


# ------------------------------------------------------------------ admin handlers


async def api_get_settings(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    d = ctx.settings.data
    return web.json_response({**d, "mqtt_status": {"connected": ctx.mqtt.connected, "error": ctx.mqtt.error}})


async def api_post_settings(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    try:
        patch = await request.json()
    except ValueError:
        return jerr("bad json")
    before_mqtt = json.dumps(ctx.settings.data["mqtt"], sort_keys=True)
    ctx.settings.patch(patch)
    if json.dumps(ctx.settings.data["mqtt"], sort_keys=True) != before_mqtt:
        await ctx.configure_mqtt()
    if "public" in patch:
        await request.app["public_server"].apply()
    if "devices" in patch:
        await ctx.ha.refresh_tracked()
    return await api_get_settings(request)


async def api_candidates(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    devs = ctx.settings.data["devices"]
    out = []
    for c in ctx.ha.candidates():
        cfg = devs.get(c["id"])
        out.append({**c, "tracked": bool(cfg and cfg.get("enabled"))})
    return web.json_response({"ha": ctx.ha.connected, "candidates": out})


async def api_place_upsert(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    try:
        place = ctx.settings.upsert_place(await request.json())
    except (ValueError, KeyError, TypeError) as exc:
        return jerr(str(exc))
    return web.json_response(place)


async def api_place_delete(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    return web.json_response({"deleted": ctx.settings.delete_place(request.match_info["id"])})


async def api_device_create(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    try:
        name = str((await request.json()).get("name", "")).strip()[:40]
    except ValueError:
        return jerr("bad json")
    if not name:
        return jerr("name required")
    dev = ext_id(name)
    ctx.settings.ensure_device(dev, name, True)
    return web.json_response({"id": dev})


async def api_device_delete(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    dev = request.match_info["id"]
    removed = 0
    if request.query.get("purge") == "1":
        removed = await asyncio.to_thread(ctx.store.delete_device, dev)
    ctx.settings.delete_device(dev)
    ctx.recorder.purge_cache(dev)
    ctx.mqtt.remove_device(dev)
    return web.json_response({"removed_points": removed})


async def api_picture_upload(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file":
        return jerr("file required")
    data = bytearray()
    while True:
        chunk = await part.read_chunk(65536)
        if not chunk:
            break
        data += chunk
        if len(data) > 1_500_000:
            return jerr("picture too large (max 1.5 MB)", 413)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = "webp"
    else:
        return jerr("only PNG, JPG or WEBP pictures")
    name = f"{uuid.uuid4().hex[:12]}.{ext}"
    with open(os.path.join(ctx.pictures_dir, name), "wb") as fp:
        fp.write(data)
    return web.json_response({"file": name})


async def api_import(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    reader = await request.multipart()
    fields: dict[str, str] = {}
    tmp_path = None
    filename = ""
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "file":
            filename = part.filename or "upload"
            tmp_path = os.path.join(ctx.tmp_dir, f"import_{uuid.uuid4().hex[:8]}")
            with open(tmp_path, "wb") as fp:
                while True:
                    chunk = await part.read_chunk(1 << 20)
                    if not chunk:
                        break
                    fp.write(chunk)
        else:
            fields[part.name] = (await part.text()).strip()
    if not tmp_path:
        return jerr("file required")
    dev = fields.get("device", "")
    if not dev:
        name = fields.get("new_name", "").strip()
        if not name:
            os.remove(tmp_path)
            return jerr("choose a device or give a name for a new one")
        dev = ext_id(name)
        ctx.settings.ensure_device(dev, name, True)
    elif dev not in ctx.settings.data["devices"]:
        os.remove(tmp_path)
        return jerr("unknown device")
    job = ctx.new_job("import")
    thin = fields.get("thin", "1") != "0"
    asyncio.create_task(asyncio.to_thread(ctx.import_file, job, tmp_path, filename, dev, thin))
    return web.json_response({"job": job["id"], "device": dev})


async def api_backfill(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    try:
        body = await request.json()
    except ValueError:
        return jerr("bad json")
    ents = [e for e in body.get("entities", []) if e in ctx.tracked_ha()]
    if not ents:
        return jerr("pick at least one tracked Home Assistant device")
    days = max(1, min(int(body.get("days", 10)), 90))
    job = ctx.new_job("backfill")
    asyncio.create_task(ctx.backfill(job, ents, days))
    return web.json_response({"job": job["id"]})


async def api_job(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    job = ctx.jobs.get(request.match_info["id"])
    if not job:
        return jerr("unknown job", 404)
    return web.json_response({k: v for k, v in job.items() if k != "created"})


async def api_purge(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    try:
        body = await request.json()
    except ValueError:
        return jerr("bad json")
    dev = body.get("dev") or None
    if body.get("all") and dev:
        n = await asyncio.to_thread(ctx.store.delete_device, dev)
        ctx.recorder.purge_cache(dev)
    else:
        days = max(1, int(body.get("older_than_days", 0)))
        n = await asyncio.to_thread(ctx.store.purge_before, int(time.time()) - days * 86400, dev)
    await asyncio.to_thread(ctx.store.vacuum)
    return web.json_response({"removed": n})


async def api_stats(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    devs = await asyncio.to_thread(ctx.store.devices)
    total = sum(d["count"] for d in devs)
    return web.json_response({
        "points": total, "devices": devs, "db_bytes": ctx.store.size_bytes(), "ha": ctx.ha.connected,
        "ha_error": ctx.ha.last_error, "time_zone": ctx.ha.time_zone, "uptime_s": int(time.time() - ctx.started),
        "version": VERSION, "mqtt": {"enabled": ctx.settings.data["mqtt"]["enabled"],
                                     "connected": ctx.mqtt.connected, "error": ctx.mqtt.error},
        "public_port": request.app["public_server"].port,
        "public_running": request.app["public_server"].running,
    })


async def api_regen_token(request: web.Request) -> web.Response:
    return web.json_response({"token": request.app["ctx"].settings.regenerate_token()})


async def api_export(request: web.Request) -> web.StreamResponse:
    ctx: Ctx = request.app["ctx"]
    dev = request.query.get("dev", "")
    if dev not in ctx.settings.data["devices"]:
        return jerr("unknown device", 404)
    fmt = request.query.get("fmt", "gpx")
    end = qint(request, "end", int(time.time()))
    start = qint(request, "start", 0)
    pts = await asyncio.to_thread(ctx.store.range, dev, start, end)
    name = dev_slug(ctx.settings.data["devices"][dev]["name"])
    from datetime import datetime, timezone

    def iso(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if fmt == "json":
        body = json.dumps([{"ts": p[0], "lat": p[1], "lon": p[2], "acc": p[3]} for p in pts])
        ctype, ext = "application/json", "json"
    elif fmt == "csv":
        lines = ["timestamp,iso_time,lat,lon,accuracy"] + [f"{p[0]},{iso(p[0])},{p[1]},{p[2]},{p[3] or ''}" for p in pts]
        body, ctype, ext = "\n".join(lines), "text/csv", "csv"
    else:
        trk = "".join(f'<trkpt lat="{p[1]}" lon="{p[2]}"><time>{iso(p[0])}</time></trkpt>\n' for p in pts)
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Mini Tracker" '
                'xmlns="http://www.topografix.com/GPX/1/1"><trk><name>' + escape(name) + "</name><trkseg>\n"
                + trk + "</trkseg></trk></gpx>")
        ctype, ext = "application/gpx+xml", "gpx"
    return web.Response(text=body, content_type=ctype,
                        headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'})


# ------------------------------------------------------------------ ingest (public app)


def _check_token(request: web.Request, expected: str) -> bool:
    given = request.headers.get("X-Token") or request.query.get("token") or ""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        given = auth[7:].strip()
    if not given and auth.lower().startswith("basic "):
        import base64
        try:
            given = base64.b64decode(auth[6:]).decode().split(":", 1)[-1]
        except Exception:  # noqa: BLE001
            given = ""
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


async def api_ingest(request: web.Request) -> web.Response:
    ctx: Ctx = request.app["ctx"]
    cfg = ctx.settings.data["ingest"]
    if not cfg["enabled"]:
        return jerr("ingest is disabled", 403)
    if not _check_token(request, cfg["token"]):
        return jerr("bad token", 401)
    try:
        payload = await request.json()
    except ValueError:
        return jerr("bad json")

    default_dev = request.query.get("device") or request.query.get("device_id") or ""
    items: list[tuple[str, str, dict]] = []   # (device name, kind, object)

    def add(obj, name_hint=""):
        if isinstance(obj, dict):
            items.append((name_hint, "obj", obj))

    response: object = {"ok": True}
    if isinstance(payload, dict) and payload.get("_type") == "location":          # OwnTracks
        parts = str(payload.get("topic", "")).split("/")
        name = "_".join(parts[1:3]) if len(parts) >= 3 else payload.get("tid") or default_dev or "owntracks"
        add({"lat": payload.get("lat"), "lon": payload.get("lon"), "tst": payload.get("tst"),
             "acc": payload.get("acc"), "batt": payload.get("batt")}, str(name))
        response = []
    elif isinstance(payload, dict) and isinstance(payload.get("locations"), list):   # Overland
        for feat in payload["locations"]:
            props = (feat or {}).get("properties") or {}
            add(feat, str(props.get("device_id") or default_dev or "overland"))
        response = {"result": "ok"}
    elif isinstance(payload, list):
        for o in payload:
            add(o, str((o or {}).get("device", default_dev)) if isinstance(o, dict) else default_dev)
    else:
        add(payload, str((payload or {}).get("device", default_dev)) if isinstance(payload, dict) else default_dev)

    stored = 0
    for name, _, obj in items:
        if not any(k in obj for k in ("ts", "timestamp", "time", "tst", "timestampMs", "properties")):
            obj = {**obj, "ts": time.time()}
        batt = obj.get("batt", obj.get("battery", obj.get("battery_level")))
        if batt is None and isinstance(obj.get("properties"), dict):
            batt = obj["properties"].get("battery_level")
            if isinstance(batt, float) and batt <= 1:
                batt = round(batt * 100)
        for fix in extract_fixes(obj):
            name = name or "device"
            dev = ext_id(name)
            cur = ctx.settings.data["devices"].get(dev)
            if cur is None:
                ctx.settings.ensure_device(dev, name, True)
            elif not cur.get("enabled"):
                continue
            if await ctx.recorder.handle_fix(dev, fix[0], fix[1], fix[2], fix[3], batt, "ingest"):
                stored += 1
    if isinstance(response, dict):
        response.setdefault("stored", stored)
    return web.json_response(response)


# ------------------------------------------------------------------ app factories


def _static(app: web.Application) -> None:
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_static("/static/", STATIC_DIR, follow_symlinks=False, show_index=False)


def _shared_routes(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/state", api_state)
    r.add_get("/api/history", api_history)
    r.add_get("/api/timeline", api_timeline)
    r.add_get("/api/places", api_places)
    r.add_get("/api/places/stats", api_place_stats)
    r.add_get("/api/icons", api_icons)
    r.add_get("/api/icon/{name:.+}", api_icon)
    r.add_get("/api/picture/{file}", api_picture)


def make_admin_app(ctx: Ctx, public_server: "PublicServer") -> web.Application:
    app = web.Application(middlewares=[admin_guard, security_headers], client_max_size=8 * 1024 * 1024)
    app["ctx"] = ctx
    app["mode"] = "admin"
    app["public_server"] = public_server
    _static(app)
    _shared_routes(app)
    r = app.router
    r.add_get("/api/settings", api_get_settings)
    r.add_post("/api/settings", api_post_settings)
    r.add_get("/api/candidates", api_candidates)
    r.add_post("/api/places", api_place_upsert)
    r.add_delete("/api/places/{id}", api_place_delete)
    r.add_post("/api/devices", api_device_create)
    r.add_delete("/api/devices/{id}", api_device_delete)
    r.add_post("/api/picture", api_picture_upload)
    r.add_post("/api/import", api_import)
    r.add_post("/api/backfill", api_backfill)
    r.add_get("/api/jobs/{id}", api_job)
    r.add_post("/api/purge", api_purge)
    r.add_get("/api/stats", api_stats)
    r.add_post("/api/ingest/token", api_regen_token)
    r.add_get("/api/export", api_export)
    return app


def make_public_app(ctx: Ctx) -> web.Application:
    app = web.Application(middlewares=[read_only_guard, security_headers], client_max_size=4 * 1024 * 1024)
    app["ctx"] = ctx
    app["mode"] = "view"
    _static(app)
    _shared_routes(app)
    app.router.add_post("/api/ingest", api_ingest)
    return app


class PublicServer:
    """Starts/stops the read-only listener when the setting is toggled."""

    def __init__(self, ctx: Ctx, port: int) -> None:
        self.ctx = ctx
        self.port = port
        self.runner: web.AppRunner | None = None
        self.running = False

    async def apply(self) -> None:
        want = bool(self.ctx.settings.data["public"]["enabled"])
        if want and not self.running:
            self.runner = web.AppRunner(make_public_app(self.ctx), access_log=None)
            await self.runner.setup()
            try:
                await web.TCPSite(self.runner, "0.0.0.0", self.port).start()
                self.running = True
                log.info("Read-only map listening on :%d", self.port)
            except OSError as exc:
                log.error("Could not start read-only map on :%d: %s", self.port, exc)
                await self.runner.cleanup()
                self.runner = None
        elif not want and self.running:
            await self.stop()

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
        self.runner = None
        self.running = False
