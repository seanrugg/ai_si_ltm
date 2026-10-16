"""The LTMS record contract (v1): field rules, hashing, signing and verification.

A record on the wire is a JSON object made of three parts:

* the **header** – every field except ``hash``, ``sig`` and ``payload``.
  It is permanent: the server never deletes or edits it.
* the **payload** – a free-form JSON object (``payload``). It may be purged by
  retention; the header keeps ``payload_hash`` so the chain still verifies.
* the **seal** – ``hash`` and ``sig``.

::

    payload_hash = SHA-256( JCS(payload) )            # hex, inside the header
    hash         = SHA-256( JCS(header) )             # hex
    sig          = Ed25519( b"ltms-record-v1\\n" || hash_raw_32_bytes )
    next.prev_hash = hash                             # genesis: 64 zeros

Auth challenges are signed under a different prefix so a record signature can
never be replayed as a login and vice versa::

    sig = Ed25519( b"ltms-auth-v1\\n" || nonce_raw_32_bytes )

See ``CONTRACT.md`` at the repo root for the language-neutral description and
``ltms_contract/vectors.json`` for test vectors.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from nacl.exceptions import BadSignatureError, CryptoError
from nacl.signing import SigningKey, VerifyKey

from ltms_contract.canonical import CanonicalizationError, canonicalize

CONTRACT_VERSION = 1
RECORD_PREFIX = b"ltms-record-v1\n"
AUTH_PREFIX = b"ltms-auth-v1\n"
GENESIS_HASH = "0" * 64

RECORD_TYPES = (
    "communication",
    "decision",
    "action",
    "event",
    "perception",
    "lesson",
    "policy",
    "system",
)
OUTCOMES = ("success", "partial", "reflex_stop", "stuck", "abandoned", "failed")

REQUIRED_HEADER = ("v", "agent", "seq", "ts", "type", "prev_hash", "payload_hash")
OPTIONAL_HEADER = ("summary", "place", "peers", "tags", "refs", "outcome", "sw")
SEAL_FIELDS = ("hash", "sig")
WIRE_ONLY = ("payload",)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
HEX128_RE = re.compile(r"^[0-9a-f]{128}$")
# RFC 3339 with mandatory offset; fractional seconds optional.
TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$"
)

LIMITS = {
    "summary": 500,
    "place": 128,
    "sw": 128,
    "list_items": 32,
    "list_item_len": 64,
    "refs_items": 16,
    "payload_bytes": 64 * 1024,
}


class ContractError(ValueError):
    """A record violates the contract. ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _canon(value: object, code: str) -> bytes:
    try:
        return canonicalize(value)
    except CanonicalizationError as exc:
        raise ContractError(code, str(exc)) from None


def payload_bytes(payload: dict) -> bytes:
    if not isinstance(payload, dict):
        raise ContractError("bad_payload", "payload must be a JSON object")
    raw = _canon(payload, "bad_payload")
    if len(raw) > LIMITS["payload_bytes"]:
        raise ContractError("payload_too_large", f"payload exceeds {LIMITS['payload_bytes']} bytes")
    return raw


def payload_hash(payload: dict) -> str:
    return sha256(payload_bytes(payload)).hex()


def header_of(record: dict) -> dict:
    """The hashed part of a wire record."""
    return {k: v for k, v in record.items() if k not in SEAL_FIELDS + WIRE_ONLY}


def header_bytes(header: dict) -> bytes:
    return _canon(header, "bad_header")


def record_signing_message(hash_hex: str) -> bytes:
    return RECORD_PREFIX + bytes.fromhex(hash_hex)


def auth_signing_message(nonce_hex: str) -> bytes:
    return AUTH_PREFIX + bytes.fromhex(nonce_hex)


# --------------------------------------------------------------------------- #
# Field validation
# --------------------------------------------------------------------------- #
def parse_ts(ts: str) -> datetime:
    """Parse an RFC 3339 timestamp to an aware UTC datetime."""
    if not isinstance(ts, str) or not TS_RE.match(ts):
        raise ContractError("bad_ts", "ts must be RFC 3339 with a UTC offset")
    text = ts.replace("Z", "+00:00")
    # Python accepts at most 6 fractional digits; truncate extra precision.
    m = re.match(r"^(.*\.\d{6})\d+(.*)$", text)
    if m:
        text = m.group(1) + m.group(2)
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        raise ContractError("bad_ts", "ts is not a valid date/time") from None


def _str_list(value: object, field: str) -> None:
    if not isinstance(value, list) or len(value) > LIMITS["list_items"]:
        raise ContractError("bad_header", f"{field} must be a list of at most {LIMITS['list_items']} strings")
    for item in value:
        if not isinstance(item, str) or not item or len(item) > LIMITS["list_item_len"]:
            raise ContractError("bad_header", f"{field} items must be non-empty strings <= {LIMITS['list_item_len']} chars")


