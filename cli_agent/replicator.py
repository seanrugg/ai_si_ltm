"""Hub replicator: relay the outbox and prune only on LTMS acks.

Takes the hub's ``Outbox`` of signed, unacknowledged records, relays each
agent's pending batch to LTMS through the appointed hub client, and prunes an
agent's queue only up to the seq LTMS acknowledged. The returned ``heads`` are
what the hub later publishes to robots on their ``acks`` topic, so a robot
deletes its local copy only after LTMS, not the hub, has the record.
"""
from __future__ import annotations

from cli_agent.outbox import Outbox


def replicate_once(client, outbox: Outbox, batch_size: int = 100) -> dict:
    """Relay every pending record once. Returns {"heads": {...}, "errors": {...}}.

    One relay call per agent. Network or auth errors are not caught: they raise
    and nothing is pruned. When a batch is rejected, only the valid prefix is
    pruned and the first rejected record is recorded under ``errors[agent]``;
    the rest of the batch stays queued and the next agent is still processed.
    """
    heads: dict = {}
    errors: dict = {}
    for agent in outbox.agents():
        recs = outbox.pending(agent, batch_size)
        if not recs:
            continue
        res = client.relay(recs)
        for name, head in res["heads"].items():
            outbox.prune(name, head["last_ack_seq"])
            heads[name] = head
        if not res["ok"]:
            bad = next(r for r in res["results"] if r["status"] == "rejected")
            errors[agent] = {
                "seq": bad["seq"],
                "code": bad["code"],
                "message": bad["message"],
            }
    return {"heads": heads, "errors": errors}
