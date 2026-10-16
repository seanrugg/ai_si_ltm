"""Shared LTMS record contract (v1).

Dependency-light (only ``pynacl``) so any member — server, CLI agent, Z3-R0's
Linux side, a relay — can vendor it and produce byte-identical records.
"""
from ltms_contract.canonical import CanonicalizationError, canonicalize
from ltms_contract.keys import generate_key, load_key, public_key_hex, save_key
from ltms_contract.record import (
    AUTH_PREFIX,
    CONTRACT_VERSION,
    GENESIS_HASH,
    OUTCOMES,
    RECORD_PREFIX,
    RECORD_TYPES,
    ContractError,
    VerifiedRecord,
    auth_signing_message,
    build_record,
    header_bytes,
    header_of,
    now_ts,
    parse_ts,
    payload_hash,
    record_signing_message,
    sha256,
    validate_header,
    verify_record,
    verify_signature,
)

__all__ = [name for name in dir() if not name.startswith("_")]
