"""The fleet charter (#255): the fleet's governance, required reading.

The charter is a reserved dataset, ``fleet-charter`` (``kind: charter``), one
item ``charter.md`` per version. So reading it is a dataset read: logged,
and sent to the LRS as ``read-resource`` like any other.

- Publishing makes a new version, approved at once (the admin is the author).
  Its SHA-256 is the charter hash. Identical bytes change nothing.
- Version control (#345): a publish carries an optional change note. **Make
  current** re-publishes an older version's exact bytes as a new version (same
  hash), so history is never rewritten. A superseded version can be
  **withdrawn** (hidden, never served, reads kept) or, if nobody has read it,
  **deleted** (an audit row keeps its number and hash). Reads count by hash:
  a member that read the same bytes under another version number has read it.
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

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Dataset, DatasetItem, DatasetRead, DatasetVersion, Member
from server.services import dataset_store, datasets
from server.services.errors import ServiceError

NAME = "fleet-charter"
KEY = "charter.md"
MAX_BYTES = 256 * 1024
MAX_NOTE = 200


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


def _note(note) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        raise ServiceError(422, "bad_note", "the change note is text")
    note = " ".join(note.split())
    if len(note) > MAX_NOTE:
        raise ServiceError(422, "bad_note", f"the change note is at most {MAX_NOTE} characters")
    return note or None


async def _versions(session: AsyncSession, ds: Dataset) -> list[tuple[DatasetVersion, DatasetItem | None]]:
    """Every remaining version, newest first, with its charter item."""
    rows = list(await session.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
                                      .order_by(DatasetVersion.version.desc())))
    items = {i.version_id: i for i in await session.scalars(
        select(DatasetItem).where(DatasetItem.version_id.in_([v.id for v in rows] or [-1]), DatasetItem.key == KEY))}
    return [(v, items.get(v.id)) for v in rows]


async def _deleted(session: AsyncSession) -> list[dict]:
    rows = await session.execute(select(AuditLog.actor, AuditLog.ts, AuditLog.detail)
                                 .where(AuditLog.action == "charter_delete").order_by(AuditLog.ts))
    return [{**(d or {}), "deleted_by": actor, "deleted_at": _as_utc(ts).isoformat()} for actor, ts, d in rows]


async def publish(session: AsyncSession, cfg, markdown, *, admin: str, note=None) -> dict:
    if not isinstance(markdown, str) or not markdown.strip():
        raise ServiceError(422, "bad_charter", "the charter is markdown text and can't be empty")
    data = markdown.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ServiceError(413, "too_large", f"the charter is at most {MAX_BYTES // 1024} KB")
    note = _note(note)
    sha = hashlib.sha256(data).hexdigest()
    cur = await current(session)
    if cur is not None and cur.hash == sha:
        return {**_public(cur), "published": False}
    if cur is not None:
        # The same bytes as an older (not withdrawn) version: that's Make current (#345).
        for v, item in await _versions(session, cur.dataset):
            if v.status == "approved" and item is not None and item.sha256 == sha:
                return await make_current(session, cfg, v.version, admin=admin)
    return await _publish_new(session, cfg, cur, markdown, admin=admin, note=note)


async def _publish_new(session: AsyncSession, cfg, cur: Current | None, markdown: str, *, admin: str,
                       note: str | None, reverted_from: int | None = None) -> dict:
    data = markdown.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    ds = cur.dataset if cur is not None else await _dataset(session)
    now = datetime.now(timezone.utc)
    if ds is None:
        ds = Dataset(name=NAME, description="The fleet charter: required reading for every member.",
                     kind="charter", licence="Fleet charter", status="approved", added_by=admin,
                     approved_by=admin)
        session.add(ds)
        await session.flush()
    last = await session.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == ds.id))
    gone = max((d.get("version") or 0 for d in await _deleted(session)), default=0)
    v = DatasetVersion(dataset_id=ds.id, version=max(last or 0, gone) + 1, status="pending", item_count=0,
                       size_bytes=0)
    session.add(v)
    await session.flush()
    stored_sha, size, head = dataset_store.save_file(cfg.data_dir, io.BytesIO(data), MAX_BYTES + 1)
    meta = {"published_by": admin, "note": note}
    if reverted_from is not None:
        meta["reverted_from"] = reverted_from
    session.add(DatasetItem(version_id=v.id, key=KEY, media_type="text/markdown", size_bytes=size,
                            sha256=stored_sha, tier="text", text=markdown, meta=meta))
    await datasets.refresh_manifest(session, v)
    v.status, v.approved_by, v.approved_at = "approved", admin, now
    ds.status, ds.updated_at = "approved", now
    detail = {"version": v.version, "hash": sha, "note": note}
    if reverted_from is not None:
        detail["reverted_from"] = reverted_from
    session.add(AuditLog(actor=admin, action="charter_publish", detail=detail))
    await session.flush()
    return {**_public(await current(session)), "published": True, "note": note, "reverted_from": reverted_from}


async def _version(session: AsyncSession, version: int) -> tuple[Current, DatasetVersion, DatasetItem]:
    cur = await current(session)
    if cur is None:
        raise ServiceError(404, "no_charter", "this fleet has no charter yet")
    v = await session.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == cur.dataset.id,
                                                          DatasetVersion.version == version))
    item = await session.scalar(select(DatasetItem).where(DatasetItem.version_id == v.id, DatasetItem.key == KEY)) \
        if v is not None else None
    if item is None:
        raise ServiceError(404, "not_found", f"no charter version {version}")
    return cur, v, item


async def make_current(session: AsyncSession, cfg, version: int, *, admin: str) -> dict:
    """Re-publish ``version``'s exact bytes as a new version (#345). Same bytes, same hash."""
    cur, v, item = await _version(session, version)
    if v.version == cur.version:
        raise ServiceError(409, "already_current", f"version {version} is the current version")
    if v.status != "approved":
        raise ServiceError(409, "withdrawn", f"version {version} is withdrawn; publish its text again instead")
    if item.sha256 == cur.hash:
        return {**_public(cur), "published": False}
    return await _publish_new(session, cfg, cur, item.text or "", admin=admin,
                              note=f"Reverted to version {version}", reverted_from=version)


