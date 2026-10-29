"""Pure geometry + analysis helpers (no I/O)."""
from __future__ import annotations

import math

EARTH_R = 6371008.8  # metres

# A point is a tuple: (ts, lat, lon, acc, batt)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def path_length_m(points: list[tuple]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += haversine_m(a[1], a[2], b[1], b[2])
    return total


def valid_fix(lat, lon) -> bool:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    if lat == 0 and lon == 0:  # classic "no fix" value
        return False
    return not (math.isnan(lat) or math.isnan(lon))


# ---------------------------------------------------------------- places


def match_place(lat: float, lon: float, places: list[dict], slack_m: float = 0.0) -> dict | None:
    """Nearest place whose radius contains the point. Smaller radius wins ties."""
    best = None
    best_key = None
    for p in places:
        d = haversine_m(lat, lon, p["lat"], p["lon"])
        r = float(p.get("radius") or 100) + slack_m
        if d <= r:
            key = (float(p.get("radius") or 100), d)
            if best_key is None or key < best_key:
                best, best_key = p, key
    return best


# ---------------------------------------------------------------- stops


def detect_stays(points: list[tuple], min_stay_s: int = 600, radius_m: float = 75.0,
                 max_gap_s: int = 6 * 3600) -> list[dict]:
    """Classic stay-point detection.

    Returns [{start, end, lat, lon, i0, i1, n}] with i0/i1 inclusive indexes.
    """
    stays: list[dict] = []
    n = len(points)
    i = 0
    while i < n:
        j = i + 1
        while j < n:
            if points[j][0] - points[j - 1][0] > max_gap_s:
                break
            if haversine_m(points[i][1], points[i][2], points[j][1], points[j][2]) > radius_m:
                break
            j += 1
        last = j - 1
        if last > i and points[last][0] - points[i][0] >= min_stay_s:
            seg = points[i:last + 1]
            lat = sum(p[1] for p in seg) / len(seg)
            lon = sum(p[2] for p in seg) / len(seg)
            stays.append({"start": points[i][0], "end": points[last][0],
                          "lat": lat, "lon": lon, "i0": i, "i1": last, "n": len(seg)})
            i = last + 1
        else:
            i += 1
    return stays


def build_timeline(points: list[tuple], places: list[dict], min_stay_s: int = 600,
                   radius_m: float = 75.0) -> dict:
    """Stops + trips + summary for one device over a range of points."""
    stays = detect_stays(points, min_stay_s, radius_m)
    for s in stays:
        pl = match_place(s["lat"], s["lon"], places, slack_m=25)
        s["place"] = pl["name"] if pl else None
        s["place_id"] = pl["id"] if pl else None
        s["duration"] = s["end"] - s["start"]

    trips = []
    for a, b in zip(stays, stays[1:]):
        seg = points[a["i1"]:b["i0"] + 1]
        dist = path_length_m(seg)
        if dist < 50:  # drift between two nearby stops is not a trip
            continue
        trips.append({"start": a["end"], "end": b["start"], "distance_m": dist,
                      "duration": b["start"] - a["end"],
                      "from": a["place"], "to": b["place"],
                      "from_pt": [a["lat"], a["lon"]], "to_pt": [b["lat"], b["lon"]]})
    # Movement before the first stop / after the last one
    if points:
        if not stays:
            dist = path_length_m(points)
            if dist >= 50:
                trips.append({"start": points[0][0], "end": points[-1][0], "distance_m": dist,
                              "duration": points[-1][0] - points[0][0], "from": None, "to": None,
                              "from_pt": [points[0][1], points[0][2]],
                              "to_pt": [points[-1][1], points[-1][2]]})
        else:
            if stays[0]["i0"] > 0:
                seg = points[:stays[0]["i0"] + 1]
                dist = path_length_m(seg)
                if dist >= 50:
                    trips.insert(0, {"start": points[0][0], "end": stays[0]["start"],
                                     "distance_m": dist, "duration": stays[0]["start"] - points[0][0],
                                     "from": None, "to": stays[0]["place"],
                                     "from_pt": [points[0][1], points[0][2]],
                                     "to_pt": [stays[0]["lat"], stays[0]["lon"]]})
            if stays[-1]["i1"] < len(points) - 1:
                seg = points[stays[-1]["i1"]:]
                dist = path_length_m(seg)
                if dist >= 50:
                    trips.append({"start": stays[-1]["end"], "end": points[-1][0],
                                  "distance_m": dist, "duration": points[-1][0] - stays[-1]["end"],
                                  "from": stays[-1]["place"], "to": None,
                                  "from_pt": [stays[-1]["lat"], stays[-1]["lon"]],
                                  "to_pt": [points[-1][1], points[-1][2]]})
    for t in trips:
        t["avg_mph"] = (t["distance_m"] / t["duration"] * 2.23694) if t["duration"] > 0 else 0.0

    summary = {
        "distance_m": sum(t["distance_m"] for t in trips),
        "moving_s": sum(t["duration"] for t in trips),
        "stops": len(stays),
        "points": len(points),
    }
    return {"stays": stays, "trips": trips, "summary": summary}


def place_stats(points: list[tuple], places: list[dict], min_stay_s: int, radius_m: float) -> list[dict]:
    """Total time + visit count per place over a range of points."""
    stays = detect_stays(points, min_stay_s, radius_m)
    out: dict[str, dict] = {}
    for s in stays:
        pl = match_place(s["lat"], s["lon"], places, slack_m=25)
        if not pl:
            continue
        o = out.setdefault(pl["id"], {"id": pl["id"], "name": pl["name"], "seconds": 0, "visits": 0,
                                      "last": 0})
        o["seconds"] += s["end"] - s["start"]
        o["visits"] += 1
        o["last"] = max(o["last"], s["end"])
    return sorted(out.values(), key=lambda x: -x["seconds"])


# ---------------------------------------------------------------- simplify


def _xy(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    return (math.radians(lon) * EARTH_R * math.cos(math.radians(lat0)), math.radians(lat) * EARTH_R)


def simplify(points: list[tuple], tol_m: float = 5.0, max_points: int = 6000) -> list[tuple]:
    """Douglas-Peucker (iterative) then stride-sample if still too many."""
    n = len(points)
    if n <= 2:
        return list(points)
    if tol_m > 0:
        lat0 = points[0][1]
        xy = [_xy(p[1], p[2], lat0) for p in points]
        keep = [False] * n
        keep[0] = keep[-1] = True
        stack = [(0, n - 1)]
        while stack:
            a, b = stack.pop()
            if b <= a + 1:
                continue
            ax, ay = xy[a]
            bx, by = xy[b]
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            best_d, best_i = -1.0, -1
            for i in range(a + 1, b):
                px, py = xy[i]
                if seg2 == 0:
                    d = math.hypot(px - ax, py - ay)
                else:
                    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                    d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
                if d > best_d:
                    best_d, best_i = d, i
            if best_d > tol_m:
                keep[best_i] = True
                stack.append((a, best_i))
                stack.append((best_i, b))
        points = [p for p, k in zip(points, keep) if k]
    if len(points) > max_points:
        step = len(points) / max_points
        idx = sorted({int(i * step) for i in range(max_points)} | {len(points) - 1})
        points = [points[i] for i in idx]
    return list(points)
