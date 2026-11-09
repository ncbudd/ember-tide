"""SQLite storage. One file, no server: the default for most installs. Thread-safe via a lock."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from collections.abc import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
  person TEXT, source TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS telemetry (
  ts REAL NOT NULL, zone TEXT NOT NULL, on_ INTEGER, setpoint REAL, measured REAL,
  occupied INTEGER, hr INTEGER, br INTEGER, status INTEGER, target REAL, phase TEXT
);
CREATE INDEX IF NOT EXISTS telemetry_ts ON telemetry(ts);
CREATE TABLE IF NOT EXISTS samples (ts REAL NOT NULL, source TEXT NOT NULL, value REAL);
CREATE INDEX IF NOT EXISTS samples_src_ts ON samples(source, ts);
CREATE TABLE IF NOT EXISTS stages (person TEXT NOT NULL, source TEXT NOT NULL, label TEXT, start REAL NOT NULL, end_ REAL NOT NULL, stage TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS stages_p_ts ON stages(person, start);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        for col in ("bed_occ INTEGER", "person TEXT"):
            try:
                self._db.execute(f"ALTER TABLE telemetry ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        self._lock = threading.Lock()
        self._sources: set[str] | None = None      # distinct sample source names, loaded once and kept up to date

    # ---- key/value documents ----
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    # ---- events ----
    def event(self, ts: float, kind: str, person: str | None = None, source: str | None = None, **data: Any) -> dict:
        with self._lock, self._db:
            self._db.execute("INSERT INTO events(ts,kind,person,source,data) VALUES(?,?,?,?,?)", (ts, kind, person, source, json.dumps(data)))
        return {"ts": ts, "kind": kind, "person": person, "source": source, **data}

    def events(self, since: float = 0, limit: int = 200, person: str | None = None) -> list[dict]:
        q = "SELECT ts,kind,person,source,data FROM events WHERE ts>=?"
        args: list[Any] = [since]
        if person:
            q += " AND (person=? OR person IS NULL)"
            args.append(person)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [{"ts": r[0], "kind": r[1], "person": r[2], "source": r[3], **json.loads(r[4] or "{}")} for r in rows]

    def events_between(self, since: float, until: float, person: str | None = None, limit: int = 5000) -> list[dict]:
        """Events inside [since, until], oldest first (bounded at both ends, unlike events())."""
        q = "SELECT ts,kind,person,source,data FROM events WHERE ts>=? AND ts<=?"
        args: list[Any] = [since, until]
        if person:
            q += " AND (person=? OR person IS NULL)"
            args.append(person)
        q += " ORDER BY ts LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [{"ts": r[0], "kind": r[1], "person": r[2], "source": r[3], **json.loads(r[4] or "{}")} for r in rows]

    # ---- telemetry ----
    def telemetry(self, rows: Iterable[tuple]) -> None:
        with self._lock, self._db:
            self._db.executemany("INSERT INTO telemetry(ts,zone,on_,setpoint,measured,occupied,hr,br,status,target,phase,bed_occ,person) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [tuple(r) + ((None,) if len(r) == 12 else ()) for r in rows])

    def history(self, zone: str, since: float, until: float | None = None, step_s: float = 0, person: str | None = None) -> list[dict]:
        """Rows for one side. With `person`, rows recorded for that person (whatever side they were on) plus old
        rows that predate person tagging and were on `zone`."""
        if person:
            q = "SELECT ts,on_,setpoint,measured,occupied,hr,br,status,target,phase,bed_occ FROM telemetry WHERE (person=? OR (person IS NULL AND zone=?)) AND ts>=?"
            args: list[Any] = [person, zone, since]
        else:
            q = "SELECT ts,on_,setpoint,measured,occupied,hr,br,status,target,phase,bed_occ FROM telemetry WHERE zone=? AND ts>=?"
            args = [zone, since]
        if until is not None:
            q += " AND ts<=?"
            args.append(until)
        q += " ORDER BY ts"
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        out, last = [], -1e18
        keys = ("ts", "on", "setpoint", "measured", "occupied", "hr", "br", "status", "target", "phase", "bed_occ")
        for r in rows:
            if step_s and r[0] - last < step_s:
                continue
            last = r[0]
            out.append(dict(zip(keys, r)))
        return out

    def window_version(self, zone: str, person: str, since: float, until: float) -> tuple:
        """A cheap fingerprint of everything recorded for one person in [since, until]: changes whenever a row or
        event inside the window is added or removed. Used to cache summaries of finished nights."""
        with self._lock:
            t = self._db.execute("SELECT COUNT(*), MAX(ts) FROM telemetry WHERE (person=? OR (person IS NULL AND zone=?)) AND ts>=? AND ts<=?",
                                 (person, zone, since, until)).fetchone()
            e = self._db.execute("SELECT COUNT(*), MAX(ts), MAX(id) FROM events WHERE (person=? OR person IS NULL) AND ts>=? AND ts<=?",
                                 (person, since, until)).fetchone()
        return tuple(t) + tuple(e)

    def sample(self, ts: float, source: str, value: float) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO samples VALUES(?,?,?)", (ts, source, value))
            if self._sources is not None:
                self._sources.add(source)

    def sample_sources(self, prefix: str) -> list[str]:
        """Source names starting with `prefix`. A full DISTINCT scan of a large samples table costs ~0.1 s, and this is
        asked every Autopilot minute, so the set of names is read once and kept up to date on every insert."""
        with self._lock:
            if self._sources is None:
                self._sources = {r[0] for r in self._db.execute("SELECT DISTINCT source FROM samples").fetchall()}
            return sorted(s for s in self._sources if s.startswith(prefix))

    def samples_many(self, rows: list[tuple[float, str, float]]) -> None:
        with self._lock, self._db:
            self._db.executemany("INSERT INTO samples VALUES(?,?,?)", rows)
            if self._sources is not None:
                self._sources.update(r[1] for r in rows)

    def samples(self, source: str, since: float, until: float) -> list[tuple[float, float]]:
        with self._lock:
            return self._db.execute("SELECT ts,value FROM samples WHERE source=? AND ts>=? AND ts<=? ORDER BY ts", (source, since, until)).fetchall()

    def put_stages(self, person: str, source: str, label: str, segs: list[dict], replace_from: float, replace_to: float) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM stages WHERE person=? AND source=? AND start>=? AND start<?", (person, source, replace_from, replace_to))
            self._db.executemany("INSERT INTO stages VALUES(?,?,?,?,?,?)", [(person, source, label, g["start"], g["end"], g["stage"]) for g in segs])

    def append_stage(self, person: str, source: str, label: str, start: float, end: float, stage: str) -> None:
        with self._lock, self._db:
            row = self._db.execute("SELECT rowid,end_,stage FROM stages WHERE person=? AND source=? ORDER BY start DESC LIMIT 1", (person, source)).fetchone()
            if row and row[2] == stage and 0 <= start - row[1] < 120:
                self._db.execute("UPDATE stages SET end_=? WHERE rowid=?", (end, row[0]))
            else:
                self._db.execute("INSERT INTO stages VALUES(?,?,?,?,?,?)", (person, source, label, start, end, stage))

    def get_stages(self, person: str, since: float, until: float) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT source,label,start,end_,stage FROM stages WHERE person=? AND end_>? AND start<? ORDER BY start", (person, since, until)).fetchall()
        out: dict[str, dict] = {}
        for src, label, a, b, st in rows:
            out.setdefault(src, {"source": src, "label": label, "segments": []})["segments"].append({"start": a, "end": b, "stage": st})
        return out

    def drop_after(self, ts: float) -> None:
        """Simulator only: forget rows 'from the future' after jumping back in time."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM telemetry WHERE ts>?", (ts,))
            self._db.execute("DELETE FROM events WHERE ts>?", (ts,))
            self._db.execute("DELETE FROM samples WHERE ts>?", (ts,))
            self._db.execute("DELETE FROM stages WHERE start>?", (ts,))
            self._sources = None

    def compact(self, now: float, samples_after_days: int = 30, telemetry_after_days: int = 60) -> dict:
        """Keep the database from growing without end, without losing what the nights looked like:
        - device readings older than `samples_after_days` become one 5-minute average per device;
        - bed telemetry older than `telemetry_after_days` keeps one row per side per minute.
        Each run only touches what the previous run hadn't (tracked in kv), so it's cheap to run often."""
        s_cut = now - samples_after_days * 86400
        t_cut = now - telemetry_after_days * 86400
        done = self.get("compacted", {}) or {}
        s_from, t_from = float(done.get("samples", 0)), float(done.get("telemetry", 0))
        out = {"samples_before": 0, "samples_after": 0, "telemetry_removed": 0}
        with self._lock, self._db:
            if s_cut > s_from:
                n = self._db.execute("SELECT COUNT(*) FROM samples WHERE ts>=? AND ts<?", (s_from, s_cut)).fetchone()[0]
                if n:
                    rows = self._db.execute(
                        "SELECT source, CAST(ts/300 AS INTEGER) AS b, AVG(ts), AVG(value) FROM samples WHERE ts>=? AND ts<? "
                        "AND value IS NOT NULL GROUP BY source, b", (s_from, s_cut)).fetchall()
                    self._db.execute("DELETE FROM samples WHERE ts>=? AND ts<?", (s_from, s_cut))
                    self._db.executemany("INSERT INTO samples VALUES(?,?,?)", [(t, src, round(v, 3)) for src, _, t, v in rows])
                    out["samples_before"], out["samples_after"] = n, len(rows)
                done["samples"] = s_cut
            if t_cut > t_from:
                cur = self._db.execute(
                    "DELETE FROM telemetry WHERE ts>=? AND ts<? AND rowid NOT IN (SELECT MIN(rowid) FROM telemetry "
                    "WHERE ts>=? AND ts<? GROUP BY zone, COALESCE(person, ''), CAST(ts/60 AS INTEGER))", (t_from, t_cut, t_from, t_cut))
                out["telemetry_removed"] = cur.rowcount
                done["telemetry"] = t_cut
        self.put("compacted", done)
        return out

    # ---- targeted forgetting (Autopilot "Start over: recalibrate") ----
    def delete_events(self, kind: str, person: str) -> int:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM events WHERE kind=? AND person=?", (kind, person)).rowcount

    def delete_samples(self, prefix: str) -> int:
        with self._lock, self._db:
            n = self._db.execute("DELETE FROM samples WHERE substr(source, 1, ?)=?", (len(prefix), prefix)).rowcount
            if self._sources is not None:
                self._sources = {x for x in self._sources if not x.startswith(prefix)}
            return n

    def delete_keys(self, prefix: str) -> int:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM kv WHERE substr(key, 1, ?)=?", (len(prefix), prefix)).rowcount
