"""Parse location-history files into (ts, lat, lon, acc) fixes.

Supported: Google Takeout Records.json (old + new Timeline exports), GeoJSON / Overland
exports, GPX, OwnTracks .rec, CSV, and any JSON list of objects with lat/lon/time keys.
JSON files are streamed so a multi-hundred-MB Takeout file does not blow up a Pi's RAM.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Iterator

from geo import valid_fix

CHUNK = 1 << 20

_LAT_KEYS = ("lat", "latitude", "Lat", "Latitude")
_LON_KEYS = ("lon", "lng", "long", "longitude", "Lon", "Lng", "Longitude")
_TIME_KEYS = ("ts", "timestamp", "timestampMs", "time", "tst", "isoDate", "date", "datetime", "recorded_at",
              "startTimestamp", "t")
_ACC_KEYS = ("acc", "accuracy", "horizontal_accuracy", "gps_accuracy", "horizontalAccuracy")
_DEG_PAIR = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*,\s*(-?\d+(?:\.\d+)?)\s*°?")


def parse_time(v) -> int | None:
    """Epoch seconds from seconds / millis / ISO string. None if unparseable."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v.strip())):
        n = float(v)
        if n > 1e17:      # nanoseconds
            n /= 1e9
        elif n > 1e14:    # microseconds
            n /= 1e6
        elif n > 1e11:    # milliseconds
            n /= 1e3
        return int(n) if n > 0 else None
    if isinstance(v, str):
        s = v.strip().replace("Z", "+00:00").replace("z", "+00:00")
        # fromisoformat dislikes >6 fractional digits
        s = re.sub(r"(\.\d{6})\d+", r"\1", s)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return None


