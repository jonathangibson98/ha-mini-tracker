"""Application context: owns settings, store, recorder, HA client, jobs."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

import geo
from ha import HAClient, day_chunks
from importers import iter_fixes
from mqtt_pub import MqttPublisher
from recorder import Recorder
from settings import Settings
from store import Store

log = logging.getLogger("mini_tracker.ctx")

VERSION = "1.0.0"
MAX_RANGE_DAYS = 400


class Ctx:
    def __init__(self, data_dir: str, ha_config_dir: str, ha_base: str, token: str,
                 supervisor_url: str = "http://supervisor") -> None:
        self.data_dir = data_dir
        self.ha_config_dir = ha_config_dir
        self.supervisor_url = supervisor_url.rstrip("/")
        self.token = token
        os.makedirs(data_dir, exist_ok=True)
        self.pictures_dir = os.path.join(data_dir, "pictures")
        self.tmp_dir = os.path.join(data_dir, "tmp")
        os.makedirs(self.pictures_dir, exist_ok=True)
        os.makedirs(self.tmp_dir, exist_ok=True)

        self.settings = Settings(os.path.join(data_dir, "settings.json"))
        self.store = Store(os.path.join(data_dir, "tracker.db"))
        self.recorder = Recorder(self.store, self.settings)
        self.ha = HAClient(ha_base, token, self.on_ha_fix)
        self.ha.tracked = self.tracked_ha
        self.mqtt = MqttPublisher()
        self.jobs: dict[str, dict] = {}
        self._mqtt_last: dict[str, float] = {}
        self.recorder.listeners.append(self.on_live_change)
        self.started = time.time()

    # ---------------------------------------------------------------- devices
    def tracked_ha(self) -> set[str]:
        return {d for d, v in self.settings.data["devices"].items()
                if v.get("enabled") and not d.startswith("ext.")}

    async def on_ha_fix(self, dev, ts, lat, lon, acc, batt, src) -> None:
        await self.recorder.handle_fix(dev, ts, lat, lon, acc, batt, src)

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.ha.time_zone)
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def is_night(self) -> bool:
        n = self.ha.night
        if n is not None:
            return n
        h = datetime.now(self.tz()).hour
        return h >= 19 or h < 7

    def all_places(self) -> list[dict]:
        out = [dict(p, source="custom") for p in self.settings.data["places"]]
        if self.settings.data["use_ha_zones"]:
            out += [z for z in self.ha.zones() if not z.get("passive")]
        return out

    def devices_view(self) -> list[dict]:
        places = self.all_places()
        out = []
        for dev, cfg in self.settings.data["devices"].items():
            if not cfg.get("enabled"):
                continue
            fix = self.recorder.live.get(dev)
            item = {"id": dev, "name": cfg["name"], "color": cfg["color"], "icon": cfg.get("icon", ""),
                    "external": dev.startswith("ext.")}
            if fix:
                pl = geo.match_place(fix["lat"], fix["lon"], places, slack_m=25)
                item.update(lat=fix["lat"], lon=fix["lon"], acc=fix["acc"], batt=fix["batt"], ts=fix["ts"],
                            place=pl["name"] if pl else None)
            out.append(item)
        return out

    # ---------------------------------------------------------------- icons
    def icons_dir(self) -> str:
        return os.path.join(self.ha_config_dir, self.settings.data["icons_folder"])

    def list_icons(self) -> list[str]:
        root = self.icons_dir()
        names = []
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in fn:
                if f.endswith(".svg") and not f.startswith("."):
                    rel = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
                    names.append(rel[:-4])
        return sorted(names)

    def icon_path(self, name: str) -> str | None:
        root = os.path.realpath(self.icons_dir())
        full = os.path.realpath(os.path.join(root, name + ".svg"))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return None
        return full

    def picture_path(self, fname: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}\.(png|jpg|jpeg|webp)", fname):
            return None
        full = os.path.join(self.pictures_dir, fname)
        return full if os.path.isfile(full) else None

    # ---------------------------------------------------------------- queries
    def _range(self, start: int, end: int) -> tuple[int, int]:
        end = min(int(end), int(time.time()) + 600)
        start = max(int(start), end - MAX_RANGE_DAYS * 86400)
        return start, end

    async def history(self, devs: list[str], start: int, end: int, tol: float = 5.0,
                      max_points: int = 6000) -> dict:
        start, end = self._range(start, end)
        out = {}
        for dev in devs:
            pts = await asyncio.to_thread(self.store.range, dev, start, end)
            dist = geo.path_length_m(pts)
            simp = geo.simplify(pts, tol, max_points)
            out[dev] = {"points": [[p[0], round(p[1], 6), round(p[2], 6), p[3]] for p in simp],
                        "distance_m": dist, "count": len(pts)}
        return out

    async def timeline(self, dev: str, start: int, end: int) -> dict:
        start, end = self._range(start, end)
        pts = await asyncio.to_thread(self.store.range, dev, start, end)
        v = self.settings.data["visits"]
        tl = geo.build_timeline(pts, self.all_places(), int(v["min_stay_min"] * 60), v["radius_m"])
        for s in tl["stays"]:  # indexes are server-internal
            s.pop("i0", None)
            s.pop("i1", None)
        return tl

    async def place_stats(self, dev: str, days: int) -> list[dict]:
        end = int(time.time())
        start = end - max(1, min(days, 180)) * 86400
        pts = await asyncio.to_thread(self.store.range, dev, start, end)
        v = self.settings.data["visits"]
        return geo.place_stats(pts, self.all_places(), int(v["min_stay_min"] * 60), v["radius_m"])

    async def distance_today(self, dev: str) -> float:
        now = datetime.now(self.tz())
        start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        pts = await asyncio.to_thread(self.store.range, dev, start, int(time.time()) + 600)
        v = self.settings.data["visits"]
        tl = geo.build_timeline(pts, [], int(v["min_stay_min"] * 60), v["radius_m"])
        return tl["summary"]["distance_m"] / 1609.344

    # ---------------------------------------------------------------- MQTT
    async def configure_mqtt(self) -> None:
        m = self.settings.data["mqtt"]
        if not m["enabled"]:
            self.mqtt.stop()
            return
        host, port, user, pw = m["host"], m["port"], m["username"], m["password"]
        if not host:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"{self.supervisor_url}/services/mqtt",
                                     headers={"Authorization": f"Bearer {self.token}"},
                                     timeout=aiohttp.ClientTimeout(total=10)) as r:
                        r.raise_for_status()
                        d = (await r.json()).get("data", {})
                host, port = d.get("host"), d.get("port", 1883)
                user, pw = d.get("username", ""), d.get("password", "")
            except Exception as exc:  # noqa: BLE001
                self.mqtt.error = f"No MQTT broker found from Supervisor ({exc}). Set one in Settings."
                log.warning(self.mqtt.error)
                return
        if not host:
            self.mqtt.error = "No MQTT broker configured"
            return
        await asyncio.to_thread(self.mqtt.start, host, port, user, pw, m["topic_prefix"])

    async def on_live_change(self, dev: str) -> None:
        if not self.settings.data["mqtt"]["enabled"] or not self.mqtt.connected:
            return
        now = time.time()
        if now - self._mqtt_last.get(dev, 0) < 20:
            return
        self._mqtt_last[dev] = now
        cfg = self.settings.data["devices"].get(dev)
        fix = self.recorder.live.get(dev)
        if not cfg or not fix:
            return
        pl = geo.match_place(fix["lat"], fix["lon"], self.all_places(), slack_m=25)
        dist = await self.distance_today(dev)
        self.mqtt.publish_device(dev, cfg["name"], pl["name"] if pl else None, dist, fix,
                                 dev.startswith("ext."))

    # ---------------------------------------------------------------- jobs
    def new_job(self, kind: str) -> dict:
        now = time.time()
        for k in [k for k, j in self.jobs.items() if now - j["created"] > 3600]:
            self.jobs.pop(k, None)
        job = {"id": uuid.uuid4().hex[:10], "kind": kind, "status": "running", "message": "starting",
               "progress": 0.0, "result": None, "created": now}
        self.jobs[job["id"]] = job
        return job

    def import_file(self, job: dict, path: str, filename: str, dev: str, thin: bool) -> None:
        """Blocking; run in a thread."""
        try:
            read = inserted = 0
            first = last = None
            state: dict = {}
            batch: list[tuple] = []
            size = max(1, os.path.getsize(path))
            t0 = time.time()

            def flush() -> int:
                nonlocal batch
                rows = batch
                batch = []
                if thin:
                    rows = self.recorder.thin_for_import(rows, state)
                return self.store.insert_many([(dev, r[0], r[1], r[2], r[3], None, "import") for r in rows])

            for fix in iter_fixes(path, filename):
                read += 1
                first = fix[0] if first is None else min(first, fix[0])
                last = fix[0] if last is None else max(last, fix[0])
                batch.append(fix)
                if len(batch) >= 5000:
                    inserted += flush()
                    job["message"] = f"read {read:,} points, kept {inserted:,}"
                    job["progress"] = min(0.95, (time.time() - t0) / 600)
            inserted += flush()
            job.update(status="done", progress=1.0, message=f"Imported {inserted:,} of {read:,} points",
                       result={"read": read, "inserted": inserted, "first": first, "last": last, "device": dev})
        except Exception as exc:  # noqa: BLE001
            log.exception("import failed")
            job.update(status="error", message=f"Import failed: {exc}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def backfill(self, job: dict, entities: list[str], days: int) -> None:
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days)
            chunks = list(day_chunks(start, end, 1))
            total = max(1, len(chunks) * len(entities))
            done = 0
            inserted = read = 0
            for eid in entities:
                state: dict = {}
                for a, b in chunks:
                    try:
                        data = await self.ha.history([eid], a, b)
                    except Exception as exc:  # noqa: BLE001
                        job["message"] = f"history error for {eid}: {exc}"
                        data = []
                    rows = []
                    for series in data:
                        for st in series:
                            fix = self.ha.fix_from_state(st)
                            if fix:
                                rows.append(fix)
                    rows.sort()
                    read += len(rows)
                    rows = self.recorder.thin_for_import(rows, state)
                    inserted += await asyncio.to_thread(
                        self.store.insert_many, [(eid, r[0], r[1], r[2], r[3], r[4], "ha-history") for r in rows])
                    done += 1
                    job["progress"] = done / total
                    job["message"] = f"{eid}: {inserted:,} points added"
            job.update(status="done", progress=1.0, message=f"Backfill finished: {inserted:,} new points",
                       result={"read": read, "inserted": inserted})
        except Exception as exc:  # noqa: BLE001
            log.exception("backfill failed")
            job.update(status="error", message=f"Backfill failed: {exc}")

    # ---------------------------------------------------------------- maintenance
    async def retention_loop(self) -> None:
        while True:
            try:
                days = self.settings.data["record"]["retention_days"]
                if days > 0:
                    n = await asyncio.to_thread(self.store.purge_before, int(time.time()) - days * 86400)
                    if n:
                        log.info("Retention purge removed %d points", n)
                await asyncio.to_thread(self.store.checkpoint)
            except Exception:  # noqa: BLE001
                log.exception("maintenance failed")
            await asyncio.sleep(6 * 3600)

    async def first_run(self) -> None:
        """On a fresh install, auto-track person entities that have GPS."""
        if self.settings.data["first_run_done"]:
            return
        for _ in range(90):
            if self.ha.connected and self.ha.states:
                break
            await asyncio.sleep(1)
        else:
            return
        cands = [c for c in self.ha.candidates() if c["domain"] == "person"]
        for c in cands:
            self.settings.ensure_device(c["id"], c["name"], True)
        self.settings.patch({"first_run_done": True})
        await self.ha.refresh_tracked()

    async def close(self) -> None:
        self.mqtt.stop()
        await self.ha.stop()
        await asyncio.to_thread(self.store.close)
