"""SQLite storage for jobs (so transfers can resume), push offers and history."""

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL);
CREATE TABLE IF NOT EXISTS job_files(
    job_id TEXT, idx INTEGER, src TEXT, rel TEXT, size INTEGER, mtime REAL,
    action TEXT, status INTEGER, chunks TEXT, final_rel TEXT,
    PRIMARY KEY(job_id, idx));
CREATE TABLE IF NOT EXISTS history(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, direction TEXT, peer TEXT, label TEXT,
    files INTEGER, bytes INTEGER, duration REAL, status TEXT, dest TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, token TEXT, items TEXT, created REAL);
"""


class DB:
    def __init__(self, path: Path):
        self._lock = threading.RLock()
        self.c = sqlite3.connect(str(path), check_same_thread=False)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA synchronous=NORMAL")
        self.c.executescript(SCHEMA)
        self.c.commit()

    # ---- jobs ----
    def save_job(self, meta: dict):
        with self._lock:
            self.c.execute("INSERT OR REPLACE INTO jobs(id, data, created) VALUES(?,?,?)",
                           (meta["id"], json.dumps(meta), meta.get("created", time.time())))
            self.c.commit()

    def insert_files(self, job_id: str, rows: list[tuple]):
        """rows: (idx, src, rel, size, mtime, action, status, chunks, final_rel)"""
        with self._lock:
            self.c.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))
            self.c.executemany(
                "INSERT INTO job_files VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(job_id, *r) for r in rows])
            self.c.commit()

    def update_files(self, job_id: str, rows: list[tuple]):
        """rows: (action, status, chunks, final_rel, idx)"""
        if not rows:
            return
        with self._lock:
            self.c.executemany(
                "UPDATE job_files SET action=?, status=?, chunks=?, final_rel=? WHERE job_id=? AND idx=?",
                [(a, s, c, f, job_id, i) for (a, s, c, f, i) in rows])
            self.c.commit()

    def load_jobs(self) -> list[dict]:
        with self._lock:
            return [json.loads(r[0]) for r in self.c.execute("SELECT data FROM jobs ORDER BY created")]

    def load_files(self, job_id: str) -> list[tuple]:
        with self._lock:
            return list(self.c.execute(
                "SELECT idx, src, rel, size, mtime, action, status, chunks, final_rel "
                "FROM job_files WHERE job_id=? ORDER BY idx", (job_id,)))

    def delete_job(self, job_id: str):
        with self._lock:
            self.c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self.c.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))
            self.c.commit()

    # ---- offers (files this PC pushes to another) ----
    def save_offer(self, oid: str, token: str, items: dict):
        with self._lock:
            self.c.execute("INSERT OR REPLACE INTO offers VALUES(?,?,?,?)",
                           (oid, token, json.dumps(items), time.time()))
            self.c.commit()

    def offer_by_token(self, token: str):
        with self._lock:
            r = self.c.execute("SELECT id, items FROM offers WHERE token=?", (token,)).fetchone()
        return (r[0], json.loads(r[1])) if r else None

    def delete_offer(self, oid: str):
        with self._lock:
            self.c.execute("DELETE FROM offers WHERE id=?", (oid,))
            self.c.commit()

    # ---- history ----
    def add_history(self, **h):
        with self._lock:
            self.c.execute(
                "INSERT INTO history(ts, direction, peer, label, files, bytes, duration, status, dest, error)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (time.time(), h.get("direction"), h.get("peer"), h.get("label"), h.get("files", 0),
                 h.get("bytes", 0), h.get("duration", 0), h.get("status"), h.get("dest", ""), h.get("error", "")))
            self.c.commit()

    def list_history(self, limit=500) -> list[dict]:
        with self._lock:
            cur = self.c.execute("SELECT * FROM history ORDER BY ts DESC LIMIT ?", (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur]

    def clear_history(self):
        with self._lock:
            self.c.execute("DELETE FROM history")
            self.c.commit()