def _first(d: dict, keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _fix(lat, lon, ts, acc=None):
    if not valid_fix(lat, lon):
        return None
    t = parse_time(ts)
    if t is None or t < 946684800:  # before year 2000 = garbage
        return None
    try:
        acc = float(acc) if acc is not None else None
    except (TypeError, ValueError):
        acc = None
    return (t, float(lat), float(lon), acc)


def extract_fixes(obj) -> list[tuple]:
    """Pull zero or more fixes out of one decoded JSON object."""
    out: list[tuple] = []
    if not isinstance(obj, dict):
        return out

    # GeoJSON Feature
    geom = obj.get("geometry")
    if isinstance(geom, dict) and geom.get("type") == "Point" and isinstance(geom.get("coordinates"), list):
        props = obj.get("properties") or {}
        c = geom["coordinates"]
        f = _fix(c[1], c[0], _first(props, _TIME_KEYS), _first(props, _ACC_KEYS))
        return [f] if f else []

    # Google Takeout Records.json (E7 integers)
    if "latitudeE7" in obj and "longitudeE7" in obj:
        f = _fix(obj["latitudeE7"] / 1e7, obj["longitudeE7"] / 1e7,
                 obj.get("timestamp", obj.get("timestampMs")), obj.get("accuracy"))
        return [f] if f else []

    # New Google Timeline export: segments with a timelinePath, or rawSignals with a position
    tp = obj.get("timelinePath")
    if isinstance(tp, list):
        for pt in tp:
            if isinstance(pt, dict):
                m = _DEG_PAIR.search(str(pt.get("point", "")))
                if m:
                    f = _fix(float(m.group(1)), float(m.group(2)), pt.get("time"))
                    if f:
                        out.append(f)
        return out
    pos = obj.get("position")
    if isinstance(pos, dict):
        m = _DEG_PAIR.search(str(pos.get("LatLng", "")))
        if m:
            f = _fix(float(m.group(1)), float(m.group(2)), pos.get("timestamp"), pos.get("accuracyMeters"))
            return [f] if f else []

    # OwnTracks / generic flat objects
    lat, lon = _first(obj, _LAT_KEYS), _first(obj, _LON_KEYS)
    if lat is not None and lon is not None:
        f = _fix(lat, lon, _first(obj, _TIME_KEYS), _first(obj, _ACC_KEYS))
        return [f] if f else []
    loc = obj.get("location") or obj.get("coords")
    if isinstance(loc, dict):
        merged = {**obj, **loc}
        merged.pop("location", None)
        merged.pop("coords", None)
        return extract_fixes(merged)
    return out


# ------------------------------------------------------------------ streaming JSON


def _iter_array_items(fp: io.TextIOBase) -> Iterator[object]:
    """Yield elements of the first JSON array found (top level, or the first `"key": [` in an object)."""
    dec = json.JSONDecoder()
    buf = fp.read(CHUNK)
    # find the start of the array
    stripped = buf.lstrip()
    pos = len(buf) - len(stripped)
    if stripped.startswith("["):
        pos += 1
    else:
        m = re.search(r'"[^"\\]{1,40}"\s*:\s*\[', buf)
        while m is None:
            more = fp.read(CHUNK)
            if not more:
                return
            buf += more
            m = re.search(r'"[^"\\]{1,40}"\s*:\s*\[', buf)
        pos = m.end()
    while True:
        # skip whitespace + commas
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n,":
                pos += 1
            if pos < len(buf):
                break
            more = fp.read(CHUNK)
            if not more:
                return
            buf = buf[pos:] + more
            pos = 0
        if buf[pos] == "]":
            return
        try:
            obj, end = dec.raw_decode(buf, pos)
        except ValueError:
            more = fp.read(CHUNK)
            if not more:
                return
            buf = buf[pos:] + more
            pos = 0
            continue
        yield obj
        pos = end
        if pos > CHUNK * 2:  # keep the buffer small
            buf = buf[pos:]
            pos = 0


def _iter_ndjson(fp: io.TextIOBase) -> Iterator[object]:
    for line in fp:
        line = line.strip()
        if not line:
            continue
        if "\t" in line:  # OwnTracks .rec: "2020-01-01T00:00:00Z\t*\t{json}"
            line = line.split("\t")[-1]
        try:
            yield json.loads(line)
        except ValueError:
            continue


# ------------------------------------------------------------------ formats


def _iter_gpx(fp) -> Iterator[tuple]:
    import xml.etree.ElementTree as ET

    for _, el in ET.iterparse(fp, events=("end",)):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept", "wpt"):
            time_el = None
            for child in el:
                if child.tag.rsplit("}", 1)[-1] == "time":
                    time_el = child
                    break
            f = _fix(el.get("lat"), el.get("lon"), time_el.text if time_el is not None else None)
            if f:
                yield f
            el.clear()


def _iter_csv(fp) -> Iterator[tuple]:
    rd = csv.DictReader(fp)
    for row in rd:
        for f in extract_fixes(row):
            yield f


def iter_fixes(path: str, filename: str = "") -> Iterator[tuple]:
    """Stream fixes out of an uploaded file on disk."""
    name = (filename or path).lower()
    if name.endswith(".gpx"):
        with open(path, "rb") as fp:
            yield from _iter_gpx(fp)
        return
    if name.endswith(".csv"):
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fp:
            yield from _iter_csv(fp)
        return
    with open(path, encoding="utf-8-sig", errors="replace") as fp:
        head = fp.read(2048)
        fp.seek(0)
        first = head.lstrip()[:1]
        if head.lstrip().startswith("<?xml") or head.lstrip().startswith("<gpx"):
            fp.close()
            with open(path, "rb") as bfp:
                yield from _iter_gpx(bfp)
            return
        if first in "[{" and not _looks_ndjson(head):
            items = _iter_array_items(fp)
        else:
            items = _iter_ndjson(fp)
        for obj in items:
            for f in extract_fixes(obj):
                yield f


def _looks_ndjson(head: str) -> bool:
    lines = [ln for ln in head.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    if "\t" in lines[0] and "{" in lines[0]:
        return True
    try:
        json.loads(lines[0])
        return lines[0].lstrip().startswith("{") and lines[0].rstrip().endswith("}")
    except ValueError:
        return False
