from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class HistoryDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self):
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                payload TEXT NOT NULL DEFAULT '{}'
            )""")

    def create_run(self, kind: str, payload: dict[str, Any]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute("INSERT INTO runs(kind, status, created_at, payload) VALUES(?, ?, ?, ?)",
                                (kind, "running", now, json.dumps(payload)))
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, payload: dict[str, Any]):
        with self._connect() as db:
            db.execute("UPDATE runs SET status=?, finished_at=?, payload=? WHERE id=?",
                       (status, datetime.now(timezone.utc).isoformat(), json.dumps(payload), run_id))

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"] or "{}")} for row in rows]

