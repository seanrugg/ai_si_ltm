"""Deleting a single record, leaving a tombstone (issue #173).

An admin can delete any record, for any reason. The chain stays valid: a
``RecordTombstone`` keeps the record's hash and prev_hash, so the next record
still links. The database enforces it: a ``records`` row can only be deleted
after its tombstone exists. Records that refer to the deleted one keep their
refs; those resolve to ``{"deleted": true, ...}``. The LRS is not told.
"""
from __future__ import annotations

from sqlalchemy import String, cast, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import (
    AuditLog,
    Record,
    RecordPayload,
    RecordProvenance,
    RecordTombstone,
)
from server.services import policy_lifecycle
from server.services.errors import ServiceError

MAX_REASON = 500


def tombstone_to_dict(t: RecordTombstone) -> dict:
    return {
        "deleted": True,
        "agent": t.agent,
        "seq": t.seq,
        "hash": t.hash,
        "prev_hash": t.prev_hash,
        "deleted_at": t.deleted_at.isoformat(),
        "deleted_by": t.deleted_by,
        "reason": t.reason,
    }


MAX_TOMBSTONES = 500


async def list_tombstones(session: AsyncSession, agent: str, *, seq_from: int | None = None,
                          seq_to: int | None = None) -> list[RecordTombstone]:
    """One agent's tombstones, seq descending, at most MAX_TOMBSTONES (#205).

    The range is by seq (inclusive): a tombstone keeps no timestamp from the
    record it replaced. An unknown agent has none.
    """
    stmt = select(RecordTombstone).where(RecordTombstone.agent == agent)
    if seq_from is not None:
        stmt = stmt.where(RecordTombstone.seq >= seq_from)
    if seq_to is not None:
        stmt = stmt.where(RecordTombstone.seq <= seq_to)
    stmt = stmt.order_by(RecordTombstone.seq.desc()).limit(MAX_TOMBSTONES)
    return list(await session.scalars(stmt))


async def get_tombstone(session: AsyncSession, agent: str, seq: int) -> RecordTombstone | None:
    return await session.scalar(select(RecordTombstone).where(RecordTombstone.agent == agent,
                                                              RecordTombstone.seq == seq))


async def _get_record(session: AsyncSession, agent: str, seq: int) -> Record:
    rec = await session.scalar(select(Record).where(Record.agent == agent, Record.seq == seq))
    if rec is not None:
        return rec
    if await get_tombstone(session, agent, seq) is not None:
        raise ServiceError(409, "already_deleted", f"{agent}:{seq} was already deleted")
    raise ServiceError(404, "not_found", "record not found")


def _ref_targets(refs) -> set[str]:
    out: set[str] = set()
    if not isinstance(refs, dict):
        return out
    for value in refs.values():
        if isinstance(value, str):
            out.add(value)
        elif isinstance(value, list):
            out.update(v for v in value if isinstance(v, str))
    return out


async def referenced_by(session: AsyncSession, agent: str, seq: int) -> list[Record]:
    """Records whose refs name ``agent:seq`` (an exact match, not a prefix)."""
    key = f"{agent}:{seq}"
    candidates = await session.scalars(
        select(Record).where(Record.refs.is_not(None), cast(Record.refs, String).like(f'%"{key}"%'))
        .order_by(Record.id))
    return [r for r in candidates if key in _ref_targets(r.refs)]


async def policy_effect(session: AsyncSession, rec: Record) -> str | None:
    """One sentence when deleting ``rec`` changes which policies are in force."""
    if rec.type != "policy":
        return None
    policies = list(await session.scalars(select(Record).where(Record.type == "policy")))
    before = policy_lifecycle.classify(policies)
    after = policy_lifecycle.classify([p for p in policies if p.id != rec.id])
    key = policy_lifecycle.policy_key(rec)
    parts = []
    if before.get(key, {}).get("status") == "in_force":
        parts.append(f"{key} is in force; deleting it takes it out of force")
    restored = sorted(k for k, v in after.items()
                      if v["status"] == "in_force" and before.get(k, {}).get("status") == "retired")
    if restored:
        parts.append(f"{', '.join(restored)} {'goes' if len(restored) == 1 else 'go'} back into force")
    return ("; ".join(parts) + ".") if parts else None


