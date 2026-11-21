"""Chain re-verification from stored bytes, incremental by default (issue #439).

For each record, in seq order:
  * seq is contiguous from 1
  * SHA-256(header_raw) == hash, and the signature over it verifies
  * prev_hash links to the previous record's hash (genesis for seq 1)
  * the queryable columns agree with header_raw (catches edits that bypass the trigger)
  * if the payload is still stored, SHA-256(payload_raw) == payload_hash
  * a deleted record's tombstone is a valid link: its prev_hash must link to
    the previous hash, and the chain continues from its hash (issue #173)
Finally the member's chain head (last_ack_seq / last_hash) must match the last record.

Checkpoints (#439). After a check, each member's ``IntegrityCheckpoint`` keeps
the last verified seq and hash and the counts up to there. A normal
("incremental") check verifies only the records after the checkpoint, linking
the first of them to the checkpoint hash, and it stops after ``budget`` records
per call (status ``partial``; the next call carries on), so it never grows with
the fleet's history. A "full" check (``run_full``, a background job) verifies
every chain from genesis and rewrites the checkpoints.

A checkpoint is not used, and the member is verified from genesis again, when:
  * the member's public key is not the one the checkpoint was made with;
  * the record (or tombstone) at the checkpoint seq is gone or has another hash;
  * the number of tombstones at or before the checkpoint changed, i.e. an admin
    deleted a record the checkpoint already covered;
  * the last check from genesis found the chain broken (the checkpoint is then
    reset to genesis, so the break keeps showing until it is fixed).
Retention purges don't void a checkpoint: a purged payload is expected and the
chain doesn't depend on it. ``note_payloads_purged`` moves the counts instead.

What an incremental check can't see: someone with database superuser rights
editing stored bytes at or before the checkpoint. That is what a full check is
for.

Records are read in keyset batches (``seq > last``, ``LIMIT batch``), never a
whole chain at once.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ltms_contract import GENESIS_HASH, record_signing_message, sha256, verify_signature
from server.models import (
    IntegrityCheckpoint,
    IntegrityRun,
    Member,
    Record,
    RecordPayload,
    RecordTombstone,
)
from server.services.errors import ServiceError

log = logging.getLogger(__name__)

_COLUMN_FIELDS = ("agent", "seq", "type", "prev_hash", "payload_hash", "summary", "place", "outcome")
_COUNTS = ("records", "payloads_present", "payloads_purged", "deleted")
CHECKED_STATUSES = ("active", "revoked")

BATCH = 500
# Records an incremental check verifies per call, across all members.
INCREMENTAL_BUDGET = 5000
# Commit a full run's progress at least this often (records).
PROGRESS_EVERY = 2000
# A "running" full run not updated for this long died with its process.
RUN_STALE_AFTER = timedelta(minutes=10)

Progress = Callable[[int], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _key_hash(member: Member) -> str:
    return hashlib.sha256(bytes(member.public_key)).hexdigest()


def _new_report(member: Member) -> dict:
    return {
        "member": member.name,
        "member_id": str(member.id),
        "status": "ok",
        "records": 0,
        "payloads_present": 0,
        "payloads_purged": 0,
        "deleted": 0,
        "head_seq": member.last_ack_seq,
        "error": None,
    }


async def _walk(session: AsyncSession, member: Member, report: dict, *, after_seq: int, prev: str,
                budget: int | None = None, batch: int = BATCH,
                progress: Progress | None = None) -> tuple[int, str, bool] | None:
    """Verify ``member``'s chain after ``after_seq``, linking from ``prev``.

    Adds to the counts in ``report``. Returns ``(last_seq, last_hash, complete)``
    (``complete`` is False when ``budget`` ran out first), or None when the
    chain is broken; ``report`` then carries the error.
    """

    def fail(seq: int | None, code: str, message: str) -> None:
        report["status"] = "broken"
        report["error"] = {"seq": seq, "code": code, "message": message}

    tombs = {t.seq: t for t in await session.scalars(
        select(RecordTombstone).where(RecordTombstone.member_id == member.id, RecordTombstone.seq > after_seq))}

    def through_tombstones(seq: int, prev_hash: str) -> tuple[int, str] | None:
        while seq in tombs:
            t = tombs[seq]
            if t.prev_hash != prev_hash:
                return fail(seq, "chain", "deleted record's prev_hash does not link to the previous record")
            report["deleted"] += 1
            seq, prev_hash = seq + 1, t.hash
        return seq, prev_hash

    expected_seq, remaining = after_seq + 1, budget
    while True:
        limit = batch if remaining is None else min(batch, remaining)
        if limit <= 0:
            more = await session.scalar(select(Record.seq).where(
                Record.member_id == member.id, Record.seq >= expected_seq).limit(1))
            if more is not None:
                return expected_seq - 1, prev, False
            break
        rows = (
            await session.execute(
                select(Record, RecordPayload)
                .outerjoin(RecordPayload, RecordPayload.record_id == Record.id)
                .where(Record.member_id == member.id, Record.seq >= expected_seq)
                .order_by(Record.seq)
                .limit(limit)
            )
        ).all()
        if not rows:
            break
        for rec, payload in rows:
            step = through_tombstones(expected_seq, prev)
            if step is None:
                return None
            expected_seq, prev = step
            if rec.seq != expected_seq:
                return fail(expected_seq, "gap", f"missing seq {expected_seq} (next stored is {rec.seq})")
            if sha256(rec.header_raw).hex() != rec.hash:
                return fail(rec.seq, "hash", "stored header bytes do not match hash")
            if not verify_signature(member.public_key, record_signing_message(rec.hash), rec.sig):
                return fail(rec.seq, "sig", "signature does not verify")
            header = json.loads(rec.header_raw)
            if header.get("prev_hash") != prev:
                return fail(rec.seq, "chain", "prev_hash does not link to the previous record")
            for field in _COLUMN_FIELDS:
                if header.get(field) != getattr(rec, field):
                    return fail(rec.seq, "column_mismatch", f"column {field!r} disagrees with signed header")
            if payload is not None:
                report["payloads_present"] += 1
                if sha256(payload.payload_raw).hex() != rec.payload_hash:
                    return fail(rec.seq, "payload_hash", "stored payload does not match payload_hash")
            else:
                report["payloads_purged"] += 1
            report["records"] += 1
            prev = rec.hash
            expected_seq += 1
        if remaining is not None:
            remaining -= len(rows)
        if progress is not None:
            await progress(len(rows))
        if len(rows) < limit:
            break

    step = through_tombstones(expected_seq, prev)  # deleted records at the end of the chain
    if step is None:
        return None
    expected_seq, prev = step
    if member.last_ack_seq != expected_seq - 1 or member.last_hash != prev:
        return fail(member.last_ack_seq, "head", "member chain head does not match the last stored record")
    return expected_seq - 1, prev, True


async def verify_member(session: AsyncSession, member: Member, *, batch: int = BATCH) -> dict:
    """Verify one chain from genesis. Reads only; stores nothing (CLI, tests)."""
    report = _new_report(member)
    if await _walk(session, member, report, after_seq=0, prev=GENESIS_HASH, batch=batch) is None:
        return report
    if report["records"] == 0 and report["deleted"] == 0:
        report["status"] = "empty"
    return report


async def verify_all(session: AsyncSession) -> list[dict]:
    """Verify every chain from genesis, storing nothing. Slow on a big fleet:
    the dashboard uses ``check_all`` and ``run_full`` instead."""
    members = await session.scalars(select(Member).where(Member.status.in_(CHECKED_STATUSES)).order_by(Member.name))
    return [await verify_member(session, m) for m in members]


# --- checkpoints -------------------------------------------------------------------

async def _checkpoint_problem(session: AsyncSession, member: Member, cp: IntegrityCheckpoint) -> str | None:
    """Why ``cp`` can't be trusted as a starting point, or None if it can."""
    if cp.key_hash != _key_hash(member):
        return "the member's key changed"
    if cp.last_verified_seq == 0:
        return None
    anchor = await session.scalar(select(Record.hash).where(
        Record.member_id == member.id, Record.seq == cp.last_verified_seq))
    if anchor is None:
        anchor = await session.scalar(select(RecordTombstone.hash).where(
            RecordTombstone.member_id == member.id, RecordTombstone.seq == cp.last_verified_seq))
    if anchor != cp.last_verified_hash:
        return "the record at the checkpoint changed"
    deleted = await session.scalar(select(func.count()).select_from(RecordTombstone).where(
        RecordTombstone.member_id == member.id, RecordTombstone.seq <= cp.last_verified_seq))
    if deleted != cp.deleted:
        return "a verified record was deleted"
    return None


