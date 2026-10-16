"""The signing contract: canonical JSON, hashing, signatures, domain separation."""
import json
from pathlib import Path

import pytest

from ltms_contract import (
    GENESIS_HASH,
    ContractError,
    auth_signing_message,
    build_record,
    canonicalize,
    generate_key,
    header_bytes,
    header_of,
    record_signing_message,
    sha256,
    verify_record,
    verify_signature,
)
from ltms_contract.canonical import CanonicalizationError

VECTORS = Path(__file__).resolve().parents[2] / "ltms_contract" / "vectors.json"


@pytest.mark.parametrize("value,expected", [
    (1.0, "1"), (0.1, "0.1"), (-0.0, "0"), (1e21, "1e+21"), (1e20, "100000000000000000000"),
    (1e-7, "1e-7"), (0.000001, "0.000001"), (123.456, "123.456"), (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"), (333333333.33333329, "333333333.3333333"),
    (2**53 - 1, "9007199254740991"), (-42, "-42"),
])
def test_jcs_numbers(value, expected):
    assert canonicalize(value).decode() == expected


def test_jcs_key_order_is_utf16():
    # RFC 8785 §3.2.3: sorted by UTF-16 code units (emoji before U+FB33).
    obj = {"€": 1, "\r": 2, "דּ": 3, "1": 4, "\U0001F600": 5, "\u0080": 6, "ö": 7}
    keys = list(json.loads(canonicalize(obj)).keys())
    assert keys == ["\r", "1", "\u0080", "ö", "€", "\U0001F600", "דּ"]


def test_jcs_strings_and_rejections():
    assert canonicalize({"s": 'a\u0001"\\\n\u007fé'}) == '{"s":"a\\u0001\\"\\\\\\n\u007fé"}'.encode()
    for bad in (float("nan"), float("inf"), 2**53, {1: "x"}, "\ud800", object()):
        with pytest.raises(CanonicalizationError):
            canonicalize(bad)


def test_round_trip_and_float_payload():
    key = generate_key()
    rec = build_record(key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="action",
                       summary="Approach Vector", peers=["vector"], outcome="reflex_stop",
                       payload={"distance_m": 0.8, "speed_mps": 0.15})
    v = verify_record(rec, bytes(key.verify_key))
    assert v.hash == rec["hash"] and sha256(v.header_bytes).hex() == rec["hash"]
    # Survives a JSON round trip through any parser that keeps doubles.
    again = json.loads(json.dumps(rec))
    verify_record(again, bytes(key.verify_key))


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(summary="Retreat from Vector"), "hash"),
    (lambda r: r["payload"].update(distance_m=9.9), "payload_hash"),
    (lambda r: r.update(sig=("0" if r["sig"][0] != "0" else "1") + r["sig"][1:]), "sig"),
    (lambda r: r.update(extra="x"), "bad_header"),
    (lambda r: r.update(type="gossip"), "bad_type"),
    (lambda r: r.update(ts="2026-10-15 12:00:00"), "bad_ts"),
    (lambda r: r.update(seq=1, prev_hash="ab" * 32), "genesis"),
    (lambda r: r.pop("payload"), "bad_payload"),
])
def test_tamper_is_detected(mutate, code):
    key = generate_key()
    rec = build_record(key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="action",
                       summary="Approach Vector", payload={"distance_m": 0.8})
    mutate(rec)
    with pytest.raises(ContractError) as exc:
        verify_record(rec, bytes(key.verify_key))
    assert exc.value.code == code


def test_wrong_key_rejected():
    rec = build_record(generate_key(), agent="a", seq=1, prev_hash=GENESIS_HASH, type="event")
    with pytest.raises(ContractError) as exc:
        verify_record(rec, bytes(generate_key().verify_key))
    assert exc.value.code == "sig"


def test_domain_separation():
    """A login signature over 32 bytes can't pass as a record signature over the
    same 32 bytes, and vice versa."""
    key = generate_key()
    blob = "ab" * 32
    auth_sig = key.sign(auth_signing_message(blob)).signature.hex()
    rec_sig = key.sign(record_signing_message(blob)).signature.hex()
    pk = bytes(key.verify_key)
    assert not verify_signature(pk, record_signing_message(blob), auth_sig)
    assert not verify_signature(pk, auth_signing_message(blob), rec_sig)


def test_published_vectors():
    """vectors.json is what other-language members test against; it must stay valid."""
    data = json.loads(VECTORS.read_text())
    for case in data["canonical"]:
        assert canonicalize(case["input"]).decode() == case["output"]
    pk = bytes.fromhex(data["record"]["public_key"])
    for rec in data["record"]["chain"]:
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        assert header_bytes(header_of(clean)).decode() == rec["_expected_header_canonical"]
        verify_record(clean, pk)
