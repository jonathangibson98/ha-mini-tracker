import json, os, tempfile
import geo, importers
from settings import Settings
from store import Store


def test_haversine_and_simplify():
    d = geo.haversine_m(40.0, -75.0, 40.0, -74.99)
    assert 840 < d < 860
    pts = [(i, 40.0 + i * 1e-5, -75.0, None, None) for i in range(1000)]  # straight line
    assert len(geo.simplify(pts, 5)) == 2
    assert len(geo.simplify(pts, 0, max_points=50)) <= 51


def test_detect_stays_and_timeline():
    home, work = (40.0, -75.0), (40.05, -75.0)
    pts = []
    t = 1_700_000_000
    for i in range(10):                      # 45 min at home
        pts.append((t + i * 300, home[0] + 1e-5 * (i % 2), home[1], 5, None))
    t2 = pts[-1][0]
    for i in range(1, 11):                   # drive 10 steps
        f = i / 10
        pts.append((t2 + i * 60, home[0] + (work[0] - home[0]) * f, home[1], 5, None))
    t3 = pts[-1][0]
    for i in range(1, 9):                    # 35 min at work
        pts.append((t3 + i * 300, work[0], work[1], 5, None))
    places = [{"id": "home", "name": "Home", "lat": home[0], "lon": home[1], "radius": 100},
              {"id": "work", "name": "Work", "lat": work[0], "lon": work[1], "radius": 100}]
    tl = geo.build_timeline(pts, places, 600, 75)
    assert [s["place"] for s in tl["stays"]] == ["Home", "Work"]
    assert len(tl["trips"]) == 1
    assert 5400 < tl["trips"][0]["distance_m"] < 5700
    assert tl["trips"][0]["from"] == "Home" and tl["trips"][0]["to"] == "Work"
    stats = geo.place_stats(pts, places, 600, 75)
    assert {s["name"] for s in stats} == {"Home", "Work"}


def test_place_matching_prefers_smaller_radius():
    places = [{"id": "a", "name": "Big", "lat": 1, "lon": 1, "radius": 500},
              {"id": "b", "name": "Small", "lat": 1, "lon": 1, "radius": 50}]
    assert geo.match_place(1, 1, places)["name"] == "Small"
    assert geo.match_place(2, 2, places) is None


def test_valid_fix():
    assert not geo.valid_fix(0, 0) and not geo.valid_fix(91, 0) and not geo.valid_fix("x", 1)
    assert geo.valid_fix(40.1, -75.2)


def test_parse_time_variants():
    assert importers.parse_time(1700000000) == 1700000000
    assert importers.parse_time(1700000000123) == 1700000000
    assert importers.parse_time("1700000000123") == 1700000000
    assert importers.parse_time("2023-11-14T22:13:20Z") == 1700000000
    assert importers.parse_time("2023-11-14T22:13:20.123456789Z") == 1700000000
    assert importers.parse_time("2023-11-14T17:13:20-05:00") == 1700000000
    assert importers.parse_time("garbage") is None


def _write(tmp, name, text):
    p = os.path.join(tmp, name)
    open(p, "w").write(text)
    return p


