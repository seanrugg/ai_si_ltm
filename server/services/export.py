"""Admin export of records as JSONL or CSV (issue #153).

JSONL is a complete, verifiable copy: each line carries the exact signed
header string, its hash and signature, the member's public key and the payload,
so anyone can re-check it with ``ltms_contract``. CSV is for spreadsheets.

Records an admin deleted (#173) are exported as tombstones after the records,
in agent/seq order, so chains still verify across the gaps. A tombstone has no
type, time or swarm, so tombstones are included only when the export is
unfiltered or filtered by member alone.

The export queries ``Record`` directly (everything an admin can see), pages by
``Record.id`` in batches of 500, and streams from its own session so the
request's transaction isn't held open.
"""
from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ltms_contract import RECORD_TYPES, ContractError, parse_ts
from server.models import Member, Record, RecordPayload, RecordProvenance, RecordTombstone, Swarm
from server.services.errors import ServiceError

BATCH = 500
FORMATS = {"jsonl": "application/x-ndjson", "csv": "text/csv; charset=utf-8"}
CSV_COLUMNS = ["agent", "seq", "ts", "type", "summary", "place", "outcome", "peers", "tags", "refs",
               "swarm", "relay", "received_at", "hash", "prev_hash", "payload_hash", "sig",
               "payload_purged", "payload", "deleted"]
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ExportFilter:
    agent: str | None = None
    swarm: str | None = None
    types: tuple[str, ...] | None = None
    since: datetime | None = None
    until: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "agent": self.agent,
            "swarm": self.swarm,
            "type": list(self.types) if self.types else None,
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
        }


