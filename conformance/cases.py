"""Conformance cases (#298). Each takes an adapter (see adapter.py) and raises AssertionError on failure."""
from __future__ import annotations

from ltms_contract import GENESIS_HASH, ContractError, verify_record


def signed_records_verify(h) -> None:
    """Every record the harness produces verifies against the frozen contract."""
    rec = h.append("perception", "conformance: signing", {"case": "signing", "values": [1, 2.5, "x"]})
    try:
        verify_record(rec, h.public_key)
    except ContractError as exc:
        raise AssertionError(f"record doesn't verify: {exc.code}: {exc}") from None
    assert rec["agent"] == h.name, f"agent {rec['agent']!r} != member name {h.name!r}"


def chain_continues_across_restart(h) -> None:
    """Sequence and prev_hash continue after a restart (state reloaded from disk)."""
    a = h.append("event", "conformance: before restart", {"case": "restart"})
    h.restart()
    b = h.append("event", "conformance: after restart", {"case": "restart"})
    assert b["seq"] == a["seq"] + 1, f"seq {b['seq']} after {a['seq']}"
    assert b["prev_hash"] == a["hash"], "prev_hash doesn't link to the previous record"


def flush_reaches_ltms_and_heads_match(h) -> None:
    """After a flush, LTMS's head for the member equals the last record the harness made."""
    last = h.append("event", "conformance: flush", {"case": "flush"})
    h.flush()
    head = h.server_head()
    assert head["last_ack_seq"] == last["seq"], f"LTMS head {head['last_ack_seq']} != {last['seq']}"
    assert head["last_hash"] == last["hash"], "LTMS head hash differs from the harness's last record"


def resend_is_idempotent(h) -> None:
    """Flushing again (nothing new, or a restart and flush) changes nothing on LTMS."""
    before = h.server_head()
    h.flush()
    h.restart()
    h.flush()
    assert h.server_head() == before, "a second flush changed LTMS's head"


def refuses_system_records(h) -> None:
    """The harness never produces a `system` record (reserved for the server)."""
    try:
        h.append("system", "conformance: must be refused", {})
    except Exception:  # noqa: BLE001 - any refusal is fine
        pass
    else:
        h.flush()
        assert not any(r["type"] == "system" for r in h.server_records()), "a system record reached LTMS"
        raise AssertionError("append('system') was accepted locally")


def stored_chain_is_unbroken(h) -> None:
    """What LTMS stored for the member is one unbroken chain from genesis."""
    recs = h.server_records()
    assert recs, "LTMS has no records for this member"
    prev = GENESIS_HASH
    for i, r in enumerate(recs, start=1):
        assert r["seq"] == i, f"gap or reorder at seq {r['seq']} (expected {i})"
        assert r["prev_hash"] == prev, f"broken link at seq {r['seq']}"
        verify_record(r, h.public_key, require_payload=False)
        prev = r["hash"]


CASES = [signed_records_verify, chain_continues_across_restart, flush_reaches_ltms_and_heads_match,
         resend_is_idempotent, refuses_system_records, stored_chain_is_unbroken]


def run(h, cases=CASES) -> list[tuple[str, bool, str]]:
    """Run every case in order; returns (name, passed, reason)."""
    out = []
    for case in cases:
        try:
            case(h)
            out.append((case.__name__, True, ""))
        except Exception as exc:  # noqa: BLE001 - report and keep going
            out.append((case.__name__, False, f"{type(exc).__name__}: {exc}"))
    return out
