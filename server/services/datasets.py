"""The fleet dataset catalog (issue #236, docs/datasets-design.md).

A dataset is created ``pending`` with an empty version 1. Versions are
quarantined (``pending``) until an admin approves them; approving the first
version approves the dataset. The current version is the newest approved one.
Every dataset's scope is ``fleet``.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Dataset, DatasetItem, DatasetRead, DatasetVersion
from server.services import dataset_store, extract
from server.models.dataset import DATASET_KINDS
from server.services.errors import ServiceError

MAX_NAME = 80
MAX_LICENCE = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        raise ServiceError(404, "not_found", "no such dataset") from None


def _audit(session, admin: str, action: str, detail: dict) -> None:
    session.add(AuditLog(actor=admin, action=action, detail=detail))


async def get_dataset(session: AsyncSession, dataset_id) -> Dataset:
    ds = await session.get(Dataset, _uuid(dataset_id))
    if ds is None:
        raise ServiceError(404, "not_found", "no such dataset")
    return ds


async def get_version(session: AsyncSession, ds: Dataset, version: int) -> DatasetVersion:
    v = await session.scalar(select(DatasetVersion).where(
        DatasetVersion.dataset_id == ds.id, DatasetVersion.version == version))
    if v is None:
        raise ServiceError(404, "not_found", f"no version {version}")
    return v


async def current_version(session: AsyncSession, ds: Dataset) -> DatasetVersion | None:
    """The newest approved version, or None."""
    return await session.scalar(select(DatasetVersion).where(
        DatasetVersion.dataset_id == ds.id, DatasetVersion.status == "approved")
        .order_by(DatasetVersion.version.desc()).limit(1))


async def create_dataset(session: AsyncSession, body: dict, *, admin: str) -> Dataset:
    name = (body.get("name") or "").strip()
    if not name or len(name) > MAX_NAME:
        raise ServiceError(422, "bad_name", f"name is required, at most {MAX_NAME} characters")
    kind = body.get("kind", "upload")
    if kind not in DATASET_KINDS:
        raise ServiceError(422, "bad_kind", f"kind must be one of {', '.join(DATASET_KINDS)}")
    licence = (body.get("licence") or "").strip()
    if not licence or len(licence) > MAX_LICENCE:
        raise ServiceError(422, "bad_licence", "licence is required: say how this data may be used")
    if await session.scalar(select(Dataset.id).where(Dataset.name == name)):
        raise ServiceError(409, "name_taken", f"a dataset named {name!r} already exists")
    ds = Dataset(name=name, description=(body.get("description") or "").strip() or None, kind=kind,
                 licence=licence, status="pending", added_by=admin)
    session.add(ds)
    await session.flush()
    session.add(DatasetVersion(dataset_id=ds.id, version=1, status="pending", item_count=0, size_bytes=0))
    _audit(session, admin, "dataset_create", {"dataset": str(ds.id), "name": name, "kind": kind})
    await session.flush()
    return ds


async def add_version(session: AsyncSession, ds: Dataset, *, admin: str) -> DatasetVersion:
    if ds.status == "retired":
        raise ServiceError(409, "retired", "this dataset is retired")
    last = await session.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == ds.id))
    v = DatasetVersion(dataset_id=ds.id, version=(last or 0) + 1, status="pending", item_count=0, size_bytes=0)
    session.add(v)
    ds.updated_at = _now()
    _audit(session, admin, "dataset_version", {"dataset": str(ds.id), "version": v.version})
    await session.flush()
    return v


async def approve_version(session: AsyncSession, ds: Dataset, version: int, *, admin: str) -> DatasetVersion:
    if ds.status == "retired":
        raise ServiceError(409, "retired", "this dataset is retired")
    v = await get_version(session, ds, version)
    if v.status == "approved":
        return v
    v.status, v.approved_by, v.approved_at = "approved", admin, _now()
    if ds.status == "pending":
        ds.status, ds.approved_by = "approved", admin
    ds.updated_at = _now()
    _audit(session, admin, "dataset_approve", {"dataset": str(ds.id), "version": version})
    await session.flush()
    return v


async def retire(session: AsyncSession, ds: Dataset, *, admin: str) -> Dataset:
    if ds.status != "retired":
        ds.status, ds.updated_at = "retired", _now()
        _audit(session, admin, "dataset_retire", {"dataset": str(ds.id)})
        await session.flush()
    return ds


def version_dict(v: DatasetVersion) -> dict:
    return {"version": v.version, "status": v.status, "item_count": v.item_count, "size_bytes": v.size_bytes,
            "manifest_hash": v.manifest_hash, "source_url": v.source_url,
            "snapshot_at": v.snapshot_at.isoformat() if v.snapshot_at else None,
            "approved_by": v.approved_by, "approved_at": v.approved_at.isoformat() if v.approved_at else None,
            "created_at": v.created_at.isoformat()}


async def dataset_dict(session: AsyncSession, ds: Dataset) -> dict:
    versions = list(await session.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
                                          .order_by(DatasetVersion.version)))
    current = max((v.version for v in versions if v.status == "approved"), default=None)
    return {"id": str(ds.id), "name": ds.name, "description": ds.description, "kind": ds.kind, "scope": ds.scope,
            "licence": ds.licence, "use": ds.use, "status": ds.status, "added_by": ds.added_by,
            "approved_by": ds.approved_by, "created_at": ds.created_at.isoformat(),
            "updated_at": ds.updated_at.isoformat(), "current_version": current,
            "versions": [version_dict(v) for v in versions]}


async def list_datasets(session: AsyncSession) -> list[dict]:
    rows = list(await session.scalars(select(Dataset).order_by(Dataset.name)))
    return [await dataset_dict(session, ds) for ds in rows]



# --------------------------------------------------------------------------- #
# Uploads (#237): the text tier
# --------------------------------------------------------------------------- #
MAX_KEY = 200


def item_key(prefix: str | None, filename: str) -> str:
    """``prefix/filename``: no ``..``, no leading ``/``, at most 200 characters."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    prefix = (prefix or "").strip().strip("/")
    key = f"{prefix}/{name}" if prefix else name
    parts = key.split("/")
    if not name or len(key) > MAX_KEY or any(p in ("", ".", "..") for p in parts):
        raise ServiceError(422, "bad_key", f"bad item key {key[:80]!r}: no '..', no empty parts, at most {MAX_KEY}")
    return key


