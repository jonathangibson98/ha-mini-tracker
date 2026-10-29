"""Persistent settings stored in /data/settings.json, edited from the web UI."""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import threading
import uuid

PALETTE = ["#03a9f4", "#ff9800", "#4caf50", "#e91e63", "#9c27b0", "#00bcd4", "#ffc107", "#f44336",
           "#8bc34a", "#3f51b5"]

DEFAULTS: dict = {
    "units": "mi",
    "theme": "auto",          # auto (sunset) | system | light | dark
    "tiles": "osm",           # osm | carto | custom
    "tile_url": "",
    "icons_folder": "custom_icons",
    "use_ha_zones": True,
    "devices": {},            # id -> {name, color, icon, enabled}
    "places": [],             # [{id, name, lat, lon, radius, icon, color}]
    "record": {"min_distance_m": 15, "heartbeat_min": 10, "max_accuracy_m": 200, "retention_days": 0},
    "visits": {"min_stay_min": 10, "radius_m": 75},
    "public": {"enabled": True},
    "ingest": {"enabled": False, "token": ""},
    "mqtt": {"enabled": False, "host": "", "port": 1883, "username": "", "password": "",
             "topic_prefix": "mini_tracker"},
    "first_run_done": False,
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
_FOLDER = re.compile(r"^[A-Za-z0-9_\-./]{1,100}$")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict) and k not in ("devices",):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


