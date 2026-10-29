import asyncio, json, os, time
import aiohttp
from aiohttp.test_utils import TestServer, TestClient

from ctx import Ctx
from web import make_admin_app, make_public_app, PublicServer
from fake_ha import FakeHA, person, iso

T0 = int(time.time()) - 6 * 3600
HOME = (40.0000, -75.0000)
WORK = (40.0500, -75.0000)


def run(coro):
    return asyncio.run(coro)


async def wait_for(cond, timeout=8):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def scenario(tmp):
    cfg = os.path.join(tmp, "ha_config")
    os.makedirs(os.path.join(cfg, "custom_icons", "rooms"))
    open(os.path.join(cfg, "custom_icons", "garage.svg"), "w").write('<svg viewBox="0 0 24 24"><path d="M0 0"/></svg>')
    open(os.path.join(tmp, "secret.svg"), "w").write("<svg/>")

    hist_states = [person("person.jon", "Jon", HOME[0] + i * 1e-4, HOME[1], T0 - 86400 + i * 300) for i in range(30)]
    fake = FakeHA({
        "person.jon": person("person.jon", "Jon", HOME[0], HOME[1], T0),
        "person.mj": person("person.mj", "MJ", WORK[0], WORK[1], T0),
        "device_tracker.pixel": person("device_tracker.pixel", "Pixel", 41, -75, T0),
        "zone.home": {"entity_id": "zone.home", "state": "0", "last_updated": iso(T0), "last_changed": iso(T0),
                      "attributes": {"friendly_name": "Home", "latitude": HOME[0], "longitude": HOME[1], "radius": 120}},
        "sun.sun": {"entity_id": "sun.sun", "state": "below_horizon", "last_updated": iso(T0), "last_changed": iso(T0), "attributes": {}},
    }, history={"person.jon": hist_states})
    await fake.start()

    ctx = Ctx(os.path.join(tmp, "data"), cfg, f"http://127.0.0.1:{fake.port}/core", "t", "http://127.0.0.1:1")
    await ctx.recorder.warm_up()
    ha_task = asyncio.create_task(ctx.ha.run())
    first = asyncio.create_task(ctx.first_run())
    pub = PublicServer(ctx, 0)
    admin = TestClient(TestServer(make_admin_app(ctx, pub)))
    public = TestClient(TestServer(make_public_app(ctx)))
    await admin.start_server(); await public.start_server()
    try:
        assert await wait_for(lambda: ctx.ha.connected and ctx.settings.data["first_run_done"])
        await first
        assert ctx.ha.time_zone == "America/New_York"

        # first run auto-tracks persons only (not the raw device_tracker)
        assert set(ctx.settings.data["devices"]) == {"person.jon", "person.mj"}
        r = await admin.get("/api/candidates"); cands = (await r.json())["candidates"]
        assert {c["id"] for c in cands} == {"person.jon", "person.mj", "device_tracker.pixel"}

        # live state: Jon matches the HA zone "Home"
        st = await (await admin.get("/api/state")).json()
        assert st["night"] is True and st["ha"] is True
        jon = next(d for d in st["devices"] if d["id"] == "person.jon")
        assert jon["place"] == "Home"

        # a drive from home to work, pushed as HA state_changed events (with a stationary period each end)
        t = T0 + 600
        for i in range(8):   # at home 40 min
            await fake.push(person("person.jon", "Jon", HOME[0], HOME[1] + 1e-5 * i, t + i * 300)); 
        t += 8 * 300
        for i in range(1, 11):
            await fake.push(person("person.jon", "Jon", HOME[0] + (WORK[0] - HOME[0]) * i / 10, HOME[1], t + i * 60))
        t += 10 * 60
        for i in range(1, 9):
            await fake.push(person("person.jon", "Jon", WORK[0], WORK[1], t + i * 300))
        t += 8 * 300
        assert await wait_for(lambda: ctx.recorder.live["person.jon"]["ts"] == t)
        await asyncio.sleep(0.2)

        h = await (await admin.get(f"/api/history?dev=person.jon&start={T0-100}&end={t+10}")).json()
        hj = h["devices"]["person.jon"]
        assert hj["count"] > 15 and 5400 < hj["distance_m"] < 5800
        # distance filter: stationary jitter (1 m) mostly thinned away
        assert hj["count"] < 8 + 10 + 8 + 5

        tl = await (await admin.get(f"/api/timeline?dev=person.jon&start={T0-100}&end={t+10}")).json()
        assert [s["place"] for s in tl["stays"]][0] == "Home"
        assert len(tl["trips"]) >= 1 and tl["summary"]["distance_m"] > 5000

        stats = await (await admin.get("/api/places/stats?dev=person.jon&days=1")).json()
        assert stats and stats[0]["name"] == "Home"

        # custom place + public read-only app
        r = await admin.post("/api/places", json={"name": "Office", "lat": WORK[0], "lon": WORK[1], "radius": 150})
        assert r.status == 200
        pl = await (await public.get("/api/places")).json()
        assert {p["name"] for p in pl} >= {"Home", "Office"}
        tl2 = await (await public.get(f"/api/timeline?dev=person.jon&start={T0-100}&end={t+10}")).json()
        assert "Office" in [s["place"] for s in tl2["stays"]]

        # public app is read-only and exposes no settings
        assert (await public.get("/api/settings")).status == 404
        assert (await public.post("/api/places", json={})).status == 405
        assert (await public.get("/")).status == 200

        # ---- ingest: disabled by default, then OwnTracks + Overland + generic
        r = await public.post("/api/ingest", json={"lat": 1, "lon": 1}); assert r.status == 403
        await admin.post("/api/settings", json={"ingest": {"enabled": True}})
        token = (await (await admin.get("/api/settings")).json())["ingest"]["token"]
        r = await public.post("/api/ingest", json={"lat": 1, "lon": 1}); assert r.status == 401
        H = {"Authorization": f"Bearer {token}"}
        now = int(time.time())
        r = await public.post("/api/ingest", headers=H, json={"_type": "location", "lat": 34.05, "lon": -118.24,
                              "tst": now - 60, "acc": 8, "batt": 55, "topic": "owntracks/jon/pixel"})
        assert r.status == 200 and await r.json() == []
        r = await public.post("/api/ingest?device=bike", headers=H, json={"locations": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-118.25, 34.06]},
             "properties": {"timestamp": iso(now - 30), "horizontal_accuracy": 5, "battery_level": 0.5}}]})
        assert (await r.json())["result"] == "ok"
        r = await public.post(f"/api/ingest?token={token}", json={"device": "my gps", "lat": 35.0, "lon": -119.0})
        assert (await r.json())["stored"] == 1
        devs = ctx.settings.data["devices"]
        assert {"ext.jon_pixel", "ext.bike", "ext.my_gps"} <= set(devs)
        st = await (await admin.get("/api/state")).json()
        assert next(d for d in st["devices"] if d["id"] == "ext.jon_pixel")["batt"] == 55

        # ---- import (GPX) into a *new* device, and into an existing one (Takeout)
        gpx = ('<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>' +
               "".join(f'<trkpt lat="{36 + i * 0.001}" lon="-100"><time>2023-11-14T22:{i:02d}:20Z</time></trkpt>' for i in range(30)) +
               "</trkseg></trk></gpx>")
        fd = aiohttp.FormData(); fd.add_field("new_name", "Old Phone"); fd.add_field("thin", "0")
        fd.add_field("file", gpx.encode(), filename="trip.gpx")
        r = await admin.post("/api/import", data=fd); jid = (await r.json())["job"]
        assert await wait_for(lambda: ctx.jobs[jid]["status"] != "running")
        assert ctx.jobs[jid]["status"] == "done", ctx.jobs[jid]
        assert ctx.jobs[jid]["result"]["inserted"] == 30
        assert "ext.old_phone" in ctx.settings.data["devices"]

        takeout = json.dumps({"locations": [{"latitudeE7": 370000000 + i * 1000, "longitudeE7": -1000000000, "timestamp": 1700000000 + i * 60} for i in range(50)]})
        fd = aiohttp.FormData(); fd.add_field("device", "person.mj"); fd.add_field("file", takeout.encode(), filename="Records.json")
        r = await admin.post("/api/import", data=fd); jid = (await r.json())["job"]
        assert await wait_for(lambda: ctx.jobs[jid]["status"] != "running")
        assert ctx.jobs[jid]["status"] == "done" and ctx.jobs[jid]["result"]["read"] == 50
        assert ctx.jobs[jid]["result"]["inserted"] >= 40          # thinned, but moving points kept
        # bad device is rejected
        fd = aiohttp.FormData(); fd.add_field("device", "person.nope"); fd.add_field("file", b"[]", filename="x.json")
        assert (await admin.post("/api/import", data=fd)).status == 400

        # ---- backfill from HA history
        r = await admin.post("/api/backfill", json={"entities": ["person.jon"], "days": 2}); jid = (await r.json())["job"]
        assert await wait_for(lambda: ctx.jobs[jid]["status"] != "running", 20)
        assert ctx.jobs[jid]["status"] == "done", ctx.jobs[jid]
        assert ctx.jobs[jid]["result"]["inserted"] > 0
        assert (await admin.post("/api/backfill", json={"entities": ["person.ghost"], "days": 2})).status == 400

        # ---- exports
        for fmt, needle in (("gpx", "<trkpt"), ("csv", "timestamp,iso_time"), ("json", '"lat"')):
            r = await admin.get(f"/api/export?dev=ext.old_phone&fmt={fmt}")
            assert r.status == 200 and needle in await r.text()

        # ---- icons + traversal protection
        ic = await (await admin.get("/api/icons")).json()
        assert ic["icons"] == ["garage"] and ic["exists"]
        r = await admin.get("/api/icon/garage"); assert r.status == 200 and "sandbox" in r.headers["Content-Security-Policy"]
        for evil in ("../../secret", "..%2f..%2fsecret", "rooms/../../secret", "nope"):
            assert (await admin.get(f"/api/icon/{evil}")).status == 404, evil
        assert (await admin.get("/api/picture/..%2fsettings.json")).status == 404

        # picture upload accepts real image bytes only
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
        fd = aiohttp.FormData(); fd.add_field("file", png, filename="a.png")
        up = await (await admin.post("/api/picture", data=fd)).json()
        assert (await admin.get(f"/api/picture/{up['file']}")).status == 200
        fd = aiohttp.FormData(); fd.add_field("file", b"<svg onload=alert(1)>", filename="a.png")
        assert (await admin.post("/api/picture", data=fd)).status == 400

        # ---- settings: validation + stats + purge + delete device
        r = await admin.post("/api/settings", json={"devices": {"person.mj": {"name": "MJ!", "color": "#ff00ff", "enabled": False}}})
        assert (await r.json())["devices"]["person.mj"]["enabled"] is False
        st = await (await admin.get("/api/state")).json()
        assert "person.mj" not in {d["id"] for d in st["devices"]}
        assert (await admin.get(f"/api/history?dev=person.mj&start=0&end={now}")).status == 200
        assert (await (await admin.get(f"/api/history?dev=person.mj&start=0&end={now}")).json())["devices"] == {}
        s = await (await admin.get("/api/stats")).json()
        assert s["points"] > 100 and s["db_bytes"] > 0
        r = await admin.post("/api/purge", json={"dev": "ext.old_phone", "all": True}); assert (await r.json())["removed"] == 30
        r = await admin.delete("/api/devices/ext.bike?purge=1"); assert (await r.json())["removed_points"] == 1
        assert "ext.bike" not in ctx.settings.data["devices"]

        # ---- public server can be toggled at runtime
        await admin.post("/api/settings", json={"public": {"enabled": False}})
        assert pub.running is False
    finally:
        await admin.close(); await public.close()
        ha_task.cancel()
        await asyncio.gather(ha_task, return_exceptions=True)
        await ctx.close(); await fake.stop()


def test_end_to_end(tmp_path):
    run(scenario(str(tmp_path)))


def test_admin_guard_blocks_non_ingress(monkeypatch):
    from unittest import mock
    from aiohttp.test_utils import make_mocked_request
    import web as w
    monkeypatch.delenv("MT_DEV", raising=False)
    transport = mock.Mock(); transport.get_extra_info.side_effect = lambda k, d=None: ("192.168.1.50", 1234) if k == "peername" else d
    req = make_mocked_request("GET", "/api/settings", transport=transport)
    async def ok(r): return w.web.Response(text="ok")
    assert run(w.admin_guard(req, ok)).status == 403
    transport.get_extra_info.side_effect = lambda k, d=None: ("172.30.32.2", 1234) if k == "peername" else d
    req = make_mocked_request("GET", "/api/settings", transport=transport)
    assert run(w.admin_guard(req, ok)).status == 200