async def _reads(session: AsyncSession, ds: Dataset) -> dict[int, int]:
    """Members that read each version (by version number)."""
    rows = await session.execute(
        select(DatasetRead.version, func.count(func.distinct(DatasetRead.member_id)))
        .where(DatasetRead.dataset_id == ds.id, DatasetRead.item_key == KEY).group_by(DatasetRead.version))
    return {int(v): int(n) for v, n in rows}


async def withdraw(session: AsyncSession, version: int, reason, *, admin: str) -> dict:
    """Hide a superseded version (#345): never served to members; its reads stay logged."""
    cur, v, item = await _version(session, version)
    if v.version == cur.version:
        raise ServiceError(409, "current", "the current version can't be withdrawn; make another version current first")
    if v.status == "withdrawn":
        raise ServiceError(409, "withdrawn", f"version {version} is already withdrawn")
    if not isinstance(reason, str) or not reason.strip():
        raise ServiceError(422, "bad_reason", "say why it's withdrawn")
    reason = " ".join(reason.split())
    if len(reason) > MAX_NOTE:
        raise ServiceError(422, "bad_reason", f"the reason is at most {MAX_NOTE} characters")
    now = datetime.now(timezone.utc)
    v.status = "withdrawn"
    item.meta = {**(item.meta or {}), "withdrawn": {"by": admin, "at": now.isoformat(), "reason": reason}}
    session.add(AuditLog(actor=admin, action="charter_withdraw",
                         detail={"version": version, "hash": item.sha256, "reason": reason}))
    await session.flush()
    return {"version": version, "status": "withdrawn", "reason": reason}


async def delete_version(session: AsyncSession, cfg, version: int, confirm, *, admin: str) -> dict:
    """Delete a superseded version nobody has read (#345). An audit row keeps its number and hash."""
    cur, v, item = await _version(session, version)
    if v.version == cur.version:
        raise ServiceError(409, "current", "the current version can't be deleted")
    if str(confirm).strip() != str(version):
        raise ServiceError(422, "confirm", f"type the version number ({version}) to confirm")
    if (await _reads(session, cur.dataset)).get(version):
        raise ServiceError(409, "has_reads", f"members have read version {version}; withdraw it instead")
    sha = item.sha256
    await session.execute(delete(DatasetItem).where(DatasetItem.version_id == v.id))
    await session.delete(v)
    session.add(AuditLog(actor=admin, action="charter_delete", detail={"version": version, "hash": sha}))
    await session.flush()
    in_use = await session.scalar(select(DatasetItem.id).where(DatasetItem.sha256 == sha).limit(1))
    dataset_store.remove_if_unused(cfg.data_dir, sha, in_use is not None)
    return {"version": version, "status": "deleted"}


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


async def _same_bytes(session: AsyncSession, cur: Current) -> list[int]:
    """Version numbers with the current version's hash (#345): reading any of them counts."""
    rows = await session.scalars(select(DatasetVersion.version).join(DatasetItem, DatasetItem.version_id == DatasetVersion.id)
                                 .where(DatasetVersion.dataset_id == cur.dataset.id, DatasetItem.key == KEY,
                                        DatasetItem.sha256 == cur.hash))
    return sorted(set(rows) | {cur.version})


async def _read_at(session: AsyncSession, cur: Current, member_ids) -> dict:
    """When each member first read the current charter's bytes, under any version number."""
    rows = await session.execute(
        select(DatasetRead.member_id, DatasetRead.ts).where(
            DatasetRead.dataset_id == cur.dataset.id, DatasetRead.version.in_(await _same_bytes(session, cur)),
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
    reads = await _reads(session, cur.dataset)
    versions = []
    for v, item in await _versions(session, cur.dataset):
        meta = (item.meta if item else None) or {}
        status = "current" if v.version == cur.version else "withdrawn" if v.status == "withdrawn" else "superseded"
        versions.append({"version": v.version, "hash": item.sha256 if item else None,
                         "published_at": _as_utc(v.approved_at).isoformat() if v.approved_at else None,
                         "published_by": v.approved_by, "note": meta.get("note"),
                         "reverted_from": meta.get("reverted_from"), "status": status,
                         "withdrawn": meta.get("withdrawn"), "reads": reads.get(v.version, 0)})
    for d in await _deleted(session):
        versions.append({"version": d.get("version"), "hash": d.get("hash"), "published_at": None,
                         "published_by": None, "note": None, "reverted_from": None, "status": "deleted",
                         "deleted_by": d["deleted_by"], "deleted_at": d["deleted_at"], "reads": 0})
    versions.sort(key=lambda x: x["version"] or 0, reverse=True)
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
    """Any remaining version's text, for admins (withdrawn ones too)."""
    cur, v, item = await _version(session, version)
    meta = item.meta or {}
    status = "current" if v.version == cur.version else "withdrawn" if v.status == "withdrawn" else "superseded"
    return {"version": version, "hash": item.sha256, "markdown": item.text, "note": meta.get("note"),
            "status": status}