def validate_header(header: dict) -> None:
    """Structural checks shared by every member and the server."""
    missing = [f for f in REQUIRED_HEADER if f not in header]
    if missing:
        raise ContractError("bad_header", f"missing header fields: {', '.join(missing)}")
    unknown = sorted(set(header) - set(REQUIRED_HEADER) - set(OPTIONAL_HEADER))
    if unknown:
        raise ContractError("bad_header", f"unknown header fields: {', '.join(unknown)} (put extra data in payload)")

    if header["v"] != CONTRACT_VERSION or isinstance(header["v"], bool):
        raise ContractError("bad_version", f"unsupported contract version {header['v']!r}")
    if not isinstance(header["agent"], str) or not NAME_RE.match(header["agent"]):
        raise ContractError("bad_header", "agent must match ^[a-z0-9][a-z0-9._-]{0,63}$")
    seq = header["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise ContractError("bad_header", "seq must be an integer >= 1")
    parse_ts(header["ts"])
    if header["type"] not in RECORD_TYPES:
        raise ContractError("bad_type", f"type must be one of {', '.join(RECORD_TYPES)}")
    for field in ("prev_hash", "payload_hash"):
        if not isinstance(header[field], str) or not HEX64_RE.match(header[field]):
            raise ContractError("bad_header", f"{field} must be 64 lowercase hex chars")
    if seq == 1 and header["prev_hash"] != GENESIS_HASH:
        raise ContractError("genesis", "seq 1 must have prev_hash of 64 zeros")

    for field in ("summary", "place", "sw"):
        if field in header:
            v = header[field]
            if not isinstance(v, str) or len(v) > LIMITS[field]:
                raise ContractError("bad_header", f"{field} must be a string <= {LIMITS[field]} chars")
    for field in ("peers", "tags"):
        if field in header:
            _str_list(header[field], field)
    if "refs" in header:
        refs = header["refs"]
        if not isinstance(refs, dict) or len(refs) > LIMITS["refs_items"]:
            raise ContractError("bad_header", "refs must be an object with at most 16 keys")
        for k, v in refs.items():
            if isinstance(v, str):
                continue
            if isinstance(v, list) and all(isinstance(i, str) for i in v):
                continue
            raise ContractError("bad_header", f"refs[{k!r}] must be a string or list of strings")
    if "outcome" in header and header["outcome"] not in OUTCOMES:
        raise ContractError("bad_header", f"outcome must be one of {', '.join(OUTCOMES)}")


# --------------------------------------------------------------------------- #
# Build + verify
# --------------------------------------------------------------------------- #
def now_ts() -> str:
    """Current time as an RFC 3339 UTC string with millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_record(
    signing_key: SigningKey,
    *,
    agent: str,
    seq: int,
    prev_hash: str,
    type: str,
    payload: dict | None = None,
    ts: str | None = None,
    **optional: object,
) -> dict:
    """Return a complete, signed wire record."""
    payload = {} if payload is None else payload
    header: dict = {
        "v": CONTRACT_VERSION,
        "agent": agent,
        "seq": seq,
        "ts": ts or now_ts(),
        "type": type,
        "prev_hash": prev_hash,
        "payload_hash": payload_hash(payload),
    }
    for key, value in optional.items():
        if value is not None:
            header[key] = value
    validate_header(header)
    hash_hex = sha256(header_bytes(header)).hex()
    sig = signing_key.sign(record_signing_message(hash_hex)).signature
    return {**header, "hash": hash_hex, "sig": sig.hex(), "payload": payload}


@dataclass(frozen=True)
class VerifiedRecord:
    header: dict
    header_bytes: bytes
    hash: str
    sig: str
    payload: dict | None
    payload_bytes: bytes | None


def verify_signature(public_key: bytes, message: bytes, sig_hex: str) -> bool:
    try:
        VerifyKey(bytes(public_key)).verify(message, bytes.fromhex(sig_hex))
        return True
    except (BadSignatureError, CryptoError, ValueError, TypeError):
        return False


def verify_record(record: dict, public_key: bytes, *, require_payload: bool = True) -> VerifiedRecord:
    """Self-contained check of one wire record (not its place in the chain).

    Order: structure -> payload hash -> header hash -> signature.
    """
    if not isinstance(record, dict):
        raise ContractError("bad_record", "record must be a JSON object")
    for field in SEAL_FIELDS:
        if field not in record:
            raise ContractError("bad_record", f"missing {field}")
    if not isinstance(record["hash"], str) or not HEX64_RE.match(record["hash"]):
        raise ContractError("bad_record", "hash must be 64 lowercase hex chars")
    if not isinstance(record["sig"], str) or not HEX128_RE.match(record["sig"]):
        raise ContractError("bad_record", "sig must be 128 lowercase hex chars")

    header = header_of(record)
    validate_header(header)

    p_bytes = None
    payload = record.get("payload")
    if payload is None:
        if require_payload:
            raise ContractError("bad_payload", "payload is required (use {} for none)")
    else:
        p_bytes = payload_bytes(payload)
        if sha256(p_bytes).hex() != header["payload_hash"]:
            raise ContractError("payload_hash", "payload does not match payload_hash")

    h_bytes = header_bytes(header)
    if sha256(h_bytes).hex() != record["hash"]:
        raise ContractError("hash", "hash does not match the canonical header")
    if not verify_signature(public_key, record_signing_message(record["hash"]), record["sig"]):
        raise ContractError("sig", "signature does not verify with the member's key")
    return VerifiedRecord(header, h_bytes, record["hash"], record["sig"], payload, p_bytes)
