"""A local handoff: harnesses on a device record *as that device* without its key (#521).

The device is the member; the harnesses on it (Hermes, Qwen Code, OpenCode, a
coding agent Hermes drives) are not. Only one process may sign for a key, or
the chain forks, so a harness never signs: it **submits** an unsigned record to
this queue, and the member process that holds the key **pumps** the queue,
signing each record into the device's one chain.

It is one SQLite file in the member's state folder (``MEMBER_HANDOFF_PATH``).
Anything that can write that file can record as the device, so keep it
readable and writable by the device's own user only (the file is created
``0600``).

Harness side::

    from cli_agent.handoff import Handoff
    Handoff(path).submit("action", summary="ran pytest", payload={...},
                         tags=["tool"], source="hermes", ts=when_it_happened)

Member side (``member_main`` does this each step when the setting is on)::

    handoff.pump(link)

``submit`` checks the record against the contract first, so a harness hears
about a bad record at once. A record the member still can't sign is moved to
``refused`` with the reason, so one bad record never blocks the queue.

If the member process crashes between signing a record and removing it here,
that record is signed again on restart: a duplicate, never a loss.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any

from ltms_contract.record import (
    CONTRACT_VERSION,
    GENESIS_HASH,
    ContractError,
    now_ts,
    payload_bytes,
    validate_header,
)

log = logging.getLogger(__name__)

HEADER_FIELDS = ("summary", "place", "peers", "tags", "refs", "outcome", "sw")
KEEP_REFUSED = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL NOT NULL,
    source TEXT,
    type TEXT NOT NULL,
    ts TEXT NOT NULL,
    header TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refused (
    id INTEGER PRIMARY KEY,
    created REAL NOT NULL,
    refused_at REAL NOT NULL,
    source TEXT,
    type TEXT NOT NULL,
    header TEXT NOT NULL,
    error TEXT NOT NULL
);
"""


class Handoff:
    """The queue between a device's harnesses and its member process."""

    def __init__(self, path: str | os.PathLike, *, timeout: float = 5.0) -> None:
        self.path = os.fspath(path)
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        new = not os.path.exists(self.path)
        if new:  # created owner-only: whoever can write this file records as the device
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        self._db = sqlite3.connect(self.path, timeout=timeout, isolation_level=None,
                                   check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------ harness side
    def submit(self, type: str, *, summary: str | None = None, payload: dict | None = None,
               ts: str | None = None, source: str | None = None, **header: Any) -> int:
        """Queue one record for the member to sign. Returns its queue id.

        ``header`` may hold ``place``, ``peers``, ``tags``, ``refs``, ``outcome`` and
        ``sw``. ``ts`` is when it happened (RFC 3339, default now). ``source`` names
        the harness (kept here for the refused list; not part of the record).
        Raises ``ContractError`` if the record could never be signed.
        """
        unknown = sorted(set(header) - set(HEADER_FIELDS))
        if unknown:
            raise ContractError("bad_header", f"unknown header fields: {', '.join(unknown)}")
        head = {k: v for k, v in header.items() if v is not None}
        if summary is not None:
            head["summary"] = summary
        ts = ts or now_ts()
        body = payload or {}
        # The same checks the member will run, with a placeholder agent and chain position.
        validate_header({"v": CONTRACT_VERSION, "agent": "handoff", "seq": 2, "ts": ts, "type": type,
                         "prev_hash": GENESIS_HASH, "payload_hash": "0" * 64, **head})
        payload_bytes(body)
        cur = self._db.execute(
            "INSERT INTO pending (created, source, type, ts, header, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), (source or None) and str(source)[:64], type, ts,
             json.dumps(head, sort_keys=True), json.dumps(body, sort_keys=True)))
        return int(cur.lastrowid)

    # ------------------------------------------------------------------ member side
    def pump(self, link, limit: int = 50) -> int:
        """Sign up to ``limit`` queued records into ``link``'s chain, oldest first.
        Returns how many were signed. Main thread only (``MemberLink`` is)."""
        rows = self._db.execute(
            "SELECT id, created, source, type, ts, header, payload FROM pending ORDER BY id LIMIT ?",
            (limit,)).fetchall()
        signed = 0
        for rid, created, source, rtype, ts, header, payload in rows:
            head = json.loads(header)
            try:
                link.append(rtype, payload=json.loads(payload), ts=ts, **head)
            except (ContractError, ValueError, TypeError) as exc:
                self._refuse(rid, created, source, rtype, header, f"{type(exc).__name__}: {exc}")
                continue
            # A crash right here signs it again on restart: a duplicate, never a loss.
            self._db.execute("DELETE FROM pending WHERE id = ?", (rid,))
            signed += 1
        return signed

    def _refuse(self, rid, created, source, rtype, header, error: str) -> None:
        log.warning("handoff: refused %s from %s: %s", rtype, source or "?", error[:200])
        self._db.execute("BEGIN")
        try:
            self._db.execute(
                "INSERT INTO refused (id, created, refused_at, source, type, header, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (rid, created, time.time(), source, rtype, header, error[:500]))
            self._db.execute("DELETE FROM pending WHERE id = ?", (rid,))
            self._db.execute("DELETE FROM refused WHERE id NOT IN "
                             "(SELECT id FROM refused ORDER BY id DESC LIMIT ?)", (KEEP_REFUSED,))
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------ status
    def pending(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM pending").fetchone()[0])

    def refused(self, limit: int = 20) -> list[dict]:
        rows = self._db.execute("SELECT id, source, type, error, refused_at FROM refused "
                                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "source": r[1], "type": r[2], "error": r[3], "refused_at": r[4]} for r in rows]