class Settings:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.data = self._load()
        if not self.data["ingest"].get("token"):
            self.data["ingest"]["token"] = secrets.token_urlsafe(24)
            self.save()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fp:
                return _merge(DEFAULTS, json.load(fp))
        except (OSError, ValueError):
            return copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(self.data, fp, indent=2)
            os.replace(tmp, self.path)

    # ---------------------------------------------------------- device helpers
    def ensure_device(self, dev: str, name: str | None = None, enabled: bool = True) -> dict:
        with self._lock:
            devs = self.data["devices"]
            if dev not in devs:
                color = PALETTE[len(devs) % len(PALETTE)]
                devs[dev] = {"name": name or dev.split(".", 1)[-1].replace("_", " ").title(),
                             "color": color, "icon": "", "enabled": enabled}
                self.save()
            return devs[dev]

    def public_view(self) -> dict:
        """Settings that are safe to show on the read-only map."""
        d = self.data
        return {"units": d["units"], "theme": d["theme"], "tiles": d["tiles"],
                "tile_url": d["tile_url"] if d["tiles"] == "custom" else ""}

    # ------------------------------------------------------------- patching
    def patch(self, patch: dict) -> dict:
        """Validate + apply a partial update. Unknown keys are ignored."""
        with self._lock:
            d = self.data
            if "units" in patch and patch["units"] in ("mi", "km"):
                d["units"] = patch["units"]
            if "theme" in patch and patch["theme"] in ("auto", "system", "light", "dark"):
                d["theme"] = patch["theme"]
            if "tiles" in patch and patch["tiles"] in ("osm", "carto", "custom"):
                d["tiles"] = patch["tiles"]
            if "tile_url" in patch:
                u = str(patch["tile_url"]).strip()
                d["tile_url"] = u if (u == "" or u.startswith(("http://", "https://"))) else d["tile_url"]
            if "icons_folder" in patch:
                f = str(patch["icons_folder"]).strip().strip("/")
                if f and _FOLDER.match(f) and ".." not in f:
                    d["icons_folder"] = f
            if "use_ha_zones" in patch:
                d["use_ha_zones"] = bool(patch["use_ha_zones"])
            if "first_run_done" in patch:
                d["first_run_done"] = bool(patch["first_run_done"])

            if isinstance(patch.get("record"), dict):
                r, p = d["record"], patch["record"]
                r["min_distance_m"] = _num(p.get("min_distance_m", r["min_distance_m"]), 0, 500, r["min_distance_m"])
                r["heartbeat_min"] = _num(p.get("heartbeat_min", r["heartbeat_min"]), 1, 240, r["heartbeat_min"])
                r["max_accuracy_m"] = _num(p.get("max_accuracy_m", r["max_accuracy_m"]), 10, 5000, r["max_accuracy_m"])
                r["retention_days"] = int(_num(p.get("retention_days", r["retention_days"]), 0, 36500, r["retention_days"]))
            if isinstance(patch.get("visits"), dict):
                v, p = d["visits"], patch["visits"]
                v["min_stay_min"] = _num(p.get("min_stay_min", v["min_stay_min"]), 2, 240, v["min_stay_min"])
                v["radius_m"] = _num(p.get("radius_m", v["radius_m"]), 20, 500, v["radius_m"])
            if isinstance(patch.get("public"), dict):
                d["public"]["enabled"] = bool(patch["public"].get("enabled", d["public"]["enabled"]))
            if isinstance(patch.get("ingest"), dict):
                d["ingest"]["enabled"] = bool(patch["ingest"].get("enabled", d["ingest"]["enabled"]))
            if isinstance(patch.get("mqtt"), dict):
                m, p = d["mqtt"], patch["mqtt"]
                m["enabled"] = bool(p.get("enabled", m["enabled"]))
                for k in ("host", "username", "password"):
                    if k in p:
                        m[k] = str(p[k]).strip()
                if "port" in p:
                    m["port"] = int(_num(p["port"], 1, 65535, m["port"]))
                if "topic_prefix" in p:
                    tp = re.sub(r"[^A-Za-z0-9_\-]", "", str(p["topic_prefix"]))[:40]
                    m["topic_prefix"] = tp or m["topic_prefix"]

            if isinstance(patch.get("devices"), dict):
                for dev, vals in patch["devices"].items():
                    if not _ID.match(str(dev)) or not isinstance(vals, dict):
                        continue
                    cur = self.ensure_device(dev)
                    if "name" in vals:
                        nm = str(vals["name"]).strip()[:40]
                        if nm:
                            cur["name"] = nm
                    if "color" in vals and _HEX.match(str(vals["color"])):
                        cur["color"] = vals["color"]
                    if "icon" in vals:
                        cur["icon"] = str(vals["icon"]).strip()[:120]
                    if "enabled" in vals:
                        cur["enabled"] = bool(vals["enabled"])
            self.save()
            return self.data

    # ---------------------------------------------------------------- places
    def upsert_place(self, p: dict) -> dict:
        with self._lock:
            lat, lon = float(p["lat"]), float(p["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("bad coordinates")
            name = str(p.get("name", "")).strip()[:60]
            if not name:
                raise ValueError("name required")
            pid = str(p.get("id") or "") or "p_" + uuid.uuid4().hex[:8]
            if not _ID.match(pid):
                raise ValueError("bad id")
            place = {"id": pid, "name": name, "lat": lat, "lon": lon,
                     "radius": _num(p.get("radius", 100), 10, 5000, 100),
                     "icon": str(p.get("icon", "")).strip()[:120],
                     "color": p["color"] if _HEX.match(str(p.get("color", ""))) else "#ff9800"}
            places = self.data["places"]
            for i, old in enumerate(places):
                if old["id"] == pid:
                    places[i] = place
                    break
            else:
                places.append(place)
            self.save()
            return place

    def delete_place(self, pid: str) -> bool:
        with self._lock:
            before = len(self.data["places"])
            self.data["places"] = [p for p in self.data["places"] if p["id"] != pid]
            self.save()
            return len(self.data["places"]) != before

    def delete_device(self, dev: str) -> None:
        with self._lock:
            self.data["devices"].pop(dev, None)
            self.save()

    def regenerate_token(self) -> str:
        with self._lock:
            self.data["ingest"]["token"] = secrets.token_urlsafe(24)
            self.save()
            return self.data["ingest"]["token"]