def _result(cp: IntegrityCheckpoint, report: dict) -> dict:
    report.update({
        "mode": cp.mode,
        "verified_through": cp.last_verified_seq,
        "verified_at": _iso(cp.verified_at),
        "checked_at": _iso(cp.checked_at),
    })
    return report


async def check_member(session: AsyncSession, member: Member, *, full: bool = False,
                       budget: int | None = INCREMENTAL_BUDGET, progress: Progress | None = None,
                       now: datetime | None = None) -> dict:
    """Check one chain and store the result and checkpoint.

    Incremental (the default) starts from the stored checkpoint when it can be
    trusted and verifies at most ``budget`` records. ``full=True`` verifies from
    genesis with no budget.
    """
    now = now or _now()
    cp = await session.get(IntegrityCheckpoint, member.id)
    report = _new_report(member)
    after_seq, prev = 0, GENESIS_HASH
    if not full and cp is not None:
        problem = await _checkpoint_problem(session, member, cp)
        if problem is None:
            after_seq, prev = cp.last_verified_seq, cp.last_verified_hash
            for k in _COUNTS:
                report[k] = getattr(cp, k)
        else:
            report["checkpoint_reset"] = problem
    verified_before = report["records"] + report["deleted"]
    walked = await _walk(session, member, report, after_seq=after_seq, prev=prev,
                         budget=None if full else budget, progress=progress)
    report["checked"] = report["records"] + report["deleted"] - verified_before

    if cp is None:
        cp = IntegrityCheckpoint(member_id=member.id, last_verified_seq=0, last_verified_hash=GENESIS_HASH,
                                 records=0, payloads_present=0, payloads_purged=0, deleted=0,
                                 key_hash=_key_hash(member), verified_at=now)
        session.add(cp)
    if walked is None:
        if after_seq == 0:  # broken from genesis: nothing is verified any more
            cp.last_verified_seq, cp.last_verified_hash = 0, GENESIS_HASH
            for k in _COUNTS:
                setattr(cp, k, 0)
            cp.key_hash, cp.verified_at = _key_hash(member), now
    else:
        last_seq, last_hash, complete = walked
        cp.last_verified_seq, cp.last_verified_hash = last_seq, last_hash
        for k in _COUNTS:
            setattr(cp, k, report[k])
        cp.key_hash, cp.verified_at = _key_hash(member), now
        if not complete:
            report["status"] = "partial"
        elif report["records"] == 0 and report["deleted"] == 0:
            report["status"] = "empty"
    cp.status, cp.error = report["status"], report["error"]
    cp.mode = "full" if full else "incremental"
    cp.head_seq, cp.checked_at = member.last_ack_seq, now
    await session.flush()
    return _result(cp, report)


