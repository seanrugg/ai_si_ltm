"""Directives for a mind with no hub (#341).

A member that reaches LTMS only over HTTPS never sees the swarm bus, so it pulls
its directives (``GET /api/members/me/directives``) as the same signed messages a
hub publishes, and hands each one to ``cli_agent.directives.DirectiveHandler``.
That code verifies the fleet's or the leader's signature and records the
``directive-ack`` statuses (``received``, then ``accepted`` or ``declined`` with a
reason), exactly as a bus member does (#306). Nothing here trusts the server's
word for what a directive says.

**Followed by default.** The brain may decline, with a reason. A paused mind
declines, saying so. A brain that has no view, fails, or is out of budget
doesn't block: the directive is followed.

Accepted directives stay open (in the mind's state) until they expire, are
cancelled, or I record a ``done`` or ``couldnt`` report on them. The agenda turns
each open one into an obligation (``loop.py``).
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Callable

from cli_agent.directives import ACK_TAG, Decision, DirectiveHandler

log = logging.getLogger("ltms_mind.directives")

REPORT_TAG = "directive-report"
CLOSED_REPORTS = ("done", "couldnt")
STATE_KEY = "directives"


def _expired(stmt: dict, now: datetime) -> bool:
    exp = stmt.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp.replace("Z", "+00:00")) <= now
    except ValueError:
        return True


class _Link:
    """What ``DirectiveHandler`` writes through: the mind's own append, noting the
    accepted ack so a report can point at it."""

    def __init__(self, append: Callable[..., dict]) -> None:
        self.append_fn = append
        self.accepted: dict[str, str] = {}

    def append(self, type: str, **kw) -> dict:
        rec = self.append_fn(type, **kw)
        pl = kw.get("payload") or {}
        if ACK_TAG in (kw.get("tags") or []) and pl.get("status") == "accepted":
            self.accepted[pl["directive"]] = f"{rec['agent']}:{rec['seq']}"
        return rec


class MindDirectives:
    def __init__(self, client, state, handler_path: str, append: Callable[..., dict],
                 decide: Callable[[dict], Decision]) -> None:
        self.client, self.state, self.handler_path = client, state, handler_path
        self.append, self.decide = append, decide

    def open(self, now: datetime) -> list[dict]:
        """Accepted directives still open: ``[{"id", "statement", "ack", "signer"}]``, oldest first."""
        kept = {k: v for k, v in (self.state.get(STATE_KEY) or {}).items() if not _expired(v["statement"], now)}
        self.state[STATE_KEY] = kept
        return [{"id": k, **v} for k, v in sorted(kept.items(), key=lambda kv: kv[1]["statement"].get("issued_at") or "")]

    def close(self, directive_id: str) -> None:
        kept = dict(self.state.get(STATE_KEY) or {})
        if kept.pop(directive_id, None) is not None:
            self.state[STATE_KEY] = kept

    def run(self, swarm: dict | None, counts: Counter, now: datetime | None = None) -> list[dict]:
        """Pull, verify, decide and acknowledge. Returns the open accepted directives."""
        now = now or datetime.now(timezone.utc)
        try:
            items = self.client.my_directives()
        except Exception as exc:  # noqa: BLE001 - offline or an old server: try next wake
            log.warning("directives unavailable: %s", exc)
            counts["directives_unavailable"] += 1
            return self.open(now)
        if items:
            fleet_key = self.client.fleet_key()
            leader = ((swarm or {}).get("leader") or {}).get("public_key")
            link = _Link(self.append)
            handler = DirectiveHandler(link, lambda: fleet_key, self.handler_path, decide=self.decide,
                                       leader_key=lambda: leader)
            opened = dict(self.state.get(STATE_KEY) or {})
            for item in items:
                if item.get("kind") == "cancel" and (handler.done.get(str(item.get("id"))) or {}).get("cancelled"):
                    continue  # a cancel is listed for 7 days; count it once
                try:
                    result = handler.on_message(json.dumps(item).encode(), now=now)
                except Exception:  # noqa: BLE001 - a bad directive never stops the wake
                    log.exception("directive %s failed", item.get("id"))
                    counts["directive_errors"] += 1
                    continue
                if result != "already":
                    counts[f"directive_{result}"] += 1
                did = str(item.get("id") or "")
                if result == "accepted" and did in link.accepted:
                    opened[did] = {"statement": json.loads(item["statement"]), "ack": link.accepted[did],
                                   "signer": item.get("signer") or "fleet"}
                elif result == "cancelled":
                    opened.pop(did, None)
            self.state[STATE_KEY] = opened
        return self.open(now)