async def refresh_manifest(session: AsyncSession, v: DatasetVersion) -> None:
    items = list(await session.scalars(select(DatasetItem).where(DatasetItem.version_id == v.id)
                                       .order_by(DatasetItem.key)))
    v.item_count = len(items)
    v.size_bytes = sum(i.size_bytes for i in items)
    lines = "".join(f"{i.key}:{i.sha256 or ''}\n" for i in items)
    v.manifest_hash = hashlib.sha256(lines.encode("utf-8")).hexdigest() if items else None


async def upload_items(session: AsyncSession, cfg, ds: Dataset, version: int, files, *,
                       prefix: str | None = None) -> list[dict]:
    """Store uploaded files into a pending version. Each file's result is reported separately."""
    v = await get_version(session, ds, version)
    if ds.status == "retired":
        raise ServiceError(409, "retired", "this dataset is retired")
    if v.status != "pending":
        raise ServiceError(409, "version_locked", "an approved version can't change; add a new version")
    max_bytes = cfg.dataset_text_max_mb * 1024 * 1024
    results = []
    for f in files:
        key = item_key(prefix, f.filename)
        if await session.scalar(select(DatasetItem.id).where(DatasetItem.version_id == v.id,
                                                             DatasetItem.key == key)):
            raise ServiceError(409, "key_exists", f"{key!r} is already in this version")
        try:
            sha, size, head = await dataset_store.save_stream(cfg.data_dir, f, max_bytes)
        except dataset_store.TooLarge:
            raise ServiceError(413, "too_large", f"{key}: over {cfg.dataset_text_max_mb} MB. Large files "
                               "arrive in a later release; use a catalog integration for big data.") from None
        media_type = extract.sniff(head, f.filename or key)
        if media_type not in extract.TEXT_TIER:
            raise ServiceError(415, "unsupported_type",
                               f"{key}: {media_type} isn't a text format (txt, md, html, csv, tsv, json, jsonl, "
                               "yaml, xml). Other types arrive in later releases.")
        text, meta = extract.extract(dataset_store.read_bytes(cfg.data_dir, sha), media_type)
        session.add(DatasetItem(version_id=v.id, key=key, media_type=media_type, size_bytes=size, sha256=sha,
                                tier="text", text=text, meta=meta))
        results.append({"key": key, "media_type": media_type, "size_bytes": size, "sha256": sha, "meta": meta})
    await session.flush()
    await refresh_manifest(session, v)
    ds.updated_at = _now()
    await session.flush()
    return results


async def list_items(session: AsyncSession, ds: Dataset, version: int) -> list[dict]:
    v = await get_version(session, ds, version)
    return [{"key": i.key, "media_type": i.media_type, "size_bytes": i.size_bytes, "sha256": i.sha256,
             "tier": i.tier, "meta": i.meta}
            for i in await session.scalars(select(DatasetItem).where(DatasetItem.version_id == v.id)
                                           .order_by(DatasetItem.key))]



# --------------------------------------------------------------------------- #
# Member reads and search (#238)
# --------------------------------------------------------------------------- #
MAX_QUERY = 200
FTS_CONFIG = "english"


async def _readable(session: AsyncSession, dataset_id) -> Dataset:
    """An approved dataset, or 404 (members never see pending or retired ones)."""
    try:
        ds = await session.get(Dataset, _uuid(dataset_id))
    except ServiceError:
        ds = None
    if ds is None or ds.status != "approved":
        raise ServiceError(404, "not_found", "no such dataset")
    return ds


