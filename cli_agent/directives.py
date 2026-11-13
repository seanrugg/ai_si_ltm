"""Members receive, verify and acknowledge directives (#306).

A directive arrives retained on ``<topic_id>/fleet/<id>``. The member checks the
fleet signature (``ltms-directive-v1\\n`` + the statement) with the fleet's public
key, then records, as itself:

- ``communication`` tagged ``directive-ack``, payload ``{"directive": id, "status": "received"}``;
- then ``accepted``, or ``declined`` with a ``reason``, once its behaviour decides.
  **Directives are followed by default**: the default decision accepts. A decline
  needs a reason (its safety envelope, its permissions, or the charter).

An invalid signature is ignored and recorded once as an ``event`` tagged
``directive-invalid``. Ids already acknowledged are kept in a small file, so a
retained directive delivered again after a reconnect isn't acknowledged twice.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Callable

from nacl.signing import VerifyKey

DOMAIN = "ltms-directive-v1\n"
CANCEL_DOMAIN = "ltms-directive-cancel-v1\n"
ACK_TAG = "directive-ack"
INVALID_TAG = "directive-invalid"


def verify(public_key_hex: str | None, domain: str, statement: str, sig_hex: str) -> bool:
    if not public_key_hex or not isinstance(statement, str) or not isinstance(sig_hex, str):
        return False
    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(domain.encode() + statement.encode(), bytes.fromhex(sig_hex))
        return True
    except Exception:  # noqa: BLE001
        return False


class Decision:
    """What the behaviour layer decided. ``decline`` needs a reason."""

    def __init__(self, accept: bool, reason: str | None = None):
        if not accept and not (isinstance(reason, str) and reason.strip()):
            raise ValueError("declining a directive needs a reason (safety envelope, permissions or the charter)")
        self.accept, self.reason = accept, (reason or None)

    @classmethod
    def accept_(cls) -> "Decision":
        return cls(True)

    @classmethod
    def decline(cls, reason: str) -> "Decision":
        return cls(False, reason)


def follow_by_default(directive: dict) -> Decision:
    return Decision(True)


class DirectiveHandler:
    def __init__(self, link, fleet_key: Callable[[], str | None], state_path: str,
                 decide: Callable[[dict], Decision] = follow_by_default, leader_key: Callable[[], str | None] = lambda: None):
        self.link, self.fleet_key, self.leader_key = link, fleet_key, leader_key
        self.state_path, self.decide = state_path, decide
        self.done = self._load()

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.done, f)
        os.replace(tmp, self.state_path)

    def _ack(self, did: str, status: str, reason: str | None = None) -> dict:
        payload = {"directive": did, "status": status}
        if reason:
            payload["reason"] = reason
        return self.link.append("communication", summary=f"directive {did[:8]}: {status}", tags=[ACK_TAG],
                                payload=payload)

    def on_message(self, payload: bytes, now: datetime | None = None) -> str:
        """Handle one fleet-topic message. Returns what happened (for logs and tests)."""
        if not payload:
            return "cleared"  # a retained directive was cleared
        try:
            msg = json.loads(payload)
        except ValueError:
            return "unreadable"
        did = str(msg.get("id") or "")
        if msg.get("kind") == "cancel":
            if verify(self.fleet_key(), CANCEL_DOMAIN, msg.get("statement"), msg.get("signature")):
                self.done.setdefault(did, {})["cancelled"] = True
                self._save()
                return "cancelled"
            return "invalid"
        if msg.get("kind") != "directive" or not did:
            return "ignored"
        if did in self.done:
            return "already"
        key = self.leader_key() if msg.get("signer") == "leader" else self.fleet_key()
        if not verify(key, DOMAIN, msg.get("statement"), msg.get("signature")):
            self.done[did] = {"invalid": True}
            self._save()
            self.link.append("event", summary=f"invalid directive {did[:8]}", tags=[INVALID_TAG],
                             payload={"directive": did})
            return "invalid"
        stmt = json.loads(msg["statement"])
        if stmt.get("id") != did:
            return "invalid"
        exp = stmt.get("expires_at")
        if exp and datetime.fromisoformat(exp.replace("Z", "+00:00")) <= (now or datetime.now(timezone.utc)):
            return "expired"
        self._ack(did, "received")
        try:
            decision = self.decide(stmt)
        except Exception as exc:  # noqa: BLE001 - a broken behaviour doesn't block the ack
            logging.exception("Member: directive decision failed")
            decision = Decision(False, f"the member's decision code failed: {type(exc).__name__}")
        self._ack(did, "accepted" if decision.accept else "declined", decision.reason)
        self.done[did] = {"status": "accepted" if decision.accept else "declined"}
        self._save()
        return self.done[did]["status"]
