"""Catalog integrations (#248): data and models that live outside LTMS.

LTMS keeps where they are and what they're called, and never fetches from
them or relays calls to them. Members fetch from the source themselves and
report what they read (``POST /api/members/me/reads``), so reads of external
items are logged and sent to the LRS like any other.

``credential_env`` is the name of an environment variable on the member's
side (``HF_TOKEN``), never a secret: LTMS stores no credential for an
integration.
"""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, CatalogIntegration, Dataset, DatasetItem, DatasetVersion
from server.models.dataset import INTEGRATION_KINDS
from server.services import datasets
from server.services.errors import ServiceError

ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
MODEL_KINDS = ("ollama", "openai")
URL_SCHEMES = {"http": ("http", "https"), "mcp": ("http", "https"), "ollama": ("http", "https"),
               "openai": ("http", "https"), "huggingface": ("http", "https"), "s3": ("s3", "http", "https")}
MAX_LOCATOR = 2000
MAX_ITEMS = 500
# An AWS access key id fits the variable-name pattern; refuse it so one is never
# stored by mistake. (Lower-case tokens like sk-…, hf_… already fail ENV_NAME.)
SECRETISH = re.compile(r"^(AKIA|ASIA)[0-9A-Z]{16}$")


def integration_dict(i: CatalogIntegration) -> dict:
    return {"id": str(i.id), "name": i.name, "kind": i.kind, "base_url": i.base_url,
            "credential_env": i.credential_env, "description": i.description,
            "created_by": i.created_by, "created_at": i.created_at.isoformat()}


def public_dict(i: CatalogIntegration) -> dict:
    """What members get: enough to fetch, nothing about who added it."""
    return {"name": i.name, "kind": i.kind, "base_url": i.base_url, "credential_env": i.credential_env}


def _uuid(value) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ServiceError(404, "not_found", "no such integration") from None


async def get(session: AsyncSession, ref) -> CatalogIntegration:
    """By id or name."""
    found = None
    try:
        found = await session.get(CatalogIntegration, uuid.UUID(str(ref)))
    except ValueError:
        pass
    if found is None:
        found = await session.scalar(select(CatalogIntegration).where(CatalogIntegration.name == str(ref)))
    if found is None:
        raise ServiceError(404, "not_found", "no such integration")
    return found


