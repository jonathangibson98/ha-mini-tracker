"""Run the real app against the fake HA with seeded data (used for the UI smoke test and local demos)."""
import asyncio, os, sys, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mini_tracker", "app"))
sys.path.insert(0, os.path.dirname(__file__))
os.environ["MT_DEV"] = "1"
from aiohttp import web
from ctx import Ctx
from web import make_admin_app, PublicServer
from fake_ha import FakeHA, person, iso

async def main(admin_port, public_port):
    tmp = tempfile.mkdtemp()
    cfg = os.path.join(tmp, "cfg"); os.makedirs(os.path.join(cfg, "custom_icons"))
    open(os.path.join(cfg, "custom_icons", "garage.svg"), "w").write('<svg viewBox="0 0 24 24"><path d="M2 8l10-6 10 6v14H2z" fill="#f00"/></svg>')
    now = int(time.time()); HOME=(40.0,-75.0); WORK=(40.05,-75.0)
    fake = FakeHA({
        "person.jon": person("person.jon", "Jon", HOME[0], HOME[1], now, entity_picture="/api/image/serve/abc/512x512"),
        "person.mj": person("person.mj", "MJ", WORK[0], WORK[1], now, icon="mdi:account-heart"),
        "zone.home": {"entity_id":"zone.home","state":"0","last_updated":iso(now),"last_changed":iso(now),
                      "attributes":{"friendly_name":"Home","latitude":HOME[0],"longitude":HOME[1],"radius":120,"icon":"mdi:home"}},
        "sun.sun": {"entity_id":"sun.sun","state":"below_horizon","last_updated":iso(now),"last_changed":iso(now),"attributes":{}}})
    await fake.start()
    ctx = Ctx(os.path.join(tmp, "data"), cfg, f"http://127.0.0.1:{fake.port}/core", "t", "http://127.0.0.1:1")
    # seed a day of history: home -> drive -> work
    base = now - 8*3600; rows=[]
    for i in range(12): rows.append(("person.jon", base+i*300, HOME[0]+1e-5*i, HOME[1], 5, 80, "seed"))
    t=base+12*300
    for i in range(1,21): rows.append(("person.jon", t+i*60, HOME[0]+(WORK[0]-HOME[0])*i/20, HOME[1]+0.001*(i%3), 5, 80, "seed"))
    t+=20*60
    for i in range(1,15): rows.append(("person.jon", t+i*300, WORK[0], WORK[1], 5, 80, "seed"))
    ctx.store.insert_many(rows)
    ctx.settings.patch({"devices": {"person.jon": {"name":"Jon"}, "person.mj": {"name":"MJ"}}})
    await ctx.recorder.warm_up()
    asyncio.create_task(ctx.ha.run()); asyncio.create_task(ctx.first_run())
    pub = PublicServer(ctx, public_port)
    runner = web.AppRunner(make_admin_app(ctx, pub)); await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", admin_port).start()
    await pub.apply()
    print("READY", flush=True)
    await asyncio.Event().wait()

asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
