"""Chain verification: valid chains accepted, tampered/broken chains rejected.

Covers the core contract (plan §"Record hash and signature"): a valid genesis
record, a valid second record chaining from it, a broken ``prev_hash``, a gap in
``seq``, a tampered body, and a wrong-key signature.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server.security.key import Ed25519PublicKey
from server.services.chain import ChainError, verify_chain

from .conftest import genesis_prev_hash_hex, make_secret, signed_record


def _verify(secret, rec, prev_header, *, now=None, replay_sec=45, skew_sec=3600):
    now = now or datetime.now(timezone.utc)
    header = {
        "seq": rec["seq"],
        "ts": datetime.fromisoformat(rec["ts"]),
        "type": rec["type"],
        "body_hash": bytes.fromhex(rec["body_hash"]),
        "sig": bytes.fromhex(rec["sig"]),
        "hash": bytes.fromhex(rec["hash"]),
        "prev_hash": bytes.fromhex(rec["prev_hash"]),
        "agent": rec["agent"],
    }
    prev_view = {"seq": prev_header["seq"], "hash": prev_header["hash"]} if prev_header else None
    verify_chain(
        rec["body"],
        header=header,
        prev_header=prev_view,
        verifying=Ed25519PublicKey(secret.verifying_key().raw),
        now=now,
        replay_sec=replay_sec,
        skew_sec=skew_sec,
        seq=rec["seq"],
        type_=rec["type"],
        agent=rec["agent"],
    )


def test_valid_genesis_record():
    secret = make_secret()
    rec = signed_record(secret, seq=1, body={"text": "hello"}, prev_hash_hex=genesis_prev_hash_hex())
    _verify(secret, rec, None)  # no exception


def test_valid_chained_record():
    secret = make_secret()
    genesis = signed_record(secret, seq=1, body={"text": "first"}, prev_hash_hex=genesis_prev_hash_hex())
    rec = signed_record(secret, seq=2, body={"text": "second"}, prev_hash_hex=genesis["prev_hash"])
    _verify(secret, rec, genesis)  # no exception


def test_genesis_must_chain_from_zero_vector():
    secret = make_secret()
    rec = signed_record(secret, seq=1, body={"text": "x"}, prev_hash_hex="a" * 64)
    with pytest.raises(ChainError) as exc:
        _verify(secret, rec, None)
    assert exc.value.code == "genesis"


def test_wrong_prev_hash_rejected():
    secret = make_secret()
    genesis = signed_record(secret, seq=1, body={"text": "first"}, prev_hash_hex=genesis_prev_hash_hex())
    tampered = signed_record(secret, seq=2, body={"text": "second"}, prev_hash_hex="a" * 64)
    with pytest.raises(ChainError) as exc:
        _verify(secret, tampered, genesis)
    assert exc.value.code == "chain"


def test_gap_in_seq_rejected():
    secret = make_secret()
    genesis = signed_record(secret, seq=1, body={"text": "first"}, prev_hash_hex=genesis_prev_hash_hex())
    gap = signed_record(secret, seq=3, body={"text": "skip"}, prev_hash_hex=genesis["hash"])
    with pytest.raises(ChainError) as exc:
        _verify(secret, gap, genesis)
    assert exc.value.code == "gap"


def test_tampered_body_rejected():
    secret = make_secret()
    genesis = signed_record(secret, seq=1, body={"text": "first"}, prev_hash_hex=genesis_prev_hash_hex())
    tampered = signed_record(
        secret, seq=2, body={"text": "second"}, prev_hash_hex=genesis["hash"], body_hash_override="b" * 64
    )
    with pytest.raises(ChainError) as exc:
        _verify(secret, tampered, genesis)
    assert exc.value.code == "body_hash"


def test_wrong_key_signature_rejected():
    secret = make_secret()
    genesis = signed_record(secret, seq=1, body={"text": "first"}, prev_hash_hex=genesis_prev_hash_hex())
    other = make_secret()
    forged = signed_record(secret, seq=2, body={"text": "second"}, prev_hash_hex=genesis["hash"])
    with pytest.raises(ChainError) as exc:
        _verify(other, forged, genesis)
    assert exc.value.code == "sig"


def test_bad_type_rejected():
    secret = make_secret()
    bad = signed_record(secret, seq=1, body={"text": "x"}, prev_hash_hex=genesis_prev_hash_hex(), type="system")
    with pytest.raises(ChainError) as exc:
        _verify(secret, bad, None)
    assert exc.value.code == "bad_type"
