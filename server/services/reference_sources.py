"""Built-in reference sources an admin imports from the dashboard (#351).

A reference source is published material LTMS knows how to turn into a fleet
dataset, one item per entry, with its rights kept. Today: the I2IDL glossary.

- **Import latest**: LTMS fetches the source's published file itself, from
  that source's own host only, with the same SSRF guards as URL snapshots
  (https, public addresses, size cap, timeout). No allow-list setting needed:
  the host is fixed in code and the fetch only happens on an admin's action or
  the refresh job below.
- **Import from file**: the same, from a file the admin uploads, for closed
  networks.
- **Refresh**: once a source has been imported, the scheduler checks it every
  ``LTMS_REFERENCE_REFRESH_HOURS`` (default 168, weekly; 0 = off). A new
  publication becomes a *pending* version: an admin still approves it.

Re-importing a publication that's already in any version changes nothing.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from server.models import AuditLog, Dataset, DatasetItem, DatasetVersion
from server.services import dataset_store, datasets, i2idl_glossary, url_snapshot
from server.services.errors import ServiceError

log = logging.getLogger("ltms.reference_sources")
MAX_DOC_MB = 25


@dataclass(frozen=True)
class Source:
    key: str
    title: str
    url: str
    dataset: str
    description: str
    build: Callable[[dict], tuple[dict, list[dict]]]  # doc -> (publication, entries)
    entry_prefix: str = "concepts"

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""


SOURCES: dict[str, Source] = {
    "i2idl-glossary": Source(
        key="i2idl-glossary", title="I2IDL Glossary of Digital Learning", url=i2idl_glossary.GLOSSARY_URL,
        dataset=i2idl_glossary.DATASET, build=i2idl_glossary.build,
        description="Terms, definitions, relationships and source rights for digital learning, one item per "
                    "concept. Members search it and cite concepts by id."),
}


def get_source(key: str) -> Source:
    if key not in SOURCES:
        raise ServiceError(404, "not_found", f"no reference source {key!r}")
    return SOURCES[key]


def _marker(pub: dict) -> str:
    return f"publication/{pub['publicationVersion']}.json"


async def _dataset(session, src: Source) -> Dataset | None:
    return await session.scalar(select(Dataset).where(Dataset.name == src.dataset))


async def _imported(session, ds: Dataset) -> dict[str, tuple[int, str]]:
    """publicationVersion -> (dataset version, status) for every version that holds one."""
    rows = (await session.execute(
        select(DatasetItem.key, DatasetVersion.version, DatasetVersion.status)
        .join(DatasetVersion, DatasetVersion.id == DatasetItem.version_id)
        .where(DatasetVersion.dataset_id == ds.id, DatasetItem.key.like("publication/%")))).all()
    return {key[len("publication/"):-len(".json")]: (version, status) for key, version, status in rows}


async def status(session, cfg) -> list[dict]:
    out = []
    for src in SOURCES.values():
        ds = await _dataset(session, src)
        entry = {"key": src.key, "title": src.title, "url": src.url, "description": src.description,
                 "dataset_id": None, "publications": [], "refresh_hours": cfg.reference_refresh_hours}
        if ds is not None:
            pubs = await _imported(session, ds)
            entry["dataset_id"] = str(ds.id)
            entry["dataset_status"] = ds.status
            entry["publications"] = [{"publicationVersion": p, "version": v, "status": s}
                                     for p, (v, s) in sorted(pubs.items(), key=lambda kv: kv[1][0])]
        out.append(entry)
    return out


async def import_doc(session, cfg, key: str, doc, *, admin: str, origin: str) -> dict:
    """Import a parsed source document. ``origin`` is the URL or "upload: <filename>"."""
    src = get_source(key)
    try:
        pub, entries = src.build(doc)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ServiceError(422, "bad_source", f"this doesn't look like the {src.title}: {exc}") from None
    if not entries:
        raise ServiceError(422, "bad_source", f"no entries found in this {src.title} file")
    ds = await _dataset(session, src)
    if ds is None:
        ds = await datasets.create_dataset(session, {
            "name": src.dataset, "kind": "url", "licence": (pub.get("rights") or "Mixed rights; see each item")[:500],
            "description": f"{src.title}. {src.description} Imported from {src.url}."}, admin=admin)
    if ds.status == "retired":
        raise ServiceError(409, "retired", f"{src.dataset} is retired")
    found = (await _imported(session, ds)).get(pub["publicationVersion"])
    if found:
        return {"status": "unchanged", "dataset_id": str(ds.id), "version": found[0],
                "publicationVersion": pub["publicationVersion"], "items": len(entries)}
    latest = await session.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
                                  .order_by(DatasetVersion.version.desc()).limit(1))
    if latest is not None and latest.status == "pending":
        if latest.item_count:
            raise ServiceError(409, "version_pending",
                               f"version {latest.version} is waiting for approval: approve or retire it first")
        v = latest
    else:
        v = await datasets.add_version(session, ds, admin=admin)

    def put(key_: str, obj: dict) -> tuple[str, str, int, bytes]:
        body = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        sha, size, _ = dataset_store.save_file(cfg.data_dir, io.BytesIO(body), len(body) + 1)
        return key_, sha, size, body[:8192]

    for e in entries:
        k, sha, size, head = put(f"{src.entry_prefix}/{e['id']}.json", e)
        await datasets.add_item(session, cfg, v, key=k, filename=k.rsplit("/", 1)[-1], sha=sha, size=size, head=head)
    k, sha, size, head = put(_marker(pub), {**pub, "entries": len(entries), "imported_from": origin})
    await datasets.add_item(session, cfg, v, key=k, filename=k.rsplit("/", 1)[-1], sha=sha, size=size, head=head)
    if origin.startswith("https://"):
        v.source_url = origin
    v.snapshot_at = datetime.now(timezone.utc)
    await datasets.finish_items(session, ds, v)
    session.add(AuditLog(actor=admin, action="reference_import",
                         detail={"source": key, "dataset": str(ds.id), "version": v.version,
                                 "publicationVersion": pub["publicationVersion"], "items": len(entries),
                                 "origin": origin}))
    return {"status": "imported", "dataset_id": str(ds.id), "version": v.version,
            "publicationVersion": pub["publicationVersion"], "items": len(entries)}


def _parse(body: bytes, src: Source):
    try:
        return json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ServiceError(422, "bad_source", f"the {src.title} file isn't valid JSON") from None


async def import_latest(session, cfg, key: str, *, admin: str, transport=None, resolve=None) -> dict:
    """Fetch the source's published file from its own host and import it."""
    src = get_source(key)
    try:
        final, _ctype, body = await asyncio.wait_for(url_snapshot._fetch(
            src.url, cfg, transport=transport, resolve=resolve or url_snapshot._system_resolve,
            allowed=[src.host]), timeout=url_snapshot.TIMEOUT_S * 2)
    except asyncio.TimeoutError:
        raise ServiceError(504, "timeout", f"{src.host} didn't answer in time; try again, or import from a file") from None
    except httpx.HTTPError as exc:
        raise ServiceError(502, "fetch_failed", f"couldn't reach {src.host} ({type(exc).__name__}). On a closed "
                                                "network, import from a saved file instead.") from None
    return await import_doc(session, cfg, key, _parse(body, src), admin=admin, origin=final)


async def import_upload(session, cfg, key: str, body: bytes, filename: str, *, admin: str) -> dict:
    src = get_source(key)
    if len(body) > MAX_DOC_MB * 1024 * 1024:
        raise ServiceError(413, "too_large", f"over {MAX_DOC_MB} MB")
    return await import_doc(session, cfg, key, _parse(body, src), admin=admin, origin=f"upload: {filename}")


async def refresh_all(session, cfg, *, transport=None, resolve=None) -> dict:
    """Scheduler job: check each already-imported source for a new publication."""
    out = {}
    for src in SOURCES.values():
        if await _dataset(session, src) is None:
            continue  # never imported: an admin opts in by importing once
        try:
            res = await import_latest(session, cfg, src.key, admin="system:reference-refresh",
                                      transport=transport, resolve=resolve)
            out[src.key] = res["status"]
        except ServiceError as exc:
            out[src.key] = f"skipped: {exc.code}"
    return out
