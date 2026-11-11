"""URL datasets (#249): fetch a page or file once and keep it as a new version.

Never a live link: pages change, and lessons must stay checkable. Guarded
against server-side request forgery:
- off unless ``dataset_url_allow`` lists hosts (exact, or ``*.domain``);
- ``https`` only, and redirects (at most 5) only to allowed hosts;
- every host is resolved first and refused if any address isn't public
  (private, loopback, link-local, reserved, multicast);
- the connected peer address is checked again before the body is read, in
  case DNS changed between the check and the connection;
- 30 s in total and the text tier's size cap.
"""
from __future__ import annotations

import asyncio
import io
import ipaddress
import posixpath
import socket
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlparse

import httpx
from sqlalchemy import select

from server.models import AuditLog, Dataset, DatasetVersion
from server.services import dataset_store, datasets
from server.services.errors import ServiceError

MAX_REDIRECTS = 5
TIMEOUT_S = 30
EXT_FOR_TYPE = {
    "text/html": ".html", "text/markdown": ".md", "text/plain": ".txt", "text/csv": ".csv",
    "application/json": ".json", "application/xml": ".xml", "text/xml": ".xml",
    "application/pdf": ".pdf", "application/yaml": ".yaml",
}


def allow_list(cfg) -> list[str]:
    return [h.strip().lower() for h in (cfg.dataset_url_allow or "").split(",") if h.strip()]


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    for rule in allowed:
        if rule.startswith("*."):
            if host.endswith(rule[1:]) and host != rule[2:]:
                return True
        elif host == rule:
            return True
    return False


def _public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not (addr.is_private or addr.is_loopback or addr.is_link_local
                                   or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


async def _system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def check_url(url: str, allowed: list[str], resolve) -> str:
    """The host of an acceptable URL, or a 422/403 explaining why not."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ServiceError(422, "bad_url", "only https URLs can be snapshotted")
    if parsed.username or parsed.password:
        raise ServiceError(422, "bad_url", "don't put credentials in the URL")
    host = parsed.hostname or ""
    if not host_allowed(host, allowed):
        raise ServiceError(403, "host_not_allowed", f"{host!r} isn't on the allow list (LTMS_DATASET_URL_ALLOW)")
    try:
        ips = await resolve(host)
    except OSError:
        raise ServiceError(422, "unresolvable", f"{host!r} doesn't resolve") from None
    if not ips or not all(_public(ip) for ip in ips):
        raise ServiceError(403, "private_address", f"{host!r} resolves to a private or local address; refused")
    return host


def _key_for(url: str, content_type: str) -> str:
    path = unquote(urlparse(url).path or "")
    name = posixpath.basename(path.rstrip("/")) or "index"
    host = urlparse(url).hostname or "page"
    stem, ext = posixpath.splitext(name)
    want = EXT_FOR_TYPE.get(content_type)
    if not ext and want:
        name = name + want
    elif want and ext.lower() not in (want, ".htm") and content_type == "text/html":
        name = stem + ".html"
    key = f"{host}/{name}"
    return "".join(c if c.isalnum() or c in "._-/" else "_" for c in key)[:200]


async def _fetch(url: str, cfg, *, transport, resolve) -> tuple[str, str, bytes]:
    allowed = allow_list(cfg)
    limit = cfg.dataset_text_max_mb * 1024 * 1024
    async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_S, follow_redirects=False,
                                 headers={"User-Agent": "LTMS dataset snapshot"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url, allowed, resolve)
            async with client.stream("GET", url) as resp:
                stream = resp.extensions.get("network_stream")
                peer = stream.get_extra_info("server_addr") if stream is not None else None
                if peer and not _public(peer[0]):
                    raise ServiceError(403, "private_address", "the connection reached a private address; refused")
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise ServiceError(502, "bad_redirect", "a redirect without a Location")
                    url = urljoin(url, location)
                    continue
                if resp.status_code != 200:
                    raise ServiceError(502, "fetch_failed", f"the server answered HTTP {resp.status_code}")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > limit:
                        raise ServiceError(413, "too_large", f"over {cfg.dataset_text_max_mb} MB; use a catalog "
                                                             "integration for data this large")
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                return url, ctype, bytes(body)
    raise ServiceError(422, "too_many_redirects", f"more than {MAX_REDIRECTS} redirects")


async def snapshot(session, cfg, ds: Dataset, url, *, admin: str, transport=None, resolve=None) -> dict:
    """Fetch ``url`` into a pending version of a ``kind: url`` dataset."""
    if not allow_list(cfg):
        raise ServiceError(403, "url_disabled", "URL snapshots are off: set LTMS_DATASET_URL_ALLOW to the hosts "
                                                "LTMS may fetch from")
    if ds.kind != "url":
        raise ServiceError(422, "not_url", "snapshots go into a kind: url dataset")
    if not isinstance(url, str) or not url.strip() or len(url) > 2000:
        raise ServiceError(422, "bad_url", "url is required")
    if ds.status == "retired":
        raise ServiceError(409, "retired", "this dataset is retired")
    latest = await session.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
                                  .order_by(DatasetVersion.version.desc()).limit(1))
    if latest is not None and latest.status == "pending" and latest.item_count:
        raise ServiceError(409, "version_pending", f"version {latest.version} is waiting for approval")
    try:
        final, ctype, body = await asyncio.wait_for(
            _fetch(url.strip(), cfg, transport=transport, resolve=resolve or _system_resolve), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        raise ServiceError(504, "timeout", f"the fetch took more than {TIMEOUT_S} s") from None
    except httpx.HTTPError as exc:
        raise ServiceError(502, "fetch_failed", f"couldn't fetch: {type(exc).__name__}") from None

    v = latest if latest is not None and latest.status == "pending" else await datasets.add_version(
        session, ds, admin=admin)
    key = _key_for(final, ctype)
    sha, size, head = dataset_store.save_file(cfg.data_dir, io.BytesIO(body), len(body) + 1, head=b"")
    item = await datasets.add_item(session, cfg, v, key=key, filename=key, sha=sha, size=size, head=body[:8192])
    v.source_url = final
    v.snapshot_at = datetime.now(timezone.utc)
    await datasets.finish_items(session, ds, v)
    session.add(AuditLog(actor=admin, action="dataset_snapshot",
                         detail={"dataset": str(ds.id), "version": v.version, "url": final, "bytes": size}))
    return {"version": v.version, "item": item, "source_url": final, "snapshot_at": v.snapshot_at.isoformat()}
