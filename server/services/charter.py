"""The fleet charter (#255): the fleet's governance, required reading.

The charter is a reserved dataset, ``fleet-charter`` (``kind: charter``), one
item ``charter.md`` per version. So reading it is a dataset read: logged,
and sent to the LRS as ``read-resource`` like any other.

- Publishing makes a new version, approved at once (the admin is the author).
  Its SHA-256 is the charter hash. Identical bytes change nothing.
- **Write gate**, when a charter exists: a member that joined after the
  current version was published must read it before its records are
  accepted; members that joined earlier have ``charter_grace_days`` to read
  it. Reading and recall are never blocked, only writes (Article 3.6).
- No charter published: no gate.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Dataset, DatasetItem, DatasetRead, DatasetVersion, Member
from server.services import dataset_store, datasets
from server.services.errors import ServiceError

NAME = "fleet-charter"
KEY = "charter.md"
MAX_BYTES = 256 * 1024


@dataclass
class Current:
    dataset: Dataset
    version: int
    hash: str
    published_at: datetime
    published_by: str | None
    markdown: str


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _dataset(session: AsyncSession) -> Dataset | None:
    return await session.scalar(select(Dataset).where(Dataset.name == NAME, Dataset.kind == "charter"))


async def current(session: AsyncSession) -> Current | None:
    ds = await _dataset(session)
    if ds is None:
        return None
    v = await session.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id,
                                                          DatasetVersion.status == "approved")
                             .order_by(DatasetVersion.version.desc()).limit(1))
    if v is None:
        return None
    item = await session.scalar(select(DatasetItem).where(DatasetItem.version_id == v.id, DatasetItem.key == KEY))
    return Current(ds, v.version, item.sha256, _as_utc(v.approved_at), v.approved_by, item.text or "")


async def publish(session: AsyncSession, cfg, markdown, *, admin: str) -> dict:
    if not isinstance(markdown, str) or not markdown.strip():
        raise ServiceError(422, "bad_charter", "the charter is markdown text and can't be empty")
    data = markdown.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ServiceError(413, "too_large", f"the charter is at most {MAX_BYTES // 1024} KB")
    sha = hashlib.sha256(data).hexdigest()
    cur = await current(session)
    if cur is not None and cur.hash == sha:
        return {**_public(cur), "published": False}
    ds = cur.dataset if cur is not None else await _dataset(session)
    now = datetime.now(timezone.utc)
    if ds is None:
        ds = Dataset(name=NAME, description="The fleet charter: required reading for every member.",
                     kind="charter", licence="Fleet charter", status="approved", added_by=admin,
                     approved_by=admin)
        session.add(ds)
        await session.flush()
        v = DatasetVersion(dataset_id=ds.id, version=1, status="pending", item_count=0, size_bytes=0)
        session.add(v)
        await session.flush()
    else:
        last = await session.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
                                    .order_by(DatasetVersion.version.desc()).limit(1))
        v = DatasetVersion(dataset_id=ds.id, version=last.version + 1, status="pending", item_count=0,
                           size_bytes=0)
        session.add(v)
        await session.flush()
    stored_sha, size, head = dataset_store.save_file(cfg.data_dir, io.BytesIO(data), MAX_BYTES + 1)
    session.add(DatasetItem(version_id=v.id, key=KEY, media_type="text/markdown", size_bytes=size,
                            sha256=stored_sha, tier="text", text=markdown, meta={"published_by": admin}))
    await datasets.refresh_manifest(session, v)
    v.status, v.approved_by, v.approved_at = "approved", admin, now
    ds.status, ds.updated_at = "approved", now
    session.add(AuditLog(actor=admin, action="charter_publish", detail={"version": v.version, "hash": sha}))
    await session.flush()
    return {**_public(await current(session)), "published": True}


def _public(cur: Current) -> dict:
    return {"version": cur.version, "hash": cur.hash, "published_at": cur.published_at.isoformat(),
            "published_by": cur.published_by}


async def read(session: AsyncSession, *, member_id) -> dict:
    """The current charter for a member, logged as a read."""
    cur = await current(session)
    if cur is None:
        raise ServiceError(404, "no_charter", "this fleet has no charter yet")
    await datasets.log_read(session, member_id=member_id, ds=cur.dataset, version=cur.version, key=KEY, cycle=None)
    return {**_public(cur), "markdown": cur.markdown}


async def _read_at(session: AsyncSession, cur: Current, member_ids) -> dict:
    rows = await session.execute(
        select(DatasetRead.member_id, DatasetRead.ts).where(
            DatasetRead.dataset_id == cur.dataset.id, DatasetRead.version == cur.version,
            DatasetRead.item_key == KEY, DatasetRead.member_id.in_(list(member_ids)))
        .order_by(DatasetRead.ts))
    out: dict = {}
    for mid, ts in rows:
        out.setdefault(mid, _as_utc(ts))
    return out


def _due(grace_days: int, cur: Current, member: Member) -> datetime:
    """When this member must have read the current version by."""
    joined = _as_utc(member.enrolled_at) if member.enrolled_at else None
    if joined is None or joined >= cur.published_at:
        return joined or cur.published_at  # joined after it was published: before the first write
    return cur.published_at + timedelta(days=grace_days)


async def status_for(session: AsyncSession, cfg, member: Member, now: datetime | None = None) -> dict | None:
    """``{version, hash, read, due}`` for one member, or None with no charter."""
    cur = await current(session)
    if cur is None:
        return None
    read_at = (await _read_at(session, cur, [member.id])).get(member.id)
    return {"version": cur.version, "hash": cur.hash, "read": read_at is not None,
            "due": None if read_at else _due(cfg.charter_grace_days, cur, member).isoformat()}


class Gate:
    """Checks authors in one ingest batch against the current charter."""

    def __init__(self, grace_days: int, cur: Current | None, read_ids: set, now: datetime) -> None:
        self.grace_days, self.cur, self.read_ids, self.now = grace_days, cur, read_ids, now

    @classmethod
    async def load(cls, session: AsyncSession, grace_days: int, members, now: datetime) -> "Gate":
        cur = await current(session)
        read_ids = set((await _read_at(session, cur, [m.id for m in members])).keys()) if cur else set()
        return cls(grace_days, cur, read_ids, now)

    def check(self, member: Member) -> tuple[str, str] | None:
        """None if ``member`` may write; else ``(code, message)``."""
        if self.cur is None or member.id in self.read_ids:
            return None
        due = _due(self.grace_days, self.cur, member)
        joined_before = member.enrolled_at is not None and _as_utc(member.enrolled_at) < self.cur.published_at
        if joined_before and self.now < due:
            return None
        return ("charter_unread",
                f"read the fleet charter (version {self.cur.version}, sha256 {self.cur.hash}) first: "
                "GET /api/charter. Reading and recall still work.")


async def admin_view(session: AsyncSession, cfg) -> dict:
    """Versions, the current text and hash, and who has and hasn't read it."""
    cur = await current(session)
    if cur is None:
        return {"current": None, "versions": [], "members": [], "grace_days": cfg.charter_grace_days}
    versions = []
    for v in await session.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == cur.dataset.id)
                                   .order_by(DatasetVersion.version.desc())):
        item = await session.scalar(select(DatasetItem).where(DatasetItem.version_id == v.id, DatasetItem.key == KEY))
        versions.append({"version": v.version, "hash": item.sha256 if item else None,
                         "published_at": _as_utc(v.approved_at).isoformat() if v.approved_at else None,
                         "published_by": v.approved_by})
    members = list(await session.scalars(select(Member).where(Member.status == "active").order_by(Member.name)))
    read_at = await _read_at(session, cur, [m.id for m in members])
    now = datetime.now(timezone.utc)
    rows = []
    for m in members:
        r = read_at.get(m.id)
        due = None if r else _due(cfg.charter_grace_days, cur, m)
        rows.append({"name": m.name, "read": r is not None, "read_at": r.isoformat() if r else None,
                     "due": due.isoformat() if due else None, "overdue": bool(due and due <= now)})
    return {"current": {**_public(cur), "markdown": cur.markdown}, "versions": versions, "members": rows,
            "grace_days": cfg.charter_grace_days}


async def version_text(session: AsyncSession, version: int) -> dict:
    cur = await current(session)
    if cur is None:
        raise ServiceError(404, "no_charter", "this fleet has no charter yet")
    v = await session.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == cur.dataset.id,
                                                          DatasetVersion.version == version))
    item = await session.scalar(select(DatasetItem).where(DatasetItem.version_id == v.id, DatasetItem.key == KEY)) \
        if v is not None else None
    if item is None:
        raise ServiceError(404, "not_found", f"no charter version {version}")
    return {"version": version, "hash": item.sha256, "markdown": item.text}
