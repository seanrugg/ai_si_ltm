"""Regenerate ltms_contract/vectors.json (deterministic: fixed key, fixed times).

Other-language members (C++ on the UNO Q, JS agents) should reproduce every
byte in this file. Only regenerate on a deliberate contract version bump.
"""
from __future__ import annotations

import json
from pathlib import Path

from nacl.signing import SigningKey

from ltms_contract import GENESIS_HASH, build_record, canonicalize, header_bytes, header_of, sha256

OUT = Path(__file__).resolve().parents[1] / "ltms_contract" / "vectors.json"

CANONICAL_CASES = [
    {"b": 2, "a": 1, "c": {"z": [3, 2, 1], "y": None}},
    {"floats": [1.0, 0.1, 1e21, 1e-7, -0.0, 123.456, 0.8, 0.15], "ints": [0, -1, 9007199254740991]},
    {"s": "tab\tnewline\nquote\"backslash\\ctrl\u0001 é é € \U0001F600"},
    {"€": 1, "\r": 2, "דּ": 3, "1": 4, "\U0001F600": 5, "\u0080": 6, "ö": 7},
    {"empty_obj": {}, "empty_list": [], "t": True, "f": False},
]


def main() -> None:
    key = SigningKey(bytes(range(32)))  # TEST KEY ONLY — never enroll it
    r1 = build_record(key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="communication",
                      ts="2026-10-20T19:04:10.000-04:00", summary="Vector chirped hello",
                      place="living-room", peers=["vector"], tags=["social"],
                      payload={"direction": "in", "text": "beep boop", "confidence": 0.93})
    r2 = build_record(key, agent="z3-r0", seq=2, prev_hash=r1["hash"], type="decision",
                      ts="2026-10-20T19:04:12.500-04:00", summary="Approach Vector",
                      place="living-room", peers=["vector"], refs={"input": "z3-r0:1"},
                      payload={"options": ["approach", "wait"], "chosen": "approach"})
    r3 = build_record(key, agent="z3-r0", seq=3, prev_hash=r2["hash"], type="action",
                      ts="2026-10-20T19:04:14.902-04:00", summary="Approach Vector",
                      place="living-room", peers=["vector"], tags=["social"], refs={"decision": "z3-r0:2"},
                      outcome="reflex_stop", sw="z3r0-app 0.5.0",
                      payload={"distance_m": 0.8, "speed_mps": 0.15, "outcome_detail": "front range 0.25 m"})
    chain = []
    for r in (r1, r2, r3):
        hb = header_bytes(header_of(r))
        chain.append({**r,
                      "_expected_header_canonical": hb.decode("utf-8"),
                      "_expected_payload_canonical": canonicalize(r["payload"]).decode("utf-8"),
                      "_expected_signing_message_hex": (b"ltms-record-v1\n" + sha256(hb)).hex()})
    data = {
        "contract_version": 1,
        "note": "Fields starting with '_' are expected intermediate values, not part of the record.",
        "canonical": [{"input": c, "output": canonicalize(c).decode("utf-8")} for c in CANONICAL_CASES],
        "record": {
            "secret_seed_hex": bytes(range(32)).hex(),
            "public_key": bytes(key.verify_key).hex(),
            "chain": chain,
        },
        "auth": {
            "nonce": "11" * 32,
            "signing_message_hex": (b"ltms-auth-v1\n" + bytes.fromhex("11" * 32)).hex(),
            "sig": key.sign(b"ltms-auth-v1\n" + bytes.fromhex("11" * 32)).signature.hex(),
        },
    }
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
