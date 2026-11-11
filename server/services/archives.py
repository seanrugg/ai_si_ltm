"""Unpack zip, tar and tar.gz uploads into dataset items (#246).

An archive is checked in full before anything is stored. It is refused
(422 ``bad_archive``, nothing stored) for:
- more than 10,000 entries;
- a total uncompressed size above ``dataset_upload_max_mb``;
- a compression ratio above 100:1 (for archives that unpack to more than 1 MB);
- absolute paths, ``..``, symlinks, hardlinks or device files.

Each member file then becomes an item keyed ``<prefix>/<path in archive>`` and
goes through the normal tier rules. Nested archives and keys already in the
version are skipped and listed; the archive file itself is not kept.
"""
from __future__ import annotations

import posixpath
import stat
import tarfile
import zipfile
from dataclasses import dataclass

from server.services import dataset_store
from server.services.errors import ServiceError

MAX_ENTRIES = 10_000
MAX_RATIO = 100
RATIO_APPLIES_ABOVE = 1024 * 1024
ARCHIVE_TYPES = {"application/zip", "application/x-tar", "application/gzip"}


@dataclass
class Member:
    path: str
    size: int


def _bad(msg: str) -> ServiceError:
    return ServiceError(422, "bad_archive", msg)


def _safe_path(name: str) -> str:
    if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        raise _bad(f"absolute path in archive: {name!r}")
    parts = name.replace("\\", "/").split("/")
    if ".." in parts:
        raise _bad(f"'..' in archive path: {name!r}")
    return posixpath.normpath("/".join(p for p in parts if p not in ("", ".")))


def _check_totals(members: list[Member], compressed: int, max_bytes: int) -> None:
    total = sum(m.size for m in members)
    if total > max_bytes:
        raise _bad(f"unpacks to {total:,} bytes, over the {max_bytes // (1024 * 1024)} MB limit")
    if total > RATIO_APPLIES_ABOVE and total > MAX_RATIO * max(compressed, 1):
        raise _bad(f"compression ratio above {MAX_RATIO}:1; refused as a possible zip bomb")


def _scan_zip(z: zipfile.ZipFile) -> list[Member]:
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise _bad(f"more than {MAX_ENTRIES:,} entries")
    out = []
    for info in infos:
        path = _safe_path(info.filename)
        ftype = stat.S_IFMT(info.external_attr >> 16)  # 0 when the zip records no Unix type
        if ftype == stat.S_IFLNK:
            raise _bad(f"symlink in archive: {info.filename!r}")
        if ftype and ftype not in (stat.S_IFREG, stat.S_IFDIR):
            raise _bad(f"special file in archive: {info.filename!r}")
        if info.is_dir():
            continue
        out.append(Member(path, info.file_size))
    return out


def _scan_tar(t: tarfile.TarFile, max_bytes: int) -> list[Member]:
    out, total, count = [], 0, 0
    for info in t:  # streams headers; stops early at a limit
        count += 1
        if count > MAX_ENTRIES:
            raise _bad(f"more than {MAX_ENTRIES:,} entries")
        path = _safe_path(info.name)
        if info.issym() or info.islnk():
            raise _bad(f"link in archive: {info.name!r}")
        if not (info.isfile() or info.isdir()):
            raise _bad(f"special file in archive: {info.name!r}")
        if info.isdir():
            continue
        total += info.size
        if total > max_bytes:
            raise _bad(f"unpacks to more than the {max_bytes // (1024 * 1024)} MB limit")
        out.append(Member(path, info.size))
    return out


def open_archive(path: str, media_type: str):
    """``("zip", ZipFile)`` or ``("tar", TarFile)``; None if it isn't really an archive."""
    if media_type == "application/zip":
        try:
            return "zip", zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            return None
    try:
        return "tar", tarfile.open(path, mode="r:*")
    except (tarfile.TarError, OSError, EOFError):
        return None  # e.g. a gzip of a single file: stored as it is


async def unpack(session, cfg, v, *, archive_key: str, sha: str, size: int, media_type: str,
                 add_item, check_new_key) -> dict | None:
    """Unpack into ``v``. Returns the summary, or None if the file isn't an archive."""
    path = str(dataset_store.path_for(cfg.data_dir, sha))
    opened = open_archive(path, media_type)
    if opened is None:
        return None
    kind, arc = opened
    max_bytes = cfg.dataset_upload_max_mb * 1024 * 1024
    prefix = archive_key.rsplit("/", 1)[0] if "/" in archive_key else ""
    stored, skipped, errors = [], [], []
    with arc:
        members = _scan_zip(arc) if kind == "zip" else _scan_tar(arc, max_bytes)
        _check_totals(members, size, max_bytes)
        wanted = {m.path for m in members}
        sources = ((i, i.filename) for i in arc.infolist() if not i.is_dir()) if kind == "zip" else \
            ((i, i.name) for i in arc.getmembers() if i.isfile())
        for info, name in sources:
            rel = _safe_path(name)
            if rel not in wanted:
                continue
            key = f"{prefix}/{rel}" if prefix else rel
            if len(key) > 200:
                skipped.append({"key": key, "reason": "key longer than 200 characters"})
                continue
            try:
                await check_new_key(session, v, key)
            except ServiceError:
                skipped.append({"key": key, "reason": "already in this version"})
                continue
            fh = arc.open(info) if kind == "zip" else arc.extractfile(info)
            if fh is None:
                skipped.append({"key": key, "reason": "not a regular file"})
                continue
            try:
                with fh:
                    head = fh.read(8192)
                    kind_of = _nested(head, rel)
                    if kind_of:
                        skipped.append({"key": key, "reason": f"nested archive ({kind_of}); upload it separately"})
                        continue
                    msha, msize, _ = dataset_store.save_file(cfg.data_dir, fh, max_bytes, head=head)
            except dataset_store.TooLarge:
                errors.append({"key": key, "error": "bigger than its header said"})
                continue
            except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
                errors.append({"key": key, "error": f"couldn't read it: {exc}"[:200]})
                continue
            stored.append(await add_item(session, cfg, v, key=key, filename=rel, sha=msha, size=msize, head=head))
    return {"stored": stored, "skipped": skipped, "errors": errors}


def _nested(head: bytes, name: str) -> str | None:
    if head.startswith(b"PK\x03\x04") and not name.lower().endswith((".docx", ".pptx", ".xlsx", ".epub")):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head[257:262] == b"ustar":
        return "tar"
    return None
