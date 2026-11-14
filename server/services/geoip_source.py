"""The city database behind network location estimates (#388).

An admin turns estimates on from Settings → Location estimates. LTMS keeps one
MaxMind-format (MMDB) city database under ``<data_dir>/geoip/``:

- **Download**: LTMS fetches the current monthly *DB-IP IP to City Lite* file
  (CC BY 4.0) itself, from DB-IP's download host only, with the same guards as
  URL snapshots: https, public addresses only, redirects only to that host,
  size and time limits. The previous month's file is tried when this month's
  isn't published yet.
- **Upload**: an ``.mmdb`` or ``.mmdb.gz`` the admin provides, for closed
  networks or another provider's file. The admin states its provider and licence.
- **Refresh**: in download mode, a scheduled job fetches a new file every
  ``LTMS_GEOIP_REFRESH_DAYS`` (default 30; 0 = off). A file that fails the
  checks is never installed: the previous one stays.
- **Turn off**: estimates stop; the file is kept unless the admin deletes it.

Every new file is decompressed with a cap (no decompression bombs), opened as
an MMDB and refused unless its type is a city database, then swapped into place
atomically. Each change is audited (``geoip_update``, ``geoip_off``).

``LTMS_GEOIP_DB`` still works and wins when set: the dashboard then says the
server's environment sets it and turns its own controls off.

Member addresses never leave the server: lookups read the local file only.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx

from server.models import AuditLog
from server.services import url_snapshot
from server.services.errors import ServiceError

log = logging.getLogger("ltms.geoip")

DBIP_HOST = "download.db-ip.com"
DBIP_PROVIDER = "DB-IP"
DBIP_LICENCE = "CC BY 4.0"
DBIP_CREDIT_URL = "https://db-ip.com"
DBIP_MANUAL_URL = "https://db-ip.com/db/download/ip-to-city-lite"
MAX_COMPRESSED_MB = 128      # the monthly City Lite .mmdb.gz is far smaller
MAX_UNCOMPRESSED_MB = 256    # the .mmdb is about 130 MB
DOWNLOAD_TIMEOUT_S = 300
MAX_REDIRECTS = 5
DB_FILE = "city.mmdb"
STATE_FILE = "state.json"
CHUNK = 1024 * 1024
GZIP_MAGIC = b"\x1f\x8b"


def dbip_url(year: int, month: int) -> str:
    return f"https://{DBIP_HOST}/free/dbip-city-lite-{year:04d}-{month:02d}.mmdb.gz"


def _candidates(now: datetime) -> list[str]:
    """This month's file, then last month's (published early in the month)."""
    prev = (now.replace(day=1) - timedelta(days=1))
    return [dbip_url(now.year, now.month), dbip_url(prev.year, prev.month)]


# --------------------------------------------------------------------------- #
# Where things live
# --------------------------------------------------------------------------- #
def geo_dir(cfg) -> Path:
    return Path(cfg.data_dir) / "geoip"


def managed_path(cfg) -> Path:
    return geo_dir(cfg) / DB_FILE


def env_path(cfg) -> str | None:
    return getattr(cfg, "geoip_db_path", None) or None


def load_state(cfg) -> dict:
    try:
        with open(geo_dir(cfg) / STATE_FILE, encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(cfg, state: dict) -> None:
    d = geo_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, d / STATE_FILE)


def active_path(cfg) -> str | None:
    """The city database lookups should use, or None when estimates are off.

    ``LTMS_GEOIP_DB`` wins; otherwise the managed file, when an admin turned
    estimates on (download or upload) and the file is there.
    """
    env = env_path(cfg)
    if env:
        return env
    state = load_state(cfg)
    path = managed_path(cfg)
    if state.get("mode") in ("download", "upload") and path.is_file():
        return str(path)
    return None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def status(cfg) -> dict:
    """What the Location estimates panel shows."""
    state = load_state(cfg)
    env = env_path(cfg)
    path = managed_path(cfg)
    refresh_days = int(getattr(cfg, "geoip_refresh_days", 30) or 0)
    mode = "env" if env else (state.get("mode") or "off")
    on = bool(active_path(cfg))
    provider = None if env else state.get("provider")
    return {
        "mode": mode,
        "on": on,
        "env": bool(env),
        "env_path": env,
        "provider": provider,
        "licence": None if env else state.get("licence"),
        "source": None if env else state.get("source"),
        "database_type": None if env else state.get("database_type"),
        "build_date": None if env else state.get("build_date"),
        "size": path.stat().st_size if (not env and path.is_file()) else None,
        "file_kept": (not env) and path.is_file(),
        "last_check": None if env else state.get("last_check"),
        "next_check": (None if env or mode != "download" or refresh_days <= 0
                       else state.get("next_check")),
        "refresh_days": refresh_days,
        "error": None if env else state.get("error"),
        "attribution": (not env) and on and provider == DBIP_PROVIDER,
        "credit_url": DBIP_CREDIT_URL,
        "manual_url": DBIP_MANUAL_URL,
    }


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def gunzip_capped(src: Path, dst: Path, cap_bytes: int) -> int:
    """Decompress ``src`` into ``dst``, refusing more than ``cap_bytes`` of output."""
    total = 0
    try:
        with gzip.open(src, "rb") as zin, open(dst, "wb") as out:
            while True:
                chunk = zin.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap_bytes:
                    raise ServiceError(413, "too_large",
                                       f"the file decompresses to more than {cap_bytes // (1024 * 1024)} MB; refused")
                out.write(chunk)
    except (OSError, EOFError) as exc:
        raise ServiceError(422, "bad_file", f"not a readable gzip file: {exc}") from None
    return total


def check_mmdb(path: Path) -> dict:
    """Open ``path`` as an MMDB and return its metadata, or refuse it (422)."""
    try:
        import maxminddb
    except ImportError:
        raise ServiceError(500, "geo_missing", "the maxminddb package isn't installed on the server") from None
    try:
        with maxminddb.open_database(str(path)) as reader:
            meta = reader.metadata()
    except (OSError, ValueError, maxminddb.InvalidDatabaseError) as exc:
        raise ServiceError(422, "bad_file", f"not a MaxMind-format (MMDB) database: {exc}") from None
    dbtype = str(getattr(meta, "database_type", "") or "")
    if "city" not in dbtype.lower():
        raise ServiceError(422, "not_city", f"this is a {dbtype or 'unknown'} database, not a city database")
    built = getattr(meta, "build_epoch", None)
    return {"database_type": dbtype,
            "build_date": datetime.fromtimestamp(built, timezone.utc).date().isoformat() if built else None}


def _install(cfg, mmdb: Path, meta: dict, *, mode: str, provider: str, licence: str | None, source: str,
             now: datetime) -> dict:
    """Swap a checked file into place and record it."""
    d = geo_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    os.replace(mmdb, managed_path(cfg))
    state = load_state(cfg)
    refresh_days = int(getattr(cfg, "geoip_refresh_days", 30) or 0)
    state.update({
        "mode": mode, "provider": provider, "licence": licence, "source": source,
        "database_type": meta["database_type"], "build_date": meta["build_date"],
        "installed_at": _iso(now), "last_check": _iso(now), "error": None,
        "next_check": _iso(now + timedelta(days=refresh_days)) if (mode == "download" and refresh_days > 0) else None,
    })
    _save_state(cfg, state)
    return state


def _note_error(cfg, message: str, *, now: datetime) -> None:
    state = load_state(cfg)
    state["error"] = message
    state["last_check"] = _iso(now)
    if state.get("mode") == "download":
        # Try again tomorrow rather than in a month.
        state["next_check"] = _iso(now + timedelta(days=1))
    _save_state(cfg, state)


def _need_env_off(cfg) -> None:
    if env_path(cfg):
        raise ServiceError(409, "env_set", "LTMS_GEOIP_DB is set in the server's environment, so the city database "
                                           "is managed there; unset it to manage it here")


# --------------------------------------------------------------------------- #
# Download from DB-IP
# --------------------------------------------------------------------------- #
async def _fetch_to(url: str, dest: Path, *, transport, resolve) -> str:
    """Stream ``url`` (DB-IP's host only) into ``dest``. Returns the final URL; 404 → FileNotFoundError."""
    allowed = [DBIP_HOST]
    cap = MAX_COMPRESSED_MB * 1024 * 1024
    async with httpx.AsyncClient(transport=transport, timeout=60, follow_redirects=False,
                                 headers={"User-Agent": "LTMS geoip"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await url_snapshot.check_url(url, allowed, resolve)
            async with client.stream("GET", url) as resp:
                stream = resp.extensions.get("network_stream")
                peer = stream.get_extra_info("server_addr") if stream is not None else None
                if peer and not url_snapshot._public(peer[0]):
                    raise ServiceError(403, "private_address", "the connection reached a private address; refused")
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise ServiceError(502, "bad_redirect", "a redirect without a Location")
                    url = urljoin(url, location)
                    continue
                if resp.status_code == 404:
                    raise FileNotFoundError(url)
                if resp.status_code != 200:
                    raise ServiceError(502, "fetch_failed", f"DB-IP answered HTTP {resp.status_code}")
                total = 0
                with open(dest, "wb") as out:
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > cap:
                            raise ServiceError(413, "too_large", f"the download is over {MAX_COMPRESSED_MB} MB; refused")
                        out.write(chunk)
                return url
    raise ServiceError(422, "too_many_redirects", f"more than {MAX_REDIRECTS} redirects")


async def _download_once(cfg, *, transport, resolve, now: datetime) -> dict:
    d = geo_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=d, prefix=".dl-") as tmp:
        gz, mmdb = Path(tmp) / "city.mmdb.gz", Path(tmp) / "city.mmdb"
        final = None
        for url in _candidates(now):
            try:
                final = await asyncio.wait_for(_fetch_to(url, gz, transport=transport, resolve=resolve),
                                               timeout=DOWNLOAD_TIMEOUT_S)
                break
            except FileNotFoundError:
                continue
        if final is None:
            raise ServiceError(502, "not_published", "DB-IP has no City Lite file for this month or last month")

        def finish() -> dict:  # blocking work, off the event loop
            gunzip_capped(gz, mmdb, MAX_UNCOMPRESSED_MB * 1024 * 1024)
            meta = check_mmdb(mmdb)
            return _install(cfg, mmdb, meta, mode="download", provider=DBIP_PROVIDER, licence=DBIP_LICENCE,
                            source=final, now=now)

        return await asyncio.to_thread(finish)


def _explain(exc: Exception) -> ServiceError:
    if isinstance(exc, ServiceError):
        return exc
    if isinstance(exc, asyncio.TimeoutError):
        return ServiceError(504, "timeout", f"the download took more than {DOWNLOAD_TIMEOUT_S} s")
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, OSError)):
        return ServiceError(502, "no_network", "the server couldn't reach download.db-ip.com. On a closed network, "
                                               "download the file by hand and use Upload instead")
    return ServiceError(502, "fetch_failed", f"couldn't download the city database: {type(exc).__name__}")