async def _readable_version(session: AsyncSession, ds: Dataset, version: int | None) -> DatasetVersion:
    if version is None:
        v = await current_version(session, ds)
    else:
        v = await session.scalar(select(DatasetVersion).where(
            DatasetVersion.dataset_id == ds.id, DatasetVersion.version == version,
            DatasetVersion.status == "approved"))
    if v is None:
        raise ServiceError(404, "not_found", "no such version")
    return v


async def list_for_members(session: AsyncSession) -> list[dict]:
    out = []
    for ds in await session.scalars(select(Dataset).where(Dataset.status == "approved").order_by(Dataset.name)):
        v = await current_version(session, ds)
        if v is None:
            continue
        out.append({"id": str(ds.id), "name": ds.name, "description": ds.description, "licence": ds.licence,
                    "use": ds.use, "scope": ds.scope, "current_version": v.version, "item_count": v.item_count})
    return out


def _snippet(text: str, q: str, width: int = 160) -> str:
    i = text.lower().find(q.lower().split()[0]) if q.split() else -1
    start = max(0, i - width // 3) if i >= 0 else 0
    piece = " ".join(text[start:start + width].split())
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


async def search(session: AsyncSession, dataset_id, q: str, *, version: int | None = None,
                 limit: int = 20) -> dict:
    q = (q or "").strip()
    if not q or len(q) > MAX_QUERY:
        raise ServiceError(422, "bad_query", f"q is required, at most {MAX_QUERY} characters")
    limit = max(1, min(int(limit), 50))
    ds = await _readable(session, dataset_id)
    v = await _readable_version(session, ds, version)
    if session.bind.dialect.name == "postgresql":
        doc = func.to_tsvector(literal(FTS_CONFIG).cast(_regconfig()),
                               func.coalesce(DatasetItem.key, "") + " " + func.coalesce(DatasetItem.text, ""))
        query = func.websearch_to_tsquery(literal(FTS_CONFIG).cast(_regconfig()), q)
        rank = func.ts_rank(doc, query)
        headline = func.ts_headline(literal(FTS_CONFIG).cast(_regconfig()), func.coalesce(DatasetItem.text, ""),
                                    query, "MaxWords=30, MinWords=10")
        rows = (await session.execute(
            select(DatasetItem.key, headline, rank).where(DatasetItem.version_id == v.id, doc.op("@@")(query))
            .order_by(rank.desc(), DatasetItem.key).limit(limit))).all()
        hits = [{"key": k, "snippet": " ".join((h or "").split()), "rank": float(r)} for k, h, r in rows]
    else:
        like = f"%{q.lower()}%"
        rows = (await session.execute(
            select(DatasetItem.key, DatasetItem.text).where(
                DatasetItem.version_id == v.id,
                or_(func.lower(DatasetItem.text).like(like), func.lower(DatasetItem.key).like(like)))
            .order_by(DatasetItem.key).limit(limit))).all()
        hits = [{"key": k, "snippet": _snippet(t or "", q), "rank": 1.0} for k, t in rows]
    return {"dataset": str(ds.id), "version": v.version, "hits": hits}


def _regconfig():
    from sqlalchemy.dialects.postgresql import REGCONFIG
    return REGCONFIG


async def log_read(session: AsyncSession, *, member_id, ds: Dataset, version: int, key: str,
                   cycle: uuid.UUID | None, source: str = "served") -> bool:
    """Log a read unless the same member already read this item version in this cycle. True if logged."""
    stmt = select(DatasetRead.id).where(
        DatasetRead.member_id == member_id, DatasetRead.dataset_id == ds.id, DatasetRead.version == version,
        DatasetRead.item_key == key, DatasetRead.source == source)
    stmt = stmt.where(DatasetRead.cycle.is_(None) if cycle is None else DatasetRead.cycle == cycle)
    if cycle is not None and await session.scalar(stmt.limit(1)):
        return False
    session.add(DatasetRead(member_id=member_id, dataset_id=ds.id, version=version, item_key=key, cycle=cycle,
                            source=source, ts=_now()))
    await session.flush()
    return True


def parse_cycle(cycle: str | None) -> uuid.UUID | None:
    if cycle in (None, ""):
        return None
    try:
        return uuid.UUID(str(cycle))
    except ValueError:
        raise ServiceError(422, "bad_cycle", "cycle must be a UUID") from None


async def read_item(session: AsyncSession, *, member_id, dataset_id, key: str, version: int | None = None,
                    cycle: str | None = None) -> dict:
    cyc = parse_cycle(cycle)
    ds = await _readable(session, dataset_id)
    v = await _readable_version(session, ds, version)
    item = await session.scalar(select(DatasetItem).where(DatasetItem.version_id == v.id, DatasetItem.key == key))
    if item is None:
        raise ServiceError(404, "not_found", f"no item {key!r} in version {v.version}")
    await log_read(session, member_id=member_id, ds=ds, version=v.version, key=key, cycle=cyc)
    return {"dataset": str(ds.id), "version": v.version, "key": item.key, "media_type": item.media_type,
            "tier": item.tier, "sha256": item.sha256, "size_bytes": item.size_bytes, "text": item.text,
            "meta": item.meta}
