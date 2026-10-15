"""Record hash contract (RFC 8785 / JCS) — plan section 4.2.

``body_hash`` is ``SHA-256(canonical_bytes(payload))`` where ``payload`` is the
record minus its ``hash``/``sig`` chain fields. Canonicalization sorts keys
alphabetically and removes all insignificant whitespace; floats are forbidden in
hashed fields. The raw canonical ``bytes`` are the source of truth so that every
member, in every language, can recompute and verify the same digest.
"""
from __future__ import annotations

import hashlib
import json
from typing import Mapping, Optional

__all__ = ["canonical_bytes", "canonicalize", "body_hash", "body_hash_hex"]


def canonical_bytes(record: Mapping) -> bytes:
    """Return RFC 8785 (JCS) canonical UTF-8 bytes for ``record``.

    ``hash``, ``sig``, and ``body_hash`` are dropped *before* canonicalizing so
    members never have to hash their own signatures (and so the digest is not
    self-referential: ``body_hash`` cannot depend on its own value). Rejects
    floats in hashed fields.
    """
    cleaned = {
        key: value
        for key, value in record.items()
        if key not in ("hash", "sig", "body_hash")
    }
    _reject_floats(cleaned)
    text = json.dumps(cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def canonicalize(record: Mapping) -> bytes:
    """Alias of :func:`canonical_bytes` for readability in the chain layer."""
    return canonical_bytes(record)


def _reject_floats(node: object) -> None:
    if isinstance(node, bool):
        return  # bools are fine; they are not floats for hashing purposes
    if isinstance(node, float):
        raise ValueError(f"floats not permitted in hashed fields: {node!r}")
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_floats(key)
            _reject_floats(value)
    elif isinstance(node, list):
        for value in node:
            _reject_floats(value)


def body_hash(record: Mapping) -> bytes:
    """Return the raw 32-byte ``body_hash`` digest for ``record``.

    Frozen contract: ``body_hash = SHA-256(canonical_bytes(payload))`` where the
    payload is the record with ``hash``/``sig`` removed, i.e. the canonical bytes
    are the source of truth.
    """
    return hashlib.sha256(canonical_bytes(dict(record))).digest()


def body_hash_hex(record: Mapping) -> str:
    """Return the hex-encoded ``body_hash`` for ``record``."""
    return body_hash(record).hex()


def canonical_hashes(records: list) -> bytes:
    """Return the combined ``body_hash`` fingerprint over a sequence of records.

    A member can commit to an ordered batch by hashing the per-record digests
    into a single 32-byte value. Raises ``TypeError`` for non-sequence input so
    callers catch misuse early.
    """
    if not isinstance(records, (list, tuple)):
        raise TypeError("canonical_hashes expects a sequence of record mappings")
    parts = [body_hash(r) for r in records]
    return hashlib.sha256(b"".join(parts)).digest()


def body_hash_bytes(record: Mapping) -> bytes:
    """Alias of :func:**body_hash** — returns the raw 32-byte digest.

    The ``_bytes`` suffix lets callers express that a byte string is expected;
    it is identical to :func:`body_hash`.
    """
    return body_hash(record)


def verify_body_hash(expected_hex: str, record: Mapping) -> bool:
    """True when ``expected_hex`` (hex or bytes) equals ``body_hash(record)``.

    Never raises. A wrong-length or non-hex value (or a garbage ``record``)
    yields ``False`` rather than raising, so a verification miss never leaks
    through an exception.
    """
    try:
        raw = bytes.fromhex(expected_hex) if isinstance(expected_hex, str) else bytes(expected_hex)
    except (ValueError, TypeError, AttributeError):
        return False
    if len(raw) != 32:
        return False
    return raw == body_hash(record)