def test_import_formats(tmp_path):
    tmp = str(tmp_path)
    takeout = json.dumps({"locations": [
        {"latitudeE7": 407000000, "longitudeE7": -740000000, "timestamp": "2023-11-14T22:13:20Z", "accuracy": 12},
        {"latitudeE7": 407010000, "longitudeE7": -740000000, "timestampMs": "1700000100000"}]}, indent=2)
    assert [f[0] for f in importers.iter_fixes(_write(tmp, "Records.json", takeout))] == [1700000000, 1700000100]

    geojson = json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.0, 40.7]},
         "properties": {"timestamp": "2023-11-14T22:13:20Z", "horizontal_accuracy": 5}}]})
    fx = list(importers.iter_fixes(_write(tmp, "o.geojson", geojson)))
    assert fx == [(1700000000, 40.7, -74.0, 5.0)]

    timeline = json.dumps({"semanticSegments": [{"timelinePath": [
        {"point": "40.7°, -74.0°", "time": "2023-11-14T22:13:20.000-05:00"}]}], "rawSignals": []})
    assert len(list(importers.iter_fixes(_write(tmp, "Timeline.json", timeline)))) == 1

    gpx = ('<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
           '<trkpt lat="40.7" lon="-74.0"><time>2023-11-14T22:13:20Z</time></trkpt>'
           '<trkpt lat="40.71" lon="-74.0"><time>2023-11-14T22:14:20Z</time></trkpt></trkseg></trk></gpx>')
    assert len(list(importers.iter_fixes(_write(tmp, "t.gpx", gpx)))) == 2

    generic = json.dumps([{"lat": 40.7, "lng": -74.0, "time": 1700000000}, {"latitude": 0, "longitude": 0, "time": 1}])
    assert len(list(importers.iter_fixes(_write(tmp, "g.json", generic)))) == 1

    owntracks = ('2023-11-14T22:13:20Z\t*\t{"_type":"location","lat":40.7,"lon":-74.0,"tst":1700000000,"acc":9}\n'
                 '2023-11-14T22:14:20Z\t*\t{"_type":"location","lat":40.71,"lon":-74.0,"tst":1700000060}\n')
    assert len(list(importers.iter_fixes(_write(tmp, "2023-11.rec", owntracks)))) == 2

    csvtxt = "timestamp,lat,lon,accuracy\n1700000000,40.7,-74.0,5\n1700000060,40.71,-74.0,6\n"
    assert len(list(importers.iter_fixes(_write(tmp, "p.csv", csvtxt)))) == 2


def test_streaming_handles_chunk_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(importers, "CHUNK", 257)  # force many refills mid-object
    items = [{"latitudeE7": 407000000 + i, "longitudeE7": -740000000, "timestamp": 1700000000 + i} for i in range(400)]
    p = os.path.join(str(tmp_path), "big.json")
    open(p, "w").write(json.dumps({"locations": items}, indent=2))
    assert len(list(importers.iter_fixes(p))) == 400


def test_store_dedupe_and_range(tmp_path):
    s = Store(os.path.join(str(tmp_path), "t.db"))
    assert s.insert("d", 1_700_000_000, 40.0, -75.0)
    assert not s.insert("d", 1_700_000_000, 41.0, -75.0)  # same (dev, ts) ignored
    assert s.insert_many([("d", 1_700_000_001 + i, 40.0, -75.0, None, None, "t") for i in range(100)]) == 100
    assert s.insert_many([("d", 1_700_000_001, 40.0, -75.0, None, None, "t")]) == 0
    assert len(s.range("d", 0, 2_000_000_000)) == 101
    assert s.last("d")[0] == 1_700_000_100
    assert s.purge_before(1_700_000_050) == 50
    s.close()


def test_settings_validation(tmp_path):
    st = Settings(os.path.join(str(tmp_path), "s.json"))
    assert st.data["ingest"]["token"]
    st.patch({"units": "banana", "record": {"min_distance_m": 99999}, "tile_url": "javascript:alert(1)",
              "icons_folder": "../../etc", "devices": {"person.jon": {"name": "Jon", "color": "red"}}})
    assert st.data["units"] == "mi"
    assert st.data["record"]["min_distance_m"] == 500
    assert st.data["tile_url"] == ""
    assert st.data["icons_folder"] == "custom_icons"
    assert st.data["devices"]["person.jon"]["name"] == "Jon" and st.data["devices"]["person.jon"]["color"].startswith("#")
    p = st.upsert_place({"name": "Mom's", "lat": 34.1, "lon": -118.2, "radius": 10000})
    assert p["radius"] == 5000
    st2 = Settings(os.path.join(str(tmp_path), "s.json"))   # persisted
    assert len(st2.data["places"]) == 1
