"""Transactional SQLite storage with ordered schema migrations."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .models import Alarm, Occurrence

SCHEMA_VERSION = 2

class Repository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self._transaction_depth = 0
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        try:
            self._migrate()
        except Exception:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        """Commit related repository operations together, including nested calls."""
        outer = self._transaction_depth == 0
        self._transaction_depth += 1
        try:
            yield
            if outer:
                self.db.commit()
        except Exception:
            if outer:
                self.db.rollback()
            raise
        finally:
            self._transaction_depth -= 1

    def _migrate(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("This database was created by a newer version of Custom Alarm Clock.")
        with self.transaction():
            if version < 1:
                self.db.execute("CREATE TABLE alarms (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
                self.db.execute("CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                self.db.execute("CREATE TABLE occurrences (id TEXT PRIMARY KEY, alarm_id TEXT NOT NULL, occurrence_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, data TEXT NOT NULL)")
                self.db.execute("PRAGMA user_version=1")
            if version < 2:
                self.db.execute("CREATE INDEX IF NOT EXISTS occurrence_status_idx ON occurrences(status)")
                self.db.execute("CREATE INDEX IF NOT EXISTS occurrence_alarm_idx ON occurrences(alarm_id)")
                self.db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, message TEXT NOT NULL)")
                self.db.execute("PRAGMA user_version=2")

    def save_alarm(self, alarm: Alarm):
        alarm.validate()
        with self.transaction():
            self.db.execute("INSERT INTO alarms VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (alarm.id, json.dumps(alarm.to_dict())))

    def alarms(self) -> list[Alarm]:
        return [Alarm.from_dict(json.loads(r[0])) for r in self.db.execute("SELECT data FROM alarms ORDER BY rowid")]

    def alarm(self, alarm_id: str) -> Alarm | None:
        row = self.db.execute("SELECT data FROM alarms WHERE id=?", (alarm_id,)).fetchone()
        return Alarm.from_dict(json.loads(row[0])) if row else None

    def delete_alarm(self, alarm_id: str):
        with self.transaction():
            self.db.execute("DELETE FROM alarms WHERE id=?", (alarm_id,))

    def claim_occurrence(self, occurrence: Occurrence) -> bool:
        with self.transaction():
            result = self.db.execute("INSERT OR IGNORE INTO occurrences VALUES (?,?,?,?,?)", (occurrence.id, occurrence.alarm_id, occurrence.key, occurrence.status, json.dumps(occurrence.to_dict())))
        return result.rowcount == 1

    def save_occurrence(self, occurrence: Occurrence):
        with self.transaction():
            self.db.execute("UPDATE occurrences SET status=?, data=? WHERE id=?", (occurrence.status, json.dumps(occurrence.to_dict()), occurrence.id))

    def occurrence(self, occurrence_id: str) -> Occurrence | None:
        row = self.db.execute("SELECT data FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
        return Occurrence.from_dict(json.loads(row[0])) if row else None

    def active(self) -> list[Occurrence]:
        return [Occurrence.from_dict(json.loads(r[0])) for r in self.db.execute("SELECT data FROM occurrences WHERE status IN ('active','snoozed') ORDER BY rowid")]

    def history(self, limit=100) -> list[Occurrence]:
        return [Occurrence.from_dict(json.loads(r[0])) for r in self.db.execute("SELECT data FROM occurrences ORDER BY rowid DESC LIMIT ?", (limit,))]

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.transaction():
            self.db.execute("INSERT INTO preferences VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE preferences.value != excluded.value", (key, json.dumps(value)))

    def unset(self, key):
        with self.transaction():
            self.db.execute("DELETE FROM preferences WHERE key=?", (key,))

    def log(self, at: float, message: str):
        with self.transaction():
            self.db.execute("INSERT INTO events(at,message) VALUES (?,?)", (at, message))
            self.db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)")

    def events(self):
        return list(self.db.execute("SELECT at,message FROM events ORDER BY id DESC LIMIT 200"))

    def close(self):
        self.db.close()
