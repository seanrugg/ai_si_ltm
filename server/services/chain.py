"""Chain verification: verify a single record against the prior header.

The chain is the heart of the LTMS contract (plan §"Record hash and signature").
Every stored signature is a signature over the exact bytes that ``prev_hash``
chains into, so verification must recompute every field from the persisted
payload — never trust a client-supplied ``body_hash`` or ``hash``.

Verification order (mirrors the plan's numbered list):

1. ``hash`` == ``SHA-256(sig)`` — the link is internally consistent.
2. ``prev_hash`` == previous header's ``hash`` (genesis marker at seq 1).
3. ``seq`` == last acknowledged + 1 — no gaps, no reordering.
4. ``body_hash`` == ``SHA-256(canonical(payload))`` — the payload is intact.
5. ``sig`` verifies with the member's stored Ed25519 key over the domain-separated
   record prefix (plan §"Signature & domain separation").
6. ``ts`` within the replay/skew window.
7. ``type`` is a permitted enum value for the member's permission set.

Any failure raises :class:`ChainError` with a machine-readable ``code`` so the
HTTP layer can map it to a status without leaking internals.
"""
from __future__ import annotations

from datetime import datetime, timezone

from server.security.hash import body_hash
from server.security.key import (
    GENESIS_PREV_HASH,
    RECORD_PREFIX,
    SEPARATOR,
    Ed25519PublicKey,
    record_hash,
    verify_signature,
)

# The record payload types a member may contribute. ``system`` is server/rollup
# only and is never accepted over the API.
RECORD_TYPES = {
    "communication",
    "decision",
    "action",
    "event",
    "perception",
    "lesson",
    "policy",
}


class ChainError(Exception):
    """Raised when a record fails chain verification.

    ``code`` is a stable, machine-readable identifier (e.g. ``"gap"``) that the
    API layer turns into a ``400`` with a ``code`` field in the JSON body. Never
    expose the message verbatim to unauthenticated callers.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _check_ts(ts: datetime, now: datetime, replay_sec: int, skew_sec: int) -> None:
    """Reject a record whose timestamp is outside the replay/skew window."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now.replace(tzinfo=timezone.utc)
    delta = (now - ts).total_seconds()
    if delta > replay_sec:
        raise ChainError("stale", f"record ts {delta:.0f}s older than replay window {replay_sec}s")
    if delta < -skew_sec:
        raise ChainError("skew", f"record ts {abs(delta):.0f}s ahead of now beyond skew {skew_sec}s")


def compute_body_hash(payload: dict) -> bytes:
    """Return the 32-byte ``body_hash`` for a payload dict (source of truth)."""
    return body_hash(payload)


def compute_link_hash(sig: bytes) -> bytes:
    """Return ``hash = SHA-256(sig_bytes)`` — the chain link this record carries."""
    return record_hash(sig)


def _verify_signature(
    verifying: Ed25519PublicKey,
    body_hash_hex_64: str,
    sig_hex: str,
) -> bool:
    """Verify the domain-separated record signature.

    Builds the message ``ltms-record-v1 : 64-hex(body_hash)`` exactly as the
    signing member does (plan §"Signature & domain separation") and verifies
    against the member's stored key. Returns ``False`` on any bad input.
    """
    message = RECORD_PREFIX + SEPARATOR + body_hash_hex_64.encode("ascii")
    return verify_signature(verifying, message, sig_hex)


def verify_chain(
    payload: dict,
    *,
    header: dict,
    prev_header: dict | None,
    verifying: Ed25519PublicKey,
    now: datetime,
    replay_sec: int,
    skew_sec: int,
    seq: int,
    type_: str,
    agent: str,
) -> None:
    """Full verification of one record against the previous header.

    ``header`` is the record being ingested (dict-like: seq, ts, type, body_hash,
    sig, hash, prev_hash, agent). ``prev_header`` is the previously persisted
    header for the same member (``None`` when this is the member's first record,
    i.e. genesis). Raises :class:`ChainError` on any failure; returns ``None`` on
    success.

    This recomputes the body hash from the payload and the link hash from the
    signature so a tampered client body or signature is caught even if the
    client supplies matching-but-wrong values.
    """
    # 3. seq must be exactly one past the previous acknowledged seq
    #    (genesis: seq 1 chains from the zero vector; treat prev_seq as 0).
    expected_seq = 1 if prev_header is None else prev_header["seq"] + 1
    if seq != expected_seq:
        raise ChainError("gap", f"expected seq {expected_seq}, got {seq}")

    # 6. timestamp within replay/skew window.
    _check_ts(header["ts"], now, replay_sec, skew_sec)

    # 7. type must be a permitted enum value.
    if type_ not in RECORD_TYPES:
        raise ChainError("bad_type", f"record type {type_!r} not permitted")

    # 2. genesis / chain-link of prev_hash.
    if prev_header is None:
        if header["prev_hash"] != GENESIS_PREV_HASH:
            raise ChainError("genesis", "seq 1 must chain from the genesis marker")
    else:
        if header["prev_hash"] != prev_header["hash"]:
            raise ChainError("chain", "prev_hash does not match previous header link hash")

    # 1. link hash must equal SHA-256(sig).
    sig = header["sig"]
    expected_link = compute_link_hash(sig)
    if header["hash"] != expected_link:
        raise ChainError("link", "record hash does not equal SHA-256(sig)")

    # 4. body_hash must match the canonicalised payload (recompute, don't trust).
    body_hash_bytes = compute_body_hash(payload)
    if header["body_hash"] != body_hash_bytes:
        raise ChainError("body_hash", "body_hash does not match canonical payload")

    # 5. signature must verify over the domain-separated record prefix.
    if not _verify_signature(verifying, body_hash_bytes.hex(), sig.hex()):
        raise ChainError("sig", "record signature does not verify against member key")
