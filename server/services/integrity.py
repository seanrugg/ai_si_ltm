"""Full chain re-verification from stored bytes.

For each record, in seq order:
  * seq is contiguous from 1
  * SHA-256(header_raw) == hash, and the signature over it verifies
  * prev_hash links to the previous record's hash (genesis for seq 1)
  * the queryable columns agree with header_raw (catches edits that bypass the trigger)
  * if the payload is still stored, SHA-256(payload_raw) == payload_hash
Finally the member's chain head (last_ack_seq / last_hash) must match the last record.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ltms_contract import GENESIS_HASH, record_signing_message, sha256, verify_signature
from server.models import Member, Record, RecordPayload

_COLUMN_FIELDS = ("agent", "seq", "type", "prev_hash", "payload_hash", "summary", "place", "outcome")


async def verify_member(session: AsyncSession, member: Member, *, batch: int = 500) -> dict:
    report = {
        "member": member.name,
        "member_id": str(member.id),
        "status": "ok",
        "records": 0,
        "payloads_present": 0,
        "payloads_purged": 0,
        "head_seq": member.last_ack_seq,
        "error": None,
    }

    def fail(seq: int | None, code: str, message: str) -> dict:
        report["status"] = "broken"
        report["error"] = {"seq": seq, "code": code, "message": message}
        return report

    expected_seq, prev = 1, GENESIS_HASH
    while True:
        rows = (
            await session.execute(
                select(Record, RecordPayload)
                .outerjoin(RecordPayload, RecordPayload.record_id == Record.id)
                .where(Record.member_id == member.id, Record.seq >= expected_seq)
                .order_by(Record.seq)
                .limit(batch)
            )
        ).all()
        if not rows:
            break
        for rec, payload in rows:
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
        if len(rows) < batch:
            break

    if report["records"] == 0:
        report["status"] = "empty"
    if member.last_ack_seq != expected_seq - 1 or member.last_hash != prev:
        return fail(member.last_ack_seq, "head", "member chain head does not match the last stored record")
    return report


async def verify_all(session: AsyncSession) -> list[dict]:
    members = await session.scalars(select(Member).where(Member.status.in_(("active", "revoked"))).order_by(Member.name))
    return [await verify_member(session, m) for m in members]
