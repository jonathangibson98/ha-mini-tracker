"""A tiny fake Home Assistant Core (websocket + REST) for tests."""
import asyncio, json
from datetime import datetime, timezone
from aiohttp import web, WSMsgType


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def person(eid, name, lat, lon, ts, state="home", acc=10, batt=80):
    return {"entity_id": eid, "state": state, "last_updated": iso(ts), "last_changed": iso(ts),
            "attributes": {"friendly_name": name, "latitude": lat, "longitude": lon, "gps_accuracy": acc,
                           "battery_level": batt}}


class FakeHA:
    def __init__(self, states, history=None):
        self.states = states
        self.history = history or {}
        self.sockets = []
        self.app = web.Application()
        self.app.router.add_get("/core/websocket", self.ws)
        self.app.router.add_get("/core/api/history/period/{start}", self.hist)
        self.runner = None
        self.port = None

    async def ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        msg = await ws.receive_json()
        if msg.get("access_token") != "t":
            await ws.send_json({"type": "auth_invalid"}); return ws
        await ws.send_json({"type": "auth_ok"})
        self.sockets.append(ws)
        async for m in ws:
            if m.type != WSMsgType.TEXT: continue
            d = json.loads(m.data)
            t = d["type"]
            if t == "get_config":
                await ws.send_json({"id": d["id"], "type": "result", "success": True, "result": {"time_zone": "America/New_York"}})
            elif t == "get_states":
                await ws.send_json({"id": d["id"], "type": "result", "success": True, "result": list(self.states.values())})
            elif t == "subscribe_events":
                await ws.send_json({"id": d["id"], "type": "result", "success": True, "result": None})
        return ws

    async def push(self, state):
        self.states[state["entity_id"]] = state
        for ws in self.sockets:
            await ws.send_json({"id": 3, "type": "event", "event": {"event_type": "state_changed",
                                "data": {"entity_id": state["entity_id"], "new_state": state}}})

    async def hist(self, request):
        ids = request.query.get("filter_entity_id", "").split(",")
        return web.json_response([self.history.get(i, []) for i in ids])

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        await self.runner.cleanup()
