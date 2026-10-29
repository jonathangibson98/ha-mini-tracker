"""Talks to Home Assistant Core through the Supervisor proxy (or a fake one in tests)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import aiohttp

from importers import parse_time

log = logging.getLogger("mini_tracker.ha")

FixCallback = Callable[[str, int, float, float, float | None, float | None, str], Awaitable[None]]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class HAClient:
    def __init__(self, base_url: str, token: str, on_fix: FixCallback) -> None:
        # base_url like http://supervisor/core  -> ws://supervisor/core/websocket, REST at /core/api
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.on_fix = on_fix
        self.states: dict[str, dict] = {}
        self.time_zone: str = "UTC"
        self.connected = False
        self.last_error = ""
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._next_id = 1
        self._stopped = False
        self.tracked: Callable[[], set[str]] = lambda: set()

    # -------------------------------------------------------------- helpers
    @property
    def ws_url(self) -> str:
        u = self.base_url
        if u.startswith("https://"):
            u = "wss://" + u[len("https://"):]
        elif u.startswith("http://"):
            u = "ws://" + u[len("http://"):]
        return u + "/websocket"

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def night(self) -> bool | None:
        sun = self.states.get("sun.sun")
        if sun:
            return sun.get("state") == "below_horizon"
        return None

    def fix_from_state(self, st: dict) -> tuple | None:
        a = st.get("attributes") or {}
        lat, lon = _num(a.get("latitude")), _num(a.get("longitude"))
        if lat is None or lon is None:
            return None
        ts = parse_time(st.get("last_updated") or st.get("last_changed"))
        if ts is None:
            return None
        batt = _num(a.get("battery_level", a.get("battery")))
        return (ts, lat, lon, _num(a.get("gps_accuracy")), batt)

    def candidates(self) -> list[dict]:
        out = []
        for eid, st in self.states.items():
            if eid.split(".", 1)[0] not in ("device_tracker", "person"):
                continue
            a = st.get("attributes") or {}
            if _num(a.get("latitude")) is None or _num(a.get("longitude")) is None:
                continue
            out.append({"id": eid, "name": a.get("friendly_name") or eid, "state": st.get("state"),
                        "domain": eid.split(".", 1)[0], "source_type": a.get("source_type")})
        return sorted(out, key=lambda c: (c["domain"], c["name"].lower()))

    def zones(self) -> list[dict]:
        out = []
        for eid, st in self.states.items():
            if not eid.startswith("zone."):
                continue
            a = st.get("attributes") or {}
            lat, lon = _num(a.get("latitude")), _num(a.get("longitude"))
            if lat is None or lon is None:
                continue
            out.append({"id": eid, "name": a.get("friendly_name") or eid, "lat": lat, "lon": lon,
                        "radius": _num(a.get("radius")) or 100.0, "icon": a.get("icon") or "",
                        "color": "#03a9f4", "source": "zone", "passive": bool(a.get("passive"))})
        return out

    # -------------------------------------------------------------- REST
    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def api_get(self, path: str, params: dict | None = None):
        session = await self._ensure_session()
        async with session.get(f"{self.base_url}/api/{path.lstrip('/')}", headers=self.headers,
                                     params=params, timeout=aiohttp.ClientTimeout(total=120)) as r:
            r.raise_for_status()
            return await r.json()

    async def history(self, entity_ids: list[str], start: datetime, end: datetime) -> list[list[dict]]:
        """Full-attribute state history for entities between two aware datetimes."""
        path = f"history/period/{start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')}"
        params = {"filter_entity_id": ",".join(entity_ids),
                  "end_time": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                  "significant_changes_only": "0", "no_attributes": "0"}
        return await self.api_get(path, params)

    # -------------------------------------------------------------- websocket
    async def _send(self, payload: dict) -> int:
        assert self._ws
        mid = self._next_id
        self._next_id += 1
        payload = {**payload, "id": mid}
        await self._ws.send_json(payload)
        return mid

    async def _handle_state(self, st: dict | None, source: str = "ha") -> None:
        if not st:
            return
        eid = st.get("entity_id")
        if not eid:
            return
        self.states[eid] = st
        if eid in self.tracked():
            fix = self.fix_from_state(st)
            if fix:
                await self.on_fix(eid, fix[0], fix[1], fix[2], fix[3], fix[4], source)

    async def run(self) -> None:
        backoff = 2
        while not self._stopped:
            try:
                await self._connect_once()
                backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.warning("Home Assistant connection problem: %s (retry in %ss)", exc, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _connect_once(self) -> None:
        session = await self._ensure_session()
        async with session.ws_connect(self.ws_url, heartbeat=30, max_msg_size=0) as ws:
            self._ws = ws
            self._next_id = 1
            msg = await ws.receive_json()
            if msg.get("type") != "auth_required":
                raise RuntimeError(f"unexpected hello: {msg}")
            await ws.send_json({"type": "auth", "access_token": self.token})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"auth failed: {msg}")

            cfg_id = await self._send({"type": "get_config"})
            states_id = await self._send({"type": "get_states"})
            sub_id = await self._send({"type": "subscribe_events", "event_type": "state_changed"})
            self.connected = True
            self.last_error = ""
            log.info("Connected to Home Assistant")

            async for m in ws:
                if m.type != aiohttp.WSMsgType.TEXT:
                    if m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                data = m.json()
                t = data.get("type")
                if t == "result":
                    if data.get("id") == cfg_id and data.get("success"):
                        self.time_zone = (data.get("result") or {}).get("time_zone") or "UTC"
                    elif data.get("id") == states_id and data.get("success"):
                        for st in data.get("result") or []:
                            await self._handle_state(st, "ha-startup")
                    elif not data.get("success") and data.get("id") in (cfg_id, states_id, sub_id):
                        log.warning("HA command failed: %s", data.get("error"))
                elif t == "event":
                    ev = data.get("event") or {}
                    if ev.get("event_type") == "state_changed":
                        await self._handle_state((ev.get("data") or {}).get("new_state"))
        raise RuntimeError("websocket closed")

    async def refresh_tracked(self) -> None:
        """Re-emit current fixes for newly tracked entities (called after settings change)."""
        for eid in list(self.tracked()):
            st = self.states.get(eid)
            if st:
                await self._handle_state(st, "ha-startup")

    async def stop(self) -> None:
        self._stopped = True
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()


def day_chunks(start: datetime, end: datetime, days: int = 1):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt
