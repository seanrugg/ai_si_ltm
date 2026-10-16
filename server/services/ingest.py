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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ltms_contract import ContractError, parse_ts, verify_record
from server.models import AuditLog, Member, Record, RecordPayload

MEMBER_FORBIDDEN_TYPES = {"system"}  # written only by the server itself


@dataclass
class IngestResult:
    results: list[dict] = field(default_factory=list)
    accepted: int = 0
    duplicates: int = 0
    last_ack_seq: int = 0
    last_hash: str = "0" * 64

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


async def ingest_batch(
    session: AsyncSession,
    member_id,
    records: list,
    *,
    future_skew_seconds: int = 300,
    request_id: str | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Verify and store ``records`` for one member. Caller commits."""
    now = now or datetime.now(timezone.utc)
    # Lock the member row: concurrent batches for the same member serialize here,
    # so seq/prev_hash checks cannot race. (No-op on SQLite, which locks the DB.)
    member = await session.scalar(select(Member).where(Member.id == member_id).with_for_update())
    if member is None or member.status != "active":
        raise IngestRejected("not_active", "member is not active")
    if "contribute" not in member.permissions:
        raise IngestRejected("forbidden", "member lacks the 'contribute' permission")

    result = IngestResult(last_ack_seq=member.last_ack_seq, last_hash=member.last_hash)
    stopped = False
    for index, record in enumerate(records):
        seq = _seq_of(record)
        if stopped:
            result.results.append({"index": index, "seq": seq, "status": "not_processed"})
            continue
        try:
            kind, verified = await _check_one(session, member, record, now, future_skew_seconds)
        except IngestRejected as exc:
            stopped = True
            result.results.append(
                {"index": index, "seq": seq, "status": "rejected", "code": exc.code, "message": exc.message}
            )
            session.add(
                AuditLog(
                    member_id=member.id,
                    actor=member.name,
                    action="ingest_reject",
                    detail={"seq": seq, "code": exc.code, "message": exc.message},
                    request_id=request_id,
                )
            )
            continue

        if kind == "duplicate":
            result.duplicates += 1
            result.results.append({"index": index, "seq": seq, "status": "duplicate"})
            continue

        h = verified.header
        row = Record(
            member_id=member.id,
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
        member.last_ack_seq = h["seq"]
        member.last_hash = verified.hash
        result.accepted += 1
        result.results.append({"index": index, "seq": seq, "status": "accepted", "hash": verified.hash})

    member.last_seen_at = now
    result.last_ack_seq = member.last_ack_seq
    result.last_hash = member.last_hash
    await session.flush()
    return result
