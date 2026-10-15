"""Ed25519 signing for the mesh signing contract (plan section 4.x).

A member holds a 32-byte Ed25519 secret key. Records and auth nonces are signed
with domain-separated prefixes so a record signature can never be replayed as an
authentication signature:

* record: ``ltms-record-v1 : 64-hex(body_hash)``
* nonce:  ``ltms-auth-v1 : 32-hex(nonce)``

The chain link ``hash`` is ``SHA-256(sig_raw_bytes)``; the server recomputes it
from the signature it verifies and compares it against the stored value, then
chains ``prev_hash`` -> ``hash`` on ingest.
"""
from __future__ import annotations

import hashlib

from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError, CryptoError

RECORD_PREFIX = b"ltms-record-v1"
AUTH_PREFIX = b"ltms-auth-v1"
SEPARATOR = b"\n"

GENESIS_PREV_HASH = b"\x00" * 32  # 32-byte zero vector genesis marker


def sha256(data: bytes) -> bytes:
    """Return the 32-byte SHA-256 digest of ``data``."""
    return hashlib.sha256(bytes(data)).digest()


def record_hash(sig: bytes) -> bytes:
    """Return ``(hash = SHA-256(sig_bytes))`` for the chain link."""
    return sha256(sig)


class Ed25519PublicKey:
    """Wrapped 32-byte Ed25519 verifying key."""

    def __init__(self, raw: bytes) -> None:
        self.raw = bytes(raw)


class Ed25519SecretKey:
    """Wrapped 32-byte Ed25519 secret key.

    Wrapping keeps the verifying key resident so generating the public key is
    cheap and does not re-read the secret repeatedly.
    """

    def __init__(self, raw: bytes) -> None:
        if not isinstance(raw, (bytes, bytearray)):
            raise TypeError("secret key must be a byte string")
        if len(raw) != 32:
            raise ValueError("Ed25519 secret key must be 32 bytes")
        self.raw = bytes(raw)
        # Validate lazily: constructing SigningKey is where bad secrets blow up.
        self._verify_key: bytes = bytes(SigningKey(self.raw).verify_key)

    def verifying_key(self) -> Ed25519PublicKey:
        """Return the corresponding :class:`Ed25519PublicKey`."""
        return Ed25519PublicKey(self._verify_key)


def sign(secret: Ed25519SecretKey, message: bytes) -> bytes:
    """Return the raw 64-byte Ed25519 signature over ``message``."""
    return SigningKey(secret.raw).sign(bytes(message)).signature


def signature_hex(secret: Ed25519SecretKey, message: bytes) -> str:
    """Return the hex-encoded 64-byte Ed25519 signature over ``message``."""
    return sign(secret, message).hex()


def sign_record(secret: Ed25519SecretKey, body_hash_hex_64: str) -> str:
    """Sign a record's ``body_hash`` (64 hex chars); return sig as hex."""
    body_hash_hex_64 = _require(body_hash_hex_64, 64)
    message = RECORD_PREFIX + SEPARATOR + body_hash_hex_64.encode("ascii")
    return sign(secret, message).hex()


def verify_record(
    verifying: Ed25519PublicKey, body_hash_hex_64: str, sig_hex: str
) -> bool:
    """Verify a record signature; never raises, returns ``False`` on bad input."""
    if len(body_hash_hex_64) != 64:
        return False
    sig = _decode_sig(sig_hex)
    if len(sig) != 64:
        return False
    message = RECORD_PREFIX + SEPARATOR + body_hash_hex_64.encode("ascii")
    return _verify(verifying, message, sig)


def sign_nonce(secret: Ed25519SecretKey, nonce_hex_32: str) -> str:
    """Sign an auth nonce (32 hex chars); return sig as hex."""
    nonce_hex_32 = _require(nonce_hex_32, 32)
    message = AUTH_PREFIX + SEPARATOR + nonce_hex_32.encode("ascii")
    return sign(secret, message).hex()


def verify_nonce(
    verifying: Ed25519PublicKey, nonce_hex_32: str, sig_hex: str
) -> bool:
    """Verify an auth-nonce signature; never raises."""
    if len(nonce_hex_32) != 32:
        return False
    sig = _decode_sig(sig_hex)
    if len(sig) != 64:
        return False
    message = AUTH_PREFIX + SEPARATOR + nonce_hex_32.encode("ascii")
    return _verify(verifying, message, sig)


def verify_signature(
    verifying: Ed25519PublicKey,
    message: bytes,
    signature_hex: str,
) -> bool:
    """Verify a raw 64-byte Ed25519 signature over ``message``.

    General-purpose entry point used by the layer package; ``verify_record`` and
    ``verify_nonce`` are thin wrappers that build the domain-separated messages
    first. Never raises — returns ``False`` on any bad input.
    """
    sig = _decode_sig(signature_hex)
    if sig is None or len(sig) != 64:
        return False
    return _verify(verifying, bytes(message), sig)


def signed_record(secret: Ed25519SecretKey, body_hash_hex_64: str) -> str:
    """Sign a record's ``body_hash`` (64 hex chars); return the raw hex sig."""
    return sign_record(secret, body_hash_hex_64)


def signed_nonce(secret: Ed25519SecretKey, nonce_hex_32: str) -> str:
    """Sign an auth nonce (32 hex chars); return the raw hex sig."""
    return sign_nonce(secret, nonce_hex_32)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _require(value: str, length: int) -> str:
    """Validate a hex string's length, raising ``ValueError`` when it does not match."""
    if not isinstance(value, str) or len(value) != length:
        raise ValueError(f"expected {length} hex chars, got {len(value)}")
    return value


def _decode_sig(sig_hex: object) -> bytes | None:
    """Decode a hex (or bytes) signature to raw bytes, or ``None`` on failure."""
    try:
        return bytes.fromhex(sig_hex if isinstance(sig_hex, str) else sig_hex.decode())
    except (ValueError, TypeError, AttributeError):
        return None


def _verify(
    verifying: Ed25519PublicKey, message: bytes, sig: bytes
) -> bool:
    """Return ``True`` iff ``sig`` over ``message`` verifies against ``verifying``."""
    try:
        VerifyKey(verifying.raw).verify(bytes(message), sig)
        return True
    except (BadSignatureError, CryptoError, ValueError):
        return False
