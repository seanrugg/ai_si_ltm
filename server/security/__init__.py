"""Crypto layer: record hash (JCS) + Ed25519 signatures + opaque tokens."""
from __future__ import annotations

import secrets

from server.security.hash import (
    canonical_bytes,
    canonical_hashes,
    body_hash_bytes,
    body_hash_hex,
    verify_body_hash,
)
from server.security.key import (
    AUTH_PREFIX,
    GENESIS_PREV_HASH,
    RECORD_PREFIX,
    Ed25519PublicKey,
    Ed25519SecretKey,
    sign,
    signature_hex,
    signed_nonce,
    signed_record,
    verify_nonce,
    verify_record,
    verify_signature,
)
from server.security.token import hash_token, issue_token, revoke_token

__all__ = [
    "canonical_bytes",
    "canonical_hashes",
    "body_hash_bytes",
    "body_hash_hex",
    "verify_body_hash",
    "Ed25519PublicKey",
    "Ed25519SecretKey",
    "RECORD_PREFIX",
    "AUTH_PREFIX",
    "GENESIS_PREV_HASH",
    "sign",
    "signature_hex",
    "signed_nonce",
    "signed_record",
    "verify_nonce",
    "verify_record",
    "verify_signature",
    "hash_token",
    "issue_token",
    "revoke_token",
]
