"""SQLite storage for location history. Thread-safe via one lock + one connection."""
from __future__ import annotations

import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS points (
    dev  TEXT    NOT NULL,
    ts   INTEGER NOT NULL,
    lat  REAL    NOT NULL,
    lon  REAL    NOT NULL,
    acc  REAL,
    batt REAL,
    src  TEXT,
    PRIMARY KEY (dev, ts)
) WITHOUT ROWID;
"""


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)

    # ----------------------------------------------------------- writes
    def insert(self, dev: str, ts: int, lat: float, lon: float, acc=None, batt=None,
               src: str = "ha") -> bool:
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO points (dev, ts, lat, lon, acc, batt, src) VALUES (?,?,?,?,?,?,?)",
                (dev, int(ts), float(lat), float(lon), acc, batt, src),
            )
            return cur.rowcount > 0

    def insert_many(self, rows: list[tuple]) -> int:
        """rows: (dev, ts, lat, lon, acc, batt, src). Returns number actually inserted."""
        if not rows:
            return 0
        with self._lock:
            before = self._db.total_changes
            self._db.execute("BEGIN")
            try:
                self._db.executemany(
                    "INSERT OR IGNORE INTO points (dev, ts, lat, lon, acc, batt, src) VALUES (?,?,?,?,?,?,?)",
                    rows,
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            return self._db.total_changes - before

    def delete_device(self, dev: str) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM points WHERE dev = ?", (dev,))
            return cur.rowcount

    def purge_before(self, ts: int, dev: str | None = None) -> int:
        with self._lock:
            if dev:
                cur = self._db.execute("DELETE FROM points WHERE ts < ? AND dev = ?", (int(ts), dev))
            else:
                cur = self._db.execute("DELETE FROM points WHERE ts < ?", (int(ts),))
            return cur.rowcount

    def vacuum(self) -> None:
        with self._lock:
            self._db.execute("VACUUM")

    # ------------------------------------------------------------ reads
    def range(self, dev: str, start: int, end: int) -> list[tuple]:
        """Points as (ts, lat, lon, acc, batt) ordered by time."""
        with self._lock:
            cur = self._db.execute(
                "SELECT ts, lat, lon, acc, batt FROM points WHERE dev = ? AND ts >= ? AND ts <= ? ORDER BY ts",
                (dev, int(start), int(end)),
            )
            return cur.fetchall()

    def last(self, dev: str) -> tuple | None:
        with self._lock:
            cur = self._db.execute(
                "SELECT ts, lat, lon, acc, batt FROM points WHERE dev = ? ORDER BY ts DESC LIMIT 1",
                (dev,),
            )
            return cur.fetchone()

    def devices(self) -> list[dict]:
        with self._lock:
            cur = self._db.execute(
                "SELECT dev, COUNT(*), MIN(ts), MAX(ts) FROM points GROUP BY dev ORDER BY dev"
            )
            return [{"dev": d, "count": c, "first": a, "last": b} for d, c, a, b in cur.fetchall()]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM points").fetchone()[0]

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        return total

    def checkpoint(self) -> None:
        with self._lock:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            try:
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._db.close()