async def delete_preview(session: AsyncSession, agent: str, seq: int) -> dict:
    rec = await _get_record(session, agent, seq)
    refs = await referenced_by(session, agent, seq)
    return {
        "agent": rec.agent,
        "seq": rec.seq,
        "type": rec.type,
        "summary": rec.summary,
        "referenced_by": [{"agent": r.agent, "seq": r.seq, "type": r.type, "summary": r.summary} for r in refs],
        "policy_effect": await policy_effect(session, rec),
    }


async def delete_record(session: AsyncSession, agent: str, seq: int, *, admin: str,
                        reason: str | None = None) -> RecordTombstone:
    """Replace one record with its tombstone. Dependent rows go first."""
    reason = (reason or "").strip() or None
    if reason is not None and len(reason) > MAX_REASON:
        raise ServiceError(422, "bad_reason", f"reason is at most {MAX_REASON} characters")
    rec = await _get_record(session, agent, seq)
    tomb = RecordTombstone(member_id=rec.member_id, agent=rec.agent, seq=rec.seq, hash=rec.hash,
                           prev_hash=rec.prev_hash, deleted_by=admin, reason=reason)
    session.add(tomb)
    await session.flush()  # the tombstone must exist before the trigger sees the DELETE
    await session.execute(delete(RecordPayload).where(RecordPayload.record_id == rec.id))
    await session.execute(delete(RecordProvenance).where(RecordProvenance.record_id == rec.id))
    await session.execute(delete(Record).where(Record.id == rec.id))
    session.add(AuditLog(actor=admin, action="record_delete", detail={
        "agent": tomb.agent, "seq": tomb.seq, "hash": tomb.hash, "reason": reason,
    }))
    return tomb


PURGE_BATCH = 500
PURGE_MAX = 100_000


def swarm_records_query(swarm_id: str):
    """IDs of the records that came through a swarm (provenance), oldest first."""
    return (select(Record.id).join(RecordProvenance, RecordProvenance.record_id == Record.id)
            .where(RecordProvenance.swarm_id == swarm_id).order_by(Record.id))


async def delete_records_bulk(session: AsyncSession, *, swarm_id: str, admin: str, reason: str | None = None,
                              batch: int = PURGE_BATCH) -> int:
    """Delete every record that came through a swarm, leaving tombstones (#233).

    The same steps as ``delete_record`` (tombstone first, then payload,
    provenance and record), in batches, inside the caller's transaction. One
    ``records_purge`` audit row for the whole purge. At most PURGE_MAX records.
    """
    reason = (reason or "").strip() or None
    if reason is not None and len(reason) > MAX_REASON:
        raise ServiceError(422, "bad_reason", f"reason is at most {MAX_REASON} characters")
    ids = list(await session.scalars(swarm_records_query(swarm_id)))
    if len(ids) > PURGE_MAX:
        raise ServiceError(422, "too_many", f"purge in steps: {len(ids)} records is over the {PURGE_MAX} limit")
    for start in range(0, len(ids), batch):
        chunk = ids[start:start + batch]
        recs = list(await session.scalars(select(Record).where(Record.id.in_(chunk))))
        session.add_all([RecordTombstone(member_id=r.member_id, agent=r.agent, seq=r.seq, hash=r.hash,
                                         prev_hash=r.prev_hash, deleted_by=admin, reason=reason) for r in recs])
        await session.flush()  # tombstones exist before the trigger sees the DELETEs
        await session.execute(delete(RecordPayload).where(RecordPayload.record_id.in_(chunk)))
        await session.execute(delete(RecordProvenance).where(RecordProvenance.record_id.in_(chunk)))
        await session.execute(delete(Record).where(Record.id.in_(chunk)))
    session.add(AuditLog(actor=admin, action="records_purge",
                         detail={"swarm": swarm_id, "count": len(ids), "reason": reason}))
    return len(ids)
