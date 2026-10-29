"""Takes raw fixes from HA / the ingest API / imports and decides what to keep."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from geo import haversine_m, valid_fix
from settings import Settings
from store import Store

log = logging.getLogger("mini_tracker.recorder")

MIN_TS = 946684800  # 2000-01-01


class Recorder:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.live: dict[str, dict] = {}                  # dev -> latest fix (stored or not)
        self._last_stored: dict[str, tuple] = {}         # dev -> (ts, lat, lon)
        self.listeners: list[Callable[[str], Awaitable[None]]] = []

    async def warm_up(self) -> None:
        """Load last known position of every device from the DB (so the map is not empty on boot)."""
        devs = await asyncio.to_thread(self.store.devices)
        for d in devs:
            row = await asyncio.to_thread(self.store.last, d["dev"])
            if row:
                self._remember(d["dev"], row[0], row[1], row[2], row[3], row[4], stored=True)

    def _remember(self, dev, ts, lat, lon, acc, batt, stored: bool) -> None:
        cur = self.live.get(dev)
        if cur is None or ts >= cur["ts"]:
            self.live[dev] = {"ts": ts, "lat": lat, "lon": lon, "acc": acc, "batt": batt}
        if stored:
            ls = self._last_stored.get(dev)
            if ls is None or ts >= ls[0]:
                self._last_stored[dev] = (ts, lat, lon)

    def should_store(self, dev: str, ts: int, lat: float, lon: float, acc) -> bool:
        rec = self.settings.data["record"]
        if acc is not None and acc > rec["max_accuracy_m"]:
            return False
        ls = self._last_stored.get(dev)
        if ls is None or ts < ls[0]:
            return True  # first point, or back-filled/out-of-order data
        if ts - ls[0] >= rec["heartbeat_min"] * 60:
            return True
        return haversine_m(ls[1], ls[2], lat, lon) >= rec["min_distance_m"]

    async def handle_fix(self, dev: str, ts: int, lat: float, lon: float, acc=None, batt=None,
                         src: str = "ha") -> bool:
        if not valid_fix(lat, lon):
            return False
        ts = int(ts)
        if ts < MIN_TS or ts > time.time() + 600:
            return False
        stored = False
        if self.should_store(dev, ts, lat, lon, acc):
            stored = await asyncio.to_thread(self.store.insert, dev, ts, lat, lon, acc, batt, src)
        # Always update the live marker, even if we did not store the point.
        acc_ok = acc is None or acc <= self.settings.data["record"]["max_accuracy_m"]
        if acc_ok:
            self._remember(dev, ts, lat, lon, acc, batt, stored=stored)
        if stored or acc_ok:
            for cb in self.listeners:
                try:
                    await cb(dev)
                except Exception:  # noqa: BLE001
                    log.exception("listener failed")
        return stored

    def thin_for_import(self, rows: list[tuple], state: dict) -> list[tuple]:
        """Apply min-distance/heartbeat thinning to chronological import rows.

        rows: (ts, lat, lon, acc). `state` carries the last kept point between batches.
        """
        rec = self.settings.data["record"]
        keep = []
        last = state.get("last")
        for r in rows:
            ts, lat, lon = r[0], r[1], r[2]
            if last is not None and ts >= last[0]:
                if ts - last[0] < rec["heartbeat_min"] * 60 and haversine_m(last[1], last[2], lat, lon) < max(
                        5.0, rec["min_distance_m"] / 2):
                    continue
            keep.append(r)
            last = (ts, lat, lon)
        state["last"] = last
        return keep

    def purge_cache(self, dev: str) -> None:
        self.live.pop(dev, None)
        self._last_stored.pop(dev, None)
