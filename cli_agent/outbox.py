"""Persistent outbox: signed records not yet acknowledged by LTMS.

Stored in SQLite so they survive a reboot. One file may hold several agents'
records, so the Jetson hub can reuse it as its upload queue. No signature
checks here -- this is only a store.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS outbox (
  agent TEXT NOT NULL,
  seq INTEGER NOT NULL,
  hash TEXT NOT NULL,
  record TEXT NOT NULL,
  PRIMARY KEY (agent, seq)
)
"""


class Outbox:
    """Signed records not yet acknowledged by LTMS. Survives restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(CREATE_TABLE)
        self.conn.commit()

    def add(self, record: dict) -> bool:
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        agent = record.get("agent")
        seq = record.get("seq")
        hash_ = record.get("hash")
        if not isinstance(agent, str):
            raise ValueError("record['agent'] must be a str")
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise ValueError("record['seq'] must be an int (not bool)")
        if not isinstance(hash_, str):
            raise ValueError("record['hash'] must be a str")

        row = self.conn.execute(
            "SELECT hash FROM outbox WHERE agent = ? AND seq = ?", (agent, seq)
        ).fetchone()
        if row is not None:
            if row[0] == hash_:
                return False
            raise ValueError(
                f"conflict: agent {agent!r} seq {seq} already stored with a different hash"
            )

        self.conn.execute(
            "INSERT INTO outbox (agent, seq, hash, record) VALUES (?, ?, ?, ?)",
            (agent, seq, hash_, json.dumps(record)),
        )
        self.conn.commit()
        return True

    def pending(self, agent: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT record FROM outbox WHERE agent = ? ORDER BY seq ASC LIMIT ?",
            (agent, limit),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def agents(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT agent FROM outbox ORDER BY agent ASC"
        ).fetchall()
        return [row[0] for row in rows]

    def prune(self, agent: str, acked_seq: int) -> int:
        cur = self.conn.execute(
            "DELETE FROM outbox WHERE agent = ? AND seq <= ?", (agent, acked_seq)
        )
        self.conn.commit()
        return cur.rowcount

    def last(self, agent: str) -> dict | None:
        row = self.conn.execute(
            "SELECT record FROM outbox WHERE agent = ? ORDER BY seq DESC LIMIT 1",
            (agent,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def close(self) -> None:
        self.conn.close()
