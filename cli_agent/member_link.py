"""MemberLink: the robot side of the MQTT link.

Signs a member's records, keeps them in a persistent Outbox until LTMS
acknowledges them (via the hub's acks topic), and continues the member's chain
correctly after a reboot. No MQTT library: the caller passes in a publish
function.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from cli_agent.mqtt_messages import (
    MAX_MESSAGE_SUMMARY, SWARM_MESSAGE_TAG, decode_ack, encode_record, records_topic, say_topic,
)
from cli_agent.outbox import Outbox
from ltms_contract import GENESIS_HASH, build_record


class MemberLink:
    """Signs and queues a member's records, resending until LTMS acks them."""

    def __init__(
        self,
        name: str,
        signing_key,
        outbox: Outbox,
        state_path,
        publish,
        swarm: str,
    ) -> None:
        self._name = name
        self._signing_key = signing_key
        self._outbox = outbox
        self._state_path = Path(state_path)
        self._publish = publish
        self._swarm = swarm

        # Load the chain head (seq, hash): from the state file if it exists,
        # from the outbox's last record if any, using whichever has the higher
        # seq; fall back to (0, GENESIS_HASH).
        head_seq = 0
        head_hash = GENESIS_HASH
        if self._state_path.exists():
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            head_seq = int(data["seq"])
            head_hash = data["hash"]
        last = self._outbox.last(name)
        if last is not None and int(last["seq"]) > head_seq:
            head_seq = int(last["seq"])
            head_hash = last["hash"]
        self._head = {"seq": head_seq, "hash": head_hash}

    @property
    def head(self) -> dict:
        return {"seq": self._head["seq"], "hash": self._head["hash"]}

    def append(
        self, type: str, *, payload: dict | None = None, ts: str | None = None, **header
    ) -> dict:
        rec = self._sign_and_store(type, payload=payload, ts=ts, **header)
        self._send(records_topic(self._name, self._swarm), rec)
        return rec

    def send_message(self, text: str, *, to=None, reply_to: str | None = None,
                     payload: dict | None = None) -> dict:
        """Say something to the swarm (#300): a signed ``communication`` record on the say topic.

        ``to``: recipient member names (omit for the whole swarm). ``reply_to``:
        ``"agent:seq"`` of the message this answers. The hub forwards it to LTMS like a
        record; until LTMS acks it, it stays in the outbox and is resent as a record.
        """
        if not isinstance(text, str) or not text or len(text) > MAX_MESSAGE_SUMMARY:
            raise ValueError(f"a swarm message needs text of 1..{MAX_MESSAGE_SUMMARY} characters")
        header = {"summary": text, "tags": [SWARM_MESSAGE_TAG]}
        if to:
            header["peers"] = [to] if isinstance(to, str) else list(to)
        if reply_to:
            header["refs"] = {"reply_to": reply_to}
        rec = self._sign_and_store("communication", payload=payload, **header)
        self._send(say_topic(self._name, self._swarm), rec)
        return rec

    def _sign_and_store(self, type: str, *, payload: dict | None = None, ts: str | None = None,
                        **header) -> dict:
        head = self.head
        rec = build_record(
            self._signing_key,
            agent=self._name,
            seq=head["seq"] + 1,
            prev_hash=head["hash"],
            type=type,
            payload=payload,
            ts=ts,
            **header,
        )
        # Persist first: a crash after this is still recovered by outbox.last.
        self._outbox.add(rec)
        self._head = {"seq": rec["seq"], "hash": rec["hash"]}

        # Atomically persist the head so a reboot can continue the chain even
        # after the outbox has been fully pruned.
        tmp = Path(str(self._state_path) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._head, f)
        os.replace(tmp, self._state_path)
        return rec

    def _send(self, topic: str, rec: dict) -> None:
        try:
            self._publish(topic, encode_record(rec))
        except Exception:  # keep the record in the outbox for resend_pending
            logging.warning(
                "MemberLink: publish of %s/%s failed; record stays in the outbox",
                self._name,
                rec["seq"],
            )

    def on_ack(self, payload: bytes) -> int:
        head = decode_ack(payload)  # ValueError propagates to the caller
        return self._outbox.prune(self._name, head["last_ack_seq"])

    def resend_pending(self, limit: int = 100) -> int:
        published = 0
        for rec in self._outbox.pending(self._name, limit):
            try:
                self._publish(records_topic(self._name, self._swarm), encode_record(rec))
            except Exception:  # stop at the first failure, return count so far
                logging.warning(
                    "MemberLink: resend of %s/%s failed; stopping", self._name, rec["seq"]
                )
                break
            published += 1
        return published
