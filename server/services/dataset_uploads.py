"""Resumable dataset uploads (#245): start, send 8 MB chunks, finish.

The bytes go to ``<data_dir>/datasets/uploads/<id>.part``. A chunk must start
where the last one ended (``Content-Range``), so a client that lost its place
asks for the upload's status and carries on from ``received_bytes``. Finishing
hashes the file into the content-addressed store and adds the item.
Unfinished uploads are deleted after 24 hours by a nightly job.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Dataset, DatasetUpload, DatasetVersion
from server.services import dataset_store, datasets
from server.services.errors import ServiceError

CHUNK_BYTES = 8 * 1024 * 1024
STALE_AFTER = timedelta(hours=24)
_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


def _dir(data_dir: str) -> Path:
    path = dataset_store.root(data_dir) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def part_path(data_dir: str, upload_id: uuid.UUID) -> Path:
    return _dir(data_dir) / f"{upload_id}.part"


def upload_dict(u: DatasetUpload) -> dict:
    return {"upload_id": str(u.id), "key": u.key, "size_bytes": u.size_bytes,
            "received_bytes": u.received_bytes, "chunk_bytes": CHUNK_BYTES,
            "created_at": u.created_at.isoformat()}


async def start(session: AsyncSession, cfg, ds: Dataset, version: int, *, filename, size, prefix=None,
                admin: str) -> DatasetUpload:
    v = await datasets.writable_version(session, ds, version)
    if not isinstance(filename, str) or not filename.strip():
        raise ServiceError(422, "bad_upload", "filename is required")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ServiceError(422, "bad_upload", "size must be a positive number of bytes")
    key = datasets.item_key(prefix, filename)
    if size > cfg.dataset_upload_max_mb * 1024 * 1024:
        raise datasets.too_large(cfg, key)
    await datasets._check_new_key(session, v, key)
    if await session.scalar(select(DatasetUpload.id).where(DatasetUpload.version_id == v.id,
                                                           DatasetUpload.key == key)):
        raise ServiceError(409, "upload_exists", f"an upload of {key!r} is already in progress; resume it")
    u = DatasetUpload(id=uuid.uuid4(), version_id=v.id, key=key, filename=filename, size_bytes=size,
                      received_bytes=0, created_by=admin)
    session.add(u)
    part_path(cfg.data_dir, u.id).write_bytes(b"")
    await session.flush()
    return u


async def get(session: AsyncSession, ds: Dataset, version: int, upload_id) -> tuple[DatasetVersion, DatasetUpload]:
    v = await datasets.get_version(session, ds, version)
    try:
        uid = uuid.UUID(str(upload_id))
    except ValueError:
        raise ServiceError(404, "not_found", "no such upload") from None
    u = await session.get(DatasetUpload, uid)
    if u is None or u.version_id != v.id:
        raise ServiceError(404, "not_found", "no such upload")
    return v, u


async def put_chunk(session: AsyncSession, cfg, ds: Dataset, version: int, upload_id, *,
                    content_range: str | None, body) -> DatasetUpload:
    """Append one chunk. ``body`` is an async iterator of bytes (the request stream)."""
    _, u = await get(session, ds, version, upload_id)
    m = _RANGE.match((content_range or "").strip())
    if not m:
        raise ServiceError(422, "bad_range", "send Content-Range: bytes <start>-<end>/<total>")
    first, last, total = (int(x) for x in m.groups())
    if total != u.size_bytes or last < first or last >= total:
        raise ServiceError(422, "bad_range", f"the range must lie within 0-{u.size_bytes - 1}")
    if first != u.received_bytes:
        raise ServiceError(409, "wrong_offset",
                           f"expected the chunk starting at byte {u.received_bytes}; resume from there")
    want = last - first + 1
    if want > CHUNK_BYTES:
        raise ServiceError(413, "chunk_too_large", f"chunks are at most {CHUNK_BYTES} bytes")
    got = 0
    path = part_path(cfg.data_dir, u.id)
    with open(path, "r+b") as out:
        out.seek(first)
        async for piece in body:
            got += len(piece)
            if got > want:
                break
            out.write(piece)
        out.truncate(first + min(got, want))
    if got != want:
        raise ServiceError(422, "bad_range", f"Content-Range says {want} bytes, the body had {got}")
    u.received_bytes = first + got
    u.updated_at = datetime.now(timezone.utc)
    return u


async def finish(session: AsyncSession, cfg, ds: Dataset, version: int, upload_id) -> dict:
    v, u = await get(session, ds, version, upload_id)
    v = await datasets.writable_version(session, ds, version)
    if u.received_bytes != u.size_bytes:
        raise ServiceError(409, "incomplete", f"received {u.received_bytes} of {u.size_bytes} bytes")
    path = part_path(cfg.data_dir, u.id)
    digest, head = hashlib.sha256(), b""
    with open(path, "rb") as f:
        while chunk := f.read(dataset_store.CHUNK):
            if len(head) < 8192:
                head += chunk[: 8192 - len(head)]
            digest.update(chunk)
    sha = digest.hexdigest()
    final = dataset_store.path_for(cfg.data_dir, sha)
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        os.unlink(path)
    else:
        os.replace(path, final)
    result = await datasets.add_item(session, cfg, v, key=u.key, filename=u.filename, sha=sha,
                                     size=u.size_bytes, head=head)
    await session.delete(u)
    await datasets.finish_items(session, ds, v)
    return result


async def purge_stale(session: AsyncSession, cfg, now: datetime | None = None) -> dict:
    """Delete uploads untouched for 24 hours (nightly). Their partial files go too."""
    cutoff = (now or datetime.now(timezone.utc)) - STALE_AFTER
    stale = list(await session.scalars(select(DatasetUpload).where(DatasetUpload.updated_at < cutoff)))
    for u in stale:
        p = part_path(cfg.data_dir, u.id)
        if p.exists():
            p.unlink()
        await session.delete(u)
    if stale:
        session.add(AuditLog(actor="system:datasets", action="dataset_upload_expire",
                             detail={"count": len(stale), "keys": [u.key for u in stale][:50]}))
    return {"expired": len(stale)}
