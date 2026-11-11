"""Content-addressed storage for dataset files (#237).

Files live at ``<data_dir>/datasets/<sha256[:2]>/<sha256>``, on a volume and
never in PostgreSQL. The same content is stored once.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

CHUNK = 1024 * 1024


class TooLarge(Exception):
    pass


def root(data_dir: str) -> Path:
    path = Path(data_dir) / "datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def path_for(data_dir: str, sha256: str) -> Path:
    return root(data_dir) / sha256[:2] / sha256


async def save_stream(data_dir: str, upload, max_bytes: int) -> tuple[str, int, bytes]:
    """Stream an upload to disk while hashing it.

    Returns ``(sha256, size, head)`` where ``head`` is the first 8 KB, for
    sniffing. Raises TooLarge (and keeps nothing) above ``max_bytes``.
    """
    base = root(data_dir)
    digest, size, head = hashlib.sha256(), 0, b""
    fd, tmp = tempfile.mkstemp(dir=base, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await upload.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge(size)
                if len(head) < 8192:
                    head += chunk[: 8192 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        sha = digest.hexdigest()
        final = path_for(data_dir, sha)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            os.unlink(tmp)
        else:
            os.replace(tmp, final)
        return sha, size, head
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_bytes(data_dir: str, sha256: str, limit: int | None = None) -> bytes:
    with open(path_for(data_dir, sha256), "rb") as f:
        return f.read() if limit is None else f.read(limit)