def _parse_time(name: str, value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_ts(value)
    except ContractError:
        pass
    try:
        parsed = datetime.fromisoformat(value)  # also accepts a plain date
    except ValueError:
        raise ServiceError(422, "bad_filter", f"{name} must be an ISO timestamp") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_filter(*, fmt: str, agent: str | None, swarm: str | None, type_: str | None,
                 since: str | None, until: str | None) -> ExportFilter:
    if fmt not in FORMATS:
        raise ServiceError(422, "bad_format", "format must be jsonl or csv")
    types = None
    if type_:
        types = tuple(t.strip() for t in type_.split(",") if t.strip())
        unknown = [t for t in types if t not in RECORD_TYPES]
        if unknown or not types:
            raise ServiceError(422, "bad_filter", f"unknown record type: {', '.join(unknown) or type_}")
    f = ExportFilter(agent=agent or None, swarm=swarm or None, types=types,
                     since=_parse_time("since", since), until=_parse_time("until", until))
    if f.since and f.until and f.since > f.until:
        raise ServiceError(422, "bad_filter", "since must not be after until")
    return f


def _query(f: ExportFilter, after_id: int):
    stmt = select(Record).where(Record.id > after_id)
    if f.agent:
        stmt = stmt.where(Record.agent == f.agent)
    if f.types:
        stmt = stmt.where(Record.type.in_(f.types))
    if f.since:
        stmt = stmt.where(Record.ts >= f.since)
    if f.until:
        stmt = stmt.where(Record.ts <= f.until)
    if f.swarm:
        stmt = (stmt.join(RecordProvenance, RecordProvenance.record_id == Record.id)
                .join(Swarm, Swarm.id == RecordProvenance.swarm_id)
                .where(Swarm.name == f.swarm))
    return stmt.order_by(Record.id).limit(BATCH)


async def _page_context(session: AsyncSession, rows: list[Record], keys: dict) -> tuple[dict, dict]:
    ids = [r.id for r in rows]
    payloads = {p.record_id: p for p in await session.scalars(
        select(RecordPayload).where(RecordPayload.record_id.in_(ids)))}
    provs = {p.record_id: p for p in await session.scalars(
        select(RecordProvenance).where(RecordProvenance.record_id.in_(ids)))}
    swarm_ids = {p.swarm_id for p in provs.values() if p.swarm_id}
    member_ids = {p.relay_member_id for p in provs.values() if p.relay_member_id} | {r.member_id for r in rows}
    swarm_names = {s.id: s.name for s in await session.scalars(select(Swarm).where(Swarm.id.in_(swarm_ids)))} \
        if swarm_ids else {}
    missing = member_ids - keys.keys()
    if missing:
        for m in await session.scalars(select(Member).where(Member.id.in_(missing))):
            keys[m.id] = (m.name, bytes(m.public_key).hex())
    context = {}
    for r in rows:
        p = provs.get(r.id)
        context[r.id] = {
            "swarm": swarm_names.get(p.swarm_id) if p and p.swarm_id else None,
            "relay": keys[p.relay_member_id][0] if p and p.relay_member_id in keys else None,
        }
    return payloads, context


async def _records(sessionmaker: async_sessionmaker, f: ExportFilter):
    """Yield (record, payload_row_or_None, provenance, public_key_hex) in id order."""
    keys: dict = {}
    after = 0
    while True:
        async with sessionmaker() as session:
            rows = list(await session.scalars(_query(f, after)))
            if not rows:
                return
            payloads, context = await _page_context(session, rows, keys)
        for r in rows:
            yield r, payloads.get(r.id), context[r.id], keys[r.member_id][1]
        after = rows[-1].id
        if len(rows) < BATCH:
            return


async def _tombstones(sessionmaker: async_sessionmaker, f: ExportFilter) -> list[RecordTombstone]:
    if f.swarm or f.types or f.since or f.until:
        return []
    stmt = select(RecordTombstone).order_by(RecordTombstone.agent, RecordTombstone.seq)
    if f.agent:
        stmt = stmt.where(RecordTombstone.agent == f.agent)
    async with sessionmaker() as session:
        return list(await session.scalars(stmt))


def _tombstone_line(t: RecordTombstone) -> dict:
    return {"deleted": True, "agent": t.agent, "seq": t.seq, "hash": t.hash, "prev_hash": t.prev_hash,
            "deleted_at": _iso(t.deleted_at), "deleted_by": t.deleted_by, "reason": t.reason}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def stream_jsonl(sessionmaker, f: ExportFilter, exported_at: datetime) -> AsyncIterator[bytes]:
    head = {"kind": "ltms-export", "version": 1, "exported_at": _iso(exported_at), "filters": f.as_dict()}
    yield (json.dumps(head) + "\n").encode("utf-8")
    async for r, payload, ctx, public_key in _records(sessionmaker, f):
        line = {
            "header": r.header_raw.decode("utf-8"),
            "hash": r.hash,
            "sig": r.sig,
            "public_key": public_key,
            "payload": json.loads(payload.payload_raw) if payload is not None else None,
            "payload_purged": payload is None,
            "provenance": {"swarm": ctx["swarm"], "relay": ctx["relay"], "received_at": _iso(r.received_at)},
        }
        yield (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")
    for t in await _tombstones(sessionmaker, f):
        yield (json.dumps(_tombstone_line(t), ensure_ascii=False) + "\n").encode("utf-8")


def safe_cell(value) -> str:
    """Neutralize spreadsheet formula injection: a leading ' on risky cells."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _json_or_empty(value) -> str:
    return "" if value is None else json.dumps(value, ensure_ascii=False)


async def stream_csv(sessionmaker, f: ExportFilter) -> AsyncIterator[bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf)

    def flush() -> bytes:
        data = buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate()
        return data

    writer.writerow(CSV_COLUMNS)
    yield flush()
    async for r, payload, ctx, _ in _records(sessionmaker, f):
        row = [r.agent, r.seq, _iso(r.ts), r.type, r.summary, r.place, r.outcome,
               _json_or_empty(r.peers), _json_or_empty(r.tags), _json_or_empty(r.refs),
               ctx["swarm"], ctx["relay"], _iso(r.received_at), r.hash, r.prev_hash, r.payload_hash, r.sig,
               "true" if payload is None else "false",
               "" if payload is None else payload.payload_raw.decode("utf-8"), "false"]
        writer.writerow([safe_cell(v) for v in row])
        yield flush()
    for t in await _tombstones(sessionmaker, f):
        row = {c: "" for c in CSV_COLUMNS}
        row.update(agent=t.agent, seq=t.seq, hash=t.hash, prev_hash=t.prev_hash, deleted="true",
                   summary=f"deleted by {t.deleted_by} on {_iso(t.deleted_at)}" + (f": {t.reason}" if t.reason else ""))
        writer.writerow([safe_cell(row[c]) for c in CSV_COLUMNS])
        yield flush()
