"""Ingest: verify a record chain, validate, insert header + payload, ack.

This is the server's write path (plan §"Server verify order"). Each record in a
batch is verified against the previously acknowledged header for the same member
using :func:`server.services.chain.verify_chain`; only fully-valid, in-order
records are inserted into the permanent ``records`` header table and the mutable
``recordpayload`` table, and the member's ``last_ack_seq`` is advanced.

The batch is atomic: if any record fails verification, the whole batch is rolled
back and no records are inserted. This keeps the chain gap-free — a single bad
record rejects the batch rather than leaving a hole that later records cannot
close.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.member import Member
from server.models.record import RecordHeader
from server.models.recordpayload import RecordPayload
from server.security.key import Ed25519PublicKey
from server.services.chain import ChainError, verify_chain


def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (accepting a trailing ``Z``) as UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_header(record: dict) -> dict:
    """Build the dict :func:`verify_chain` expects from an incoming record.

    Hex-encoded chain fields become raw bytes; ``ts`` becomes a datetime.
    """
    return {
        "seq": record["seq"],
        "ts": _parse_ts(record["ts"]),
        "type": record["type"],
        "body_hash": bytes.fromhex(record["body_hash"]),
        "sig": bytes.fromhex(record["sig"]),
        "hash": bytes.fromhex(record["hash"]),
        "prev_hash": bytes.fromhex(record["prev_hash"]),
        "agent": record["agent"],
    }


def _last_header(session: AsyncSession, member_id) -> RecordHeader | None:
    """Return the highest-``seq`` header for a member, or ``None``."""
    stmt = (
        select(RecordHeader)
        .where(RecordHeader.member_id == member_id)
        .order_by(RecordHeader.seq.desc())
        .limit(1)
    )
    return session.scalar(stmt)


def _verify_one(
    record: dict,
    prev_header: RecordHeader | None,
    *,
    verifying: Ed25519PublicKey,
    now: datetime,
    replay_sec: int,
    skew_sec: int,
) -> None:
    """Verify a single normalized record against the previous header."""
    header = _normalize_header(record)
    prev_view: dict | None = (
        {"seq": prev_header.seq, "hash": prev_header.hash}
        if prev_header is not None
        else None
    )
    verify_chain(
        record,
        header=header,
        prev_header=prev_view,
        verifying=verifying,
        now=now,
        replay_sec=replay_sec,
        skew_sec=skew_sec,
        seq=record["seq"],
        type_=record["type"],
        agent=record["agent"],
    )


async def ingest_batch(
    session: AsyncSession,
    member: Member,
    records: list[dict],
    *,
    now: datetime | None = None,
    replay_sec: int = 45,
    skew_sec: int = 3600,
) -> list[dict]:
    """Ingest ``records`` for ``member`` and return per-record outcomes.

    The batch is atomic: a :class:`ChainError` on any record rolls the whole
    batch back and returns ``[{"status": "rejected", "code": <code>}, ...]`` for
    the records already processed, with no rows inserted.

    Returns a list of outcomes, one per input record, each ``{"seq", "status",
    "code", "message"}``. On full success every outcome is ``"accepted"`` and the
    member's ``last_ack_seq`` is advanced to the highest ingested seq.
    """
    if member.status != "active":
        raise ChainError("not_active", f"member status is {member.status!r}")
    if "contribute" not in (member.permissions or []):
        raise ChainError("no_contribute", "member lacks the 'contribute' permission")

    now = now or datetime.now(timezone.utc)
    verifying = Ed25519PublicKey(member.public_key)

    prev_header = _last_header(session, member.id)
    outcomes: list[dict] = []

    for record in records:
        seq = record["seq"]
        try:
            _verify_one(
                record,
                prev_header,
                verifying=verifying,
                now=now,
                replay_sec=replay_sec,
                skew_sec=skew_sec,
            )
        except ChainError as exc:
            session.rollback()
            outcomes.append(
                {"seq": seq, "status": "rejected", "code": exc.code, "message": exc.message}
            )
            return outcomes

        # Persist header + payload for the accepted record.
        header = RecordHeader(
            member_id=member.id,
            agent=record["agent"],
            seq=record["seq"],
            ts=_parse_ts(record["ts"]),
            type=record["type"],
            summary=record.get("summary"),
            refs=record.get("refs") or {},
            place=record.get("place"),
            peers=record.get("peers") or [],
            body_hash=bytes.fromhex(record["body_hash"]),
            sig=bytes.fromhex(record["sig"]),
            hash=bytes.fromhex(record["hash"]),
            prev_hash=bytes.fromhex(record["prev_hash"]),
        )
        session.add(header)
        await session.flush()

        payload = RecordPayload(
            header_id=header.id,
            member_id=member.id,
            seq=record["seq"],
            status=record.get("status"),
            place=record.get("place"),
            body=record.get("body") or {},
            media_type=record.get("media_type") or [],
            ts=_parse_ts(record["ts"]),
        )
        session.add(payload)

        outcomes.append({"seq": seq, "status": "accepted"})
        prev_header = header

    member.last_ack_seq = max((o["seq"] for o in outcomes), default=member.last_ack_seq)
    return outcomes