async def check_all(session: AsyncSession, *, budget: int = INCREMENTAL_BUDGET) -> list[dict]:
    """Incremental check of every chain, ``budget`` records in all."""
    members = list(await session.scalars(
        select(Member).where(Member.status.in_(CHECKED_STATUSES)).order_by(Member.name)))
    reports, left = [], budget
    for m in members:
        rep = await check_member(session, m, budget=left)
        left = max(0, left - rep["checked"])
        reports.append(rep)
    return reports


async def note_payloads_purged(session: AsyncSession, record_ids) -> None:
    """Move checkpoint counts for payloads about to be purged (call before the delete).

    ``record_ids`` is anything ``in_()`` takes. Only records at or before a
    checkpoint matter; later ones are counted when they are verified.
    """
    rows = await session.execute(
        select(Record.member_id, func.count())
        .join(RecordPayload, RecordPayload.record_id == Record.id)
        .join(IntegrityCheckpoint, IntegrityCheckpoint.member_id == Record.member_id)
        .where(Record.id.in_(record_ids), Record.seq <= IntegrityCheckpoint.last_verified_seq)
        .group_by(Record.member_id))
    for member_id, n in rows.all():
        await session.execute(
            update(IntegrityCheckpoint).where(IntegrityCheckpoint.member_id == member_id).values(
                payloads_present=IntegrityCheckpoint.payloads_present - n,
                payloads_purged=IntegrityCheckpoint.payloads_purged + n))


async def latest_results(session: AsyncSession) -> list[dict]:
    """The stored result of the latest check per member, without checking anything.

    One dict per active or revoked member, by name. A member never checked has
    status ``unchecked`` and no counts.
    """
    rows = await session.execute(
        select(Member, IntegrityCheckpoint)
        .outerjoin(IntegrityCheckpoint, IntegrityCheckpoint.member_id == Member.id)
        .where(Member.status.in_(CHECKED_STATUSES))
        .order_by(Member.name))
    out = []
    for m, cp in rows.all():
        if cp is None:
            out.append({"member": m.name, "status": "unchecked", "records": None, "head_seq": m.last_ack_seq,
                        "payloads_present": None, "payloads_purged": None, "deleted": None, "error": None,
                        "verified_through": None, "verified_at": None, "checked_at": None, "mode": None})
            continue
        out.append({"member": m.name, "status": cp.status, "records": cp.records, "head_seq": cp.head_seq,
                    "payloads_present": cp.payloads_present, "payloads_purged": cp.payloads_purged,
                    "deleted": cp.deleted, "error": cp.error, "verified_through": cp.last_verified_seq,
                    "verified_at": _iso(cp.checked_at), "checked_at": _iso(cp.checked_at), "mode": cp.mode})
    return out


