"""The fleet dataset catalog (issue #236, docs/datasets-design.md).

A dataset is created ``pending`` with an empty version 1. Versions are
quarantined (``pending``) until an admin approves them; approving the first
version approves the dataset. The current version is the newest approved one.
Every dataset's scope is ``fleet``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Dataset, DatasetVersion
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