async def download(session, cfg, *, admin: str, transport=None, resolve=None, now: datetime | None = None) -> dict:
    """Turn estimates on with DB-IP's current file. On failure the previous file stays."""
    _need_env_off(cfg)
    now = now or datetime.now(timezone.utc)
    try:
        state = await _download_once(cfg, transport=transport, resolve=resolve or url_snapshot._system_resolve,
                                     now=now)
    except Exception as exc:  # noqa: BLE001 - every failure is reported, the old file kept
        err = _explain(exc)
        _note_error(cfg, err.message, now=now)
        raise err from None
    session.add(AuditLog(actor=admin, action="geoip_update",
                         detail={"mode": "download", "provider": state["provider"], "source": state["source"],
                                 "build_date": state["build_date"]}))
    return status(cfg)


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
async def upload(session, cfg, fileobj, filename: str, *, provider: str | None, licence: str | None,
                 admin: str, now: datetime | None = None) -> dict:
    """Install an uploaded ``.mmdb`` or ``.mmdb.gz`` (read from ``fileobj`` in chunks)."""
    _need_env_off(cfg)
    now = now or datetime.now(timezone.utc)
    provider = (provider or "").strip()[:100]
    if not provider:
        raise ServiceError(422, "provider_required", "say who provides this file (for example DB-IP or MaxMind)")
    licence = (licence or "").strip()[:200] or None
    name = (filename or "upload").strip()[:200]
    meta = await asyncio.to_thread(_install_upload, cfg, fileobj, name, provider, licence, now)
    session.add(AuditLog(actor=admin, action="geoip_update",
                         detail={"mode": "upload", "provider": provider, "licence": licence, "file": name,
                                 "build_date": meta["build_date"]}))
    return status(cfg)