# --- full check as a background job -----------------------------------------------

def run_to_dict(run: IntegrityRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": str(run.id), "mode": run.mode, "status": run.status, "started_by": run.started_by,
        "started_at": _iso(run.started_at), "updated_at": _iso(run.updated_at),
        "finished_at": _iso(run.finished_at), "members_total": run.members_total,
        "members_done": run.members_done, "records_done": run.records_done,
        "broken": run.broken, "error": run.error,
    }


async def _expire_stale(session: AsyncSession, now: datetime) -> None:
    stale = await session.scalars(select(IntegrityRun).where(
        IntegrityRun.status == "running", IntegrityRun.updated_at < now - RUN_STALE_AFTER))
    for run in stale:
        run.status, run.finished_at = "failed", now
        run.error = "interrupted: the server stopped before the run finished"


async def latest_run(session: AsyncSession) -> IntegrityRun | None:
    await _expire_stale(session, _now())
    return await session.scalar(select(IntegrityRun).order_by(IntegrityRun.started_at.desc()).limit(1))


async def start_full_run(session: AsyncSession, *, started_by: str) -> IntegrityRun:
    """Create a full run (the caller starts it with ``launch_full``). One at a time."""
    now = _now()
    await _expire_stale(session, now)
    running = await session.scalar(select(IntegrityRun).where(IntegrityRun.status == "running").limit(1))
    if running is not None:
        raise ServiceError(409, "already_running", "a full check is already running")
    run = IntegrityRun(mode="full", status="running", started_by=started_by, started_at=now, updated_at=now,
                       members_total=0, members_done=0, records_done=0, broken=0)
    session.add(run)
    await session.flush()
    return run


async def run_full(sessionmaker: async_sessionmaker, run_id: uuid.UUID,
                   on_done: Callable[[list[dict]], None] | None = None) -> list[dict]:
    """Verify every chain from genesis, one member per transaction, saving progress."""

    async def save(**values) -> None:
        async with sessionmaker() as s:
            await s.execute(update(IntegrityRun).where(IntegrityRun.id == run_id).values(
                updated_at=_now(), **values))
            await s.commit()

    reports: list[dict] = []
    try:
        async with sessionmaker() as s:
            member_ids = list(await s.scalars(
                select(Member.id).where(Member.status.in_(CHECKED_STATUSES)).order_by(Member.name)))
        await save(members_total=len(member_ids))
        done = {"records": 0, "saved": 0}

        async def progress(n: int) -> None:
            done["records"] += n
            if done["records"] - done["saved"] >= PROGRESS_EVERY:
                done["saved"] = done["records"]
                await save(records_done=done["records"])

        for i, member_id in enumerate(member_ids, start=1):
            async with sessionmaker() as s:
                member = await s.get(Member, member_id)
                if member is None:
                    continue
                rep = await check_member(s, member, full=True, progress=progress)
                await s.commit()
            reports.append(rep)
            done["records"] = done["saved"] = sum(r["checked"] for r in reports)  # exact, breaks included
            await save(members_done=i, records_done=done["records"],
                       broken=sum(r["status"] == "broken" for r in reports))
        if on_done is not None:
            on_done(reports)
        await save(status="done", finished_at=_now())
    except asyncio.CancelledError:
        try:
            await asyncio.shield(save(status="failed", finished_at=_now(), error="cancelled"))
        except Exception:  # shutting down; the stale check marks it later
            log.warning("integrity full run %s cancelled; status not saved", run_id)
        raise
    except Exception as exc:  # the run must end in a terminal state whatever happened
        log.exception("integrity full run %s failed", run_id)
        await save(status="failed", finished_at=_now(), error=f"{type(exc).__name__}: {exc}"[:2000])
    return reports


def launch_full(state, run_id: uuid.UUID, on_done: Callable[[list[dict]], None] | None = None) -> asyncio.Task:
    """Start ``run_full`` on the running loop, keeping a reference on ``state``."""
    tasks = getattr(state, "integrity_tasks", None)
    if tasks is None:
        tasks = state.integrity_tasks = set()
    task = asyncio.create_task(run_full(state.sessionmaker, run_id, on_done))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task
