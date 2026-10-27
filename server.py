import json, os, re, secrets, sqlite3, threading, time
from base64 import b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", 8090))
DB_PATH = os.environ.get("DB_PATH", "/data/tracks.db")
PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TILE_URL = os.environ.get("TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
HERE = os.path.dirname(os.path.abspath(__file__))

lock = threading.Lock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL")
db.executescript("""
CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL, token TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS points(device_id INTEGER NOT NULL, ts INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, acc INTEGER, PRIMARY KEY(device_id, ts));
""")


def q(sql, args=()):
    with lock:
        return db.execute(sql, args).fetchall()


def w(sql, args=()):
    with lock:
        db.execute(sql, args)
        db.commit()


def clean_color(c):
    return c if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) else "#3b4cca"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authed(self):
        if not PASSWORD:
            return True
        try:
            h = self.headers.get("Authorization", "")
            ok = h.startswith("Basic ") and secrets.compare_digest(
                b64decode(h[6:]).decode().split(":", 1)[1], PASSWORD)
        except Exception:
            ok = False
        if not ok:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="tracker"')
            self.send_header("Content-Length", "0")
            self.end_headers()
        return ok

    def ingest(self, token, m):
        rows = q("SELECT id FROM devices WHERE token=?", (token,))
        if not rows:
            return self.send(401, {"error": "unknown token"})
        if isinstance(m, dict) and m.get("_type") == "location":
            try:
                ts, lat, lon = int(m.get("tst") or time.time()), float(m["lat"]), float(m["lon"])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError
                w("INSERT OR IGNORE INTO points VALUES(?,?,?,?,?)",
                  (rows[0][0], ts, lat, lon, int(m.get("acc") or 0)))
            except (KeyError, ValueError, TypeError):
                return self.send(400, {"error": "bad location"})
        self.send(200, [])  # OwnTracks HTTP mode expects a JSON array back

    def do_POST(self):
        u = urlparse(self.path)
        raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1000000))
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self.send(400, {"error": "bad json"})
        if u.path == "/api/v1/ingest/owntracks":
            return self.ingest(parse_qs(u.query).get("token", [""])[0], body)
        if not self.authed():
            return
        name = str(body.get("name", "")).strip()[:40]
        if u.path == "/api/devices" and name:
            w("INSERT INTO devices(name,color,token) VALUES(?,?,?)",
              (name, clean_color(body.get("color")), secrets.token_urlsafe(18)))
            return self.send(200, {"ok": True})
        if u.path == "/api/devices/update" and name:
            w("UPDATE devices SET name=?, color=? WHERE id=?",
              (name, clean_color(body.get("color")), int(body.get("id", 0))))
            return self.send(200, {"ok": True})
        if u.path == "/api/devices/delete":
            did = int(body.get("id", 0))
            w("DELETE FROM points WHERE device_id=?", (did,))
            w("DELETE FROM devices WHERE id=?", (did,))
            return self.send(200, {"ok": True})
        self.send(400, {"error": "bad request"})

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if not self.authed():
            return
        if u.path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/api/config":
            return self.send(200, {"tile_url": TILE_URL})
        if u.path == "/api/devices":
            out = []
            for id_, name, color, token in q("SELECT id,name,color,token FROM devices ORDER BY id"):
                last = q("SELECT ts,lat,lon,acc FROM points WHERE device_id=? ORDER BY ts DESC LIMIT 1", (id_,))
                n = q("SELECT COUNT(*) FROM points WHERE device_id=?", (id_,))[0][0]
                out.append({"id": id_, "name": name, "color": color, "token": token,
                            "last": list(last[0]) if last else None, "count": n})
            return self.send(200, out)
        if u.path == "/api/track":
            try:
                dev, a, b = int(qs["device"][0]), int(qs["from"][0]), int(qs["to"][0])
            except (KeyError, ValueError):
                return self.send(400, {"error": "need device, from, to"})
            rows = q("SELECT ts,lat,lon,acc FROM points WHERE device_id=? AND ts BETWEEN ? AND ? ORDER BY ts",
                     (dev, a, b))
            return self.send(200, [list(r) for r in rows])
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"Mini Tracker listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
