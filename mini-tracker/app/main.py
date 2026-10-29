"""Mini Tracker entry point."""
from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiohttp import web

from ctx import VERSION, Ctx
from web import PublicServer, make_admin_app


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


async def main() -> None:
    level = env("MT_LOG_LEVEL", "info").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("mini_tracker")
    log.info("Mini Tracker %s starting", VERSION)

    ctx = Ctx(
        data_dir=env("MT_DATA", "/data"),
        ha_config_dir=env("MT_HA_CONFIG", "/homeassistant"),
        ha_base=env("MT_HA_BASE", "http://supervisor/core"),
        token=env("SUPERVISOR_TOKEN", env("MT_TOKEN", "")),
        supervisor_url=env("MT_SUPERVISOR", "http://supervisor"),
    )
    await ctx.recorder.warm_up()

    public = PublicServer(ctx, int(env("MT_PUBLIC_PORT", "8100")))
    runner = web.AppRunner(make_admin_app(ctx, public), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(env("MT_ADMIN_PORT", "8099"))).start()
    await public.apply()
    log.info("Admin UI (ingress) on :%s", env("MT_ADMIN_PORT", "8099"))

    tasks = [asyncio.create_task(ctx.ha.run()), asyncio.create_task(ctx.retention_loop()),
             asyncio.create_task(ctx.first_run())]
    await ctx.configure_mqtt()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()

    log.info("Shutting down")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await public.stop()
    await runner.cleanup()
    await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