async def create(session: AsyncSession, body: dict, *, admin: str) -> CatalogIntegration:
    name = (body.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ServiceError(422, "bad_name", "name is required, at most 80 characters")
    kind = body.get("kind")
    if kind not in INTEGRATION_KINDS:
        raise ServiceError(422, "bad_kind", f"kind must be one of {', '.join(INTEGRATION_KINDS)}")
    base_url = (body.get("base_url") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in URL_SCHEMES[kind] or not (parsed.netloc or parsed.path):
        raise ServiceError(422, "bad_url", f"base_url must be a {' or '.join(URL_SCHEMES[kind])} URL")
    if parsed.username or parsed.password or "token=" in (parsed.query or "").lower() \
            or "key=" in (parsed.query or "").lower():
        raise ServiceError(422, "credential_in_url", "don't put credentials in the URL; use credential_env")
    env = body.get("credential_env")
    if env in ("", None):
        env = None
    elif not isinstance(env, str) or not ENV_NAME.match(env) or SECRETISH.search(env):
        raise ServiceError(422, "bad_credential_env",
                           "credential_env is the NAME of an environment variable on the member, "
                           "like HF_TOKEN, never the secret itself")
    if await session.scalar(select(CatalogIntegration.id).where(CatalogIntegration.name == name)):
        raise ServiceError(409, "name_taken", f"an integration named {name!r} already exists")
    i = CatalogIntegration(name=name, kind=kind, base_url=base_url, credential_env=env,
                           description=(body.get("description") or "").strip() or None, created_by=admin)
    session.add(i)
    await session.flush()
    session.add(AuditLog(actor=admin, action="integration_create",
                         detail={"integration": str(i.id), "name": name, "kind": kind}))
    return i


async def list_all(session: AsyncSession) -> list[dict]:
    return [integration_dict(i) for i in
            await session.scalars(select(CatalogIntegration).order_by(CatalogIntegration.name))]


async def delete(session: AsyncSession, ref, *, admin: str) -> None:
    i = await get(session, ref)
    used = await session.scalar(select(Dataset.name).where(Dataset.integration_id == i.id).limit(1))
    if used:
        raise ServiceError(409, "in_use", f"dataset {used!r} uses this integration; retire or change it first")
    await session.delete(i)
    session.add(AuditLog(actor=admin, action="integration_delete", detail={"integration": str(i.id), "name": i.name}))


async def add_external_items(session: AsyncSession, ds: Dataset, version: int, items: list) -> list[dict]:
    """Add ``tier: external`` items to a pending version of an integration dataset.

    Each item: ``{key, locator, media_type?, size_bytes?, model?}``. ``locator``
    is a URL, bucket/key, repo id or model name; ``model`` is required for
    ollama and openai integrations (model files are integrations only).
    """
    if ds.kind != "integration" or ds.integration_id is None:
        raise ServiceError(422, "not_integration", "external items belong to a kind: integration dataset")
    integ = await session.get(CatalogIntegration, ds.integration_id)
    v = await datasets.writable_version(session, ds, version)
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ServiceError(422, "bad_items", f"items must be a list of 1 to {MAX_ITEMS} entries")
    out = []
    for n, it in enumerate(items):
        if not isinstance(it, dict):
            raise ServiceError(422, "bad_items", f"items[{n}] must be an object")
        key = str(it.get("key") or "").strip()
        if not key or len(key) > 200 or ".." in key.split("/") or key.startswith("/"):
            raise ServiceError(422, "bad_key", f"items[{n}].key is required, a relative path of at most 200 characters")
        locator = it.get("locator")
        if not isinstance(locator, str) or not locator.strip() or len(locator) > MAX_LOCATOR:
            raise ServiceError(422, "bad_locator", f"items[{n}].locator is required (at most {MAX_LOCATOR} characters)")
        meta: dict = {"locator": locator.strip()}
        model = it.get("model")
        if integ.kind in MODEL_KINDS:
            if not isinstance(model, str) or not model.strip():
                raise ServiceError(422, "bad_model", f"items[{n}].model is required for {integ.kind} integrations")
        if isinstance(model, str) and model.strip():
            meta["model"] = model.strip()
        size = it.get("size_bytes") or 0
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ServiceError(422, "bad_items", f"items[{n}].size_bytes must be a whole number")
        await datasets._check_new_key(session, v, key)
        media_type = str(it.get("media_type") or "application/octet-stream")[:100]
        session.add(DatasetItem(version_id=v.id, key=key, media_type=media_type, size_bytes=size, sha256=None,
                                tier="external", text=None, meta=meta))
        out.append({"key": key, "tier": "external", "media_type": media_type, "meta": meta})
    await datasets.finish_items(session, ds, v)
    return out


async def integration_of(session: AsyncSession, ds: Dataset) -> dict | None:
    if ds.integration_id is None:
        return None
    i = await session.get(CatalogIntegration, ds.integration_id)
    return public_dict(i) if i else None


async def report_read(session: AsyncSession, *, member_id, body: dict) -> dict:
    """A member read an item itself (usually an external one) and says so (#248)."""
    if not isinstance(body, dict):
        raise ServiceError(422, "bad_read", "send {dataset, version, item, cycle?}")
    version, item = body.get("version"), body.get("item")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1 or not isinstance(item, str):
        raise ServiceError(422, "bad_read", "version must be a positive number and item a key")
    cyc = datasets.parse_cycle(body.get("cycle"))
    ds = await datasets._readable(session, body.get("dataset"))
    v = await session.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id,
                                                          DatasetVersion.version == version))
    if v is None or v.status != "approved":
        raise ServiceError(404, "not_found", f"no approved version {version}")
    if await session.scalar(select(DatasetItem.id).where(DatasetItem.version_id == v.id,
                                                         DatasetItem.key == item)) is None:
        raise ServiceError(404, "not_found", f"no item {item!r} in version {version}")
    logged = await datasets.log_read(session, member_id=member_id, ds=ds, version=version, key=item, cycle=cyc,
                                     source="reported")
    return {"logged": logged}
