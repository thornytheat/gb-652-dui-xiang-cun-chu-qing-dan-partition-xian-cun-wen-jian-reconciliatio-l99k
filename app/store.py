from __future__ import annotations
import json
from pathlib import Path
import sqlite3

class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (digest TEXT PRIMARY KEY, run_id TEXT NOT NULL, result TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS partition_batches ("
                "batch_id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL, "
                "replays INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS partition_batches_digest ON partition_batches (digest)"
            )

    def connect(self):
        return sqlite3.connect(self.path)

    def record(self, digest: str, run_id: str, result: dict) -> dict:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                       (digest, run_id, json.dumps(result, ensure_ascii=False, sort_keys=True)))
            row = db.execute("SELECT run_id, result FROM runs WHERE digest=?", (digest,)).fetchone()
        return {"run_id": row[0], "result": json.loads(row[1])}

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def all(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT run_id, digest, result FROM runs ORDER BY run_id").fetchall()
        return [{"run_id": r[0], "digest": r[1], "result": json.loads(r[2])} for r in rows]

    # ------------------------------------------------------------------
    # 分区清单批次
    # ------------------------------------------------------------------

    def save_batch(self, batch_id: str, digest: str, state: dict) -> dict:
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
        with self.connect() as db:
            db.execute(
                "INSERT INTO partition_batches (batch_id, digest, state, replays) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(batch_id) DO UPDATE SET state=excluded.state, replays=excluded.replays, "
                "updated_at=CURRENT_TIMESTAMP",
                (batch_id, digest, payload, int(state.get("replays", 0))),
            )
        return state

    def load_batch(self, batch_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT state FROM partition_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def find_batch_by_digest(self, digest: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT state FROM partition_batches WHERE digest=? "
                "ORDER BY updated_at DESC, rowid DESC LIMIT 1",
                (digest,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def latest_batch(self) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT state FROM partition_batches "
                "ORDER BY updated_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def batch_count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM partition_batches").fetchone()[0]
