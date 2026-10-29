"""Ingest: verify each record against the contract and the member's chain head,
then store the header (permanent) and payload (purgeable).

Batch policy: records are processed in order and the **valid prefix is kept**.
At the first bad record processing stops; the records before it are committed
and acknowledged, the bad one is reported with a code, and anything after it is
reported as ``not_processed``. A member resends from ``last_ack_seq + 1``.

Retries are idempotent: a record whose ``seq`` is already stored with the same
``hash`` is reported as ``duplicate`` (not an error), so a robot that lost an
ack can simply resend. Same ``seq`` with a different ``hash`` is a ``conflict``
— the member's own history forked — and is rejected.

Relays (issue #15): the **hub** of a swarm may submit records written by other
members of its swarm, unchanged. Each record is still checked against its
**author's** key and chain, so a relay can carry records but never forge or
alter them. A batch may mix authors; every member row it touches is locked in
id order, so concurrent batches can't deadlock. Every stored record gets a
provenance row: the author's swarm at that moment, and the relay (or none).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ltms_contract import ContractError, parse_ts, verify_record
from server.models import AuditLog, Member, Record, RecordPayload, RecordProvenance
from server.services import swarms

MEMBER_FORBIDDEN_TYPES = {"system"}  # written only by the server itself


@dataclass
class IngestResult:
    results: list[dict] = field(default_factory=list)
    accepted: int = 0
    duplicates: int = 0
    last_ack_seq: int = 0
    last_hash: str = "0" * 64
    heads: dict = field(default_factory=dict)  # agent -> {"last_ack_seq", "last_hash"} for every author touched

    @property
    def ok(self) -> bool:
        return all(r["status"] in ("accepted", "duplicate") for r in self.results)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "last_ack_seq": self.last_ack_seq,
            "last_hash": self.last_hash,
            "heads": self.heads,
            "results": self.results,
        }


class IngestRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _seq_of(record: object) -> int | None:
    if isinstance(record, dict) and isinstance(record.get("seq"), int) and not isinstance(record["seq"], bool):
        return record["seq"]
    return None


async def _check_one(session: AsyncSession, member: Member, record: dict, now: datetime, skew: int) -> tuple[str, object]:
    """Return ("duplicate", None) or ("new", VerifiedRecord); raise IngestRejected."""
    if isinstance(record, dict) and "agent" in record and record["agent"] != member.name:
        raise IngestRejected("wrong_agent", f"agent {record['agent']!r} is not this member ({member.name!r})")
    try:
        verified = verify_record(record, member.public_key)
    except ContractError as exc:
        raise IngestRejected(exc.code, exc.message) from None
    header = verified.header

    if header["agent"] != member.name:
        raise IngestRejected("wrong_agent", f"agent {header['agent']!r} is not this member ({member.name!r})")
    if header["type"] in MEMBER_FORBIDDEN_TYPES:
        raise IngestRejected("type_forbidden", f"members may not write {header['type']!r} records")
    if header["type"] == "policy" and "policy" not in member.permissions:
        raise IngestRejected("type_forbidden", "writing policy records needs the 'policy' permission")
    if parse_ts(header["ts"]) > now + timedelta(seconds=skew):
        raise IngestRejected("future_ts", f"ts is more than {skew}s in the future; check the member's clock")

    seq = header["seq"]
    if seq <= member.last_ack_seq:
        stored = await session.scalar(
            select(Record.hash).where(Record.member_id == member.id, Record.seq == seq)
        )
        if stored == verified.hash:
            return "duplicate", None
        raise IngestRejected("conflict", f"seq {seq} is already stored with a different hash")
    if seq != member.last_ack_seq + 1:
        raise IngestRejected("gap", f"expected seq {member.last_ack_seq + 1}, got {seq}")
    if header["prev_hash"] != member.last_hash:
        raise IngestRejected("chain", "prev_hash does not match the hash of the member's last record")
    return "new", verified


def _agent_of(record: object) -> str | None:
    if isinstance(record, dict) and isinstance(record.get("agent"), str):
        return record["agent"]
    return None


async def ingest_batch(
    session: AsyncSession,
    member_id,
    records: list,
    *,
    future_skew_seconds: int = 300,
    request_id: str | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Verify and store ``records`` submitted by one member (its own, or relayed). Caller commits."""
    now = now or datetime.now(timezone.utc)
    submitter_name = await session.scalar(select(Member.name).where(Member.id == member_id))
    others = {a for a in map(_agent_of, records) if a is not None and a != submitter_name}

    # Lock every member row this batch can touch, always in id order: concurrent
    # batches for the same author serialize here (so seq/prev_hash checks can't
    # race), and two relays can't deadlock on each other. No-op on SQLite.
    locked = list(await session.scalars(
        select(Member).where(or_(Member.id == member_id, Member.name.in_(others)))
        .order_by(Member.id).with_for_update()
    ))
    by_name = {m.name: m for m in locked}
    submitter = next((m for m in locked if m.id == member_id), None)
    if submitter is None or submitter.status != "active":
        raise IngestRejected("not_active", "member is not active")
    if "contribute" not in submitter.permissions:
        raise IngestRejected("forbidden", "member lacks the 'contribute' permission")
    relay_swarm = await swarms.hub_swarm_id(session, submitter) if others else None
    memberships = await swarms.open_memberships(session, [m.id for m in locked])

    def swarm_of(m: Member) -> str | None:
        row = memberships.get(m.id)
        return row.swarm_id if row is not None else None

    def author_for(record: object) -> Member:
        agent = _agent_of(record)
        if agent is None or agent == submitter.name:
            return submitter
        if relay_swarm is None:
            raise IngestRejected("wrong_agent", f"agent {agent!r} is not this member ({submitter.name!r}), "
                                                "and this member isn't the hub of an active swarm")
        author = by_name.get(agent)
        if author is None or swarm_of(author) != relay_swarm:
            raise IngestRejected("not_in_swarm", f"{agent!r} is not a member of this relay's swarm")
        if author.status != "active":
            raise IngestRejected("not_active", f"member {agent!r} is not active")
        if "contribute" not in author.permissions:
            raise IngestRejected("forbidden", f"member {agent!r} lacks the 'contribute' permission")
        return author

    result = IngestResult(last_ack_seq=submitter.last_ack_seq, last_hash=submitter.last_hash)
    touched: dict[str, Member] = {}
    stopped = False
    for index, record in enumerate(records):
        seq = _seq_of(record)
        agent = _agent_of(record) or submitter.name
        if stopped:
            result.results.append({"index": index, "agent": agent, "seq": seq, "status": "not_processed"})
            continue
        try:
            author = author_for(record)
            touched[author.name] = author
            kind, verified = await _check_one(session, author, record, now, future_skew_seconds)
        except IngestRejected as exc:
            stopped = True
            result.results.append({"index": index, "agent": agent, "seq": seq, "status": "rejected",
                                   "code": exc.code, "message": exc.message})
            session.add(
                AuditLog(
                    member_id=submitter.id,
                    actor=submitter.name,
                    action="ingest_reject",
                    detail={"agent": agent, "seq": seq, "code": exc.code, "message": exc.message},
                    request_id=request_id,
                )
            )
            continue

        if kind == "duplicate":
            result.duplicates += 1
            result.results.append({"index": index, "agent": author.name, "seq": seq, "status": "duplicate"})
            continue

        h = verified.header
        row = Record(
            member_id=author.id,
            agent=h["agent"],
            seq=h["seq"],
            ts=parse_ts(h["ts"]),
            type=h["type"],
            summary=h.get("summary"),
            place=h.get("place"),
            outcome=h.get("outcome"),
            peers=h.get("peers"),
            tags=h.get("tags"),
            refs=h.get("refs"),
            sw=h.get("sw"),
            prev_hash=h["prev_hash"],
            payload_hash=h["payload_hash"],
            hash=verified.hash,
            sig=verified.sig,
            header_raw=verified.header_bytes,
            received_at=now,
        )
        session.add(row)
        await session.flush()
        session.add(
            RecordPayload(
                record_id=row.id,
                payload_raw=verified.payload_bytes,
                body=verified.payload,
                size=len(verified.payload_bytes),
            )
        )
        session.add(
            RecordProvenance(
                record_id=row.id,
                swarm_id=swarm_of(author),
                relay_member_id=submitter.id if author is not submitter else None,
                received_at=now,
            )
        )
        author.last_ack_seq = h["seq"]
        author.last_hash = verified.hash
        result.accepted += 1
        result.results.append({"index": index, "agent": author.name, "seq": seq, "status": "accepted",
                               "hash": verified.hash})

    submitter.last_seen_at = now
    result.last_ack_seq = submitter.last_ack_seq
    result.last_hash = submitter.last_hash
    result.heads = {name: {"last_ack_seq": m.last_ack_seq, "last_hash": m.last_hash}
                    for name, m in sorted(touched.items())}
    await session.flush()
    return result