def _install_upload(cfg, fileobj, name: str, provider: str, licence: str | None, now: datetime) -> dict:
    d = geo_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=d, prefix=".up-") as tmp:
        raw, mmdb = Path(tmp) / "upload", Path(tmp) / "city.mmdb"
        total = 0
        with open(raw, "wb") as out:
            while True:
                chunk = fileobj.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UNCOMPRESSED_MB * 1024 * 1024:
                    raise ServiceError(413, "too_large", f"over {MAX_UNCOMPRESSED_MB} MB; refused")
                out.write(chunk)
        if total == 0:
            raise ServiceError(422, "empty", "the file is empty")
        with open(raw, "rb") as fh:
            gz = fh.read(2) == GZIP_MAGIC
        if gz:
            if total > MAX_COMPRESSED_MB * 1024 * 1024:
                raise ServiceError(413, "too_large", f"over {MAX_COMPRESSED_MB} MB compressed; refused")
            gunzip_capped(raw, mmdb, MAX_UNCOMPRESSED_MB * 1024 * 1024)
        else:
            shutil.move(raw, mmdb)
        meta = check_mmdb(mmdb)
        _install(cfg, mmdb, meta, mode="upload", provider=provider, licence=licence, source=f"upload: {name}", now=now)
    return meta


# --------------------------------------------------------------------------- #
# Off, and the monthly refresh
# --------------------------------------------------------------------------- #
async def turn_off(session, cfg, *, delete: bool, admin: str) -> dict:
    _need_env_off(cfg)
    state = load_state(cfg)
    state.update({"mode": "off", "next_check": None, "error": None})
    deleted = False
    if delete:
        try:
            managed_path(cfg).unlink()
            deleted = True
        except FileNotFoundError:
            pass
        for key in ("provider", "licence", "source", "database_type", "build_date", "installed_at"):
            state.pop(key, None)
    _save_state(cfg, state)
    session.add(AuditLog(actor=admin, action="geoip_off", detail={"deleted_file": deleted}))
    return status(cfg)


async def refresh(session, cfg, *, transport=None, resolve=None, now: datetime | None = None) -> str:
    """Scheduler job: in download mode, fetch a newer file when one is due."""
    now = now or datetime.now(timezone.utc)
    if env_path(cfg) or int(getattr(cfg, "geoip_refresh_days", 30) or 0) <= 0:
        return "skipped: off"
    state = load_state(cfg)
    if state.get("mode") != "download":
        return "skipped: not in download mode"
    due = state.get("next_check")
    if due:
        try:
            if datetime.fromisoformat(due) > now:
                return "skipped: not due"
        except ValueError:
            pass
    try:
        new = await _download_once(cfg, transport=transport, resolve=resolve or url_snapshot._system_resolve, now=now)
    except Exception as exc:  # noqa: BLE001 - keep the previous file
        err = _explain(exc)
        _note_error(cfg, err.message, now=now)
        log.warning("geoip refresh failed, keeping the previous file: %s", err.message)
        return f"failed: {err.message}"
    session.add(AuditLog(actor="system", action="geoip_update",
                         detail={"mode": "download", "provider": new["provider"], "source": new["source"],
                                 "build_date": new["build_date"], "refresh": True}))
    return f"refreshed: {new['build_date']}"
