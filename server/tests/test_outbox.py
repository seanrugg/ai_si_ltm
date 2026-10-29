"""Persistent outbox: signed records survive a restart."""
import pytest

from cli_agent.outbox import Outbox
from ltms_contract import GENESIS_HASH, build_record, generate_key


@pytest.fixture
def outbox(tmp_path):
    return Outbox(tmp_path / "outbox.sqlite")


def _record(key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, summary="one"):
    return build_record(
        key, agent=agent, seq=seq, prev_hash=prev_hash, type="event", summary=summary
    )


def test_pending_returns_records_in_seq_order(outbox):
    key = generate_key()
    r2 = _record(key, seq=2, summary="two")
    r1 = _record(key, seq=1, summary="one")
    assert outbox.add(r2) is True
    assert outbox.add(r1) is True
    got = outbox.pending("z3-r0")
    assert [r["seq"] for r in got] == [1, 2]
    assert got[0]["summary"] == "one"
    assert got[1]["summary"] == "two"


def test_survives_close_and_reopen(outbox):
    key = generate_key()
    r1 = _record(key, seq=1, summary="one")
    outbox.add(r1)
    path = outbox.path
    outbox.close()
    again = Outbox(path)
    assert again.pending("z3-r0") == [r1]
    again.close()


def test_identical_add_returns_true_then_false(outbox):
    key = generate_key()
    r1 = _record(key, seq=1, summary="one")
    assert outbox.add(r1) is True
    assert outbox.add(r1) is False
    assert len(outbox.pending("z3-r0")) == 1


def test_same_seq_different_hash_raises(outbox):
    key = generate_key()
    r1 = _record(key, seq=2, summary="two")
    r1b = _record(key, seq=2, prev_hash="1" * 64, summary="two")
    outbox.add(r1)
    with pytest.raises(ValueError, match="conflict"):
        outbox.add(r1b)


def test_add_rejects_bad_records(outbox):
    with pytest.raises(ValueError):
        outbox.add("not a dict")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        outbox.add({"seq": 1, "hash": "0" * 64})  # missing agent
    with pytest.raises(ValueError):
        outbox.add({"agent": "z3-r0", "hash": "0" * 64})  # missing seq
    with pytest.raises(ValueError):
        outbox.add({"agent": "z3-r0", "seq": 1})  # missing hash
    with pytest.raises(ValueError):
        outbox.add({"agent": "z3-r0", "seq": True, "hash": "0" * 64})
    with pytest.raises(ValueError):
        outbox.add({"agent": "z3-r0", "seq": 1, "hash": 123})
    with pytest.raises(ValueError):
        outbox.add({"agent": "z3-r0", "seq": "1", "hash": "0" * 64})


def test_prune_deletes_upto_acked_and_returns_count(outbox):
    key = generate_key()
    r1 = _record(key, seq=1, summary="one")
    r2 = _record(key, seq=2, prev_hash=r1["hash"], summary="two")
    r3 = _record(key, seq=3, prev_hash=r2["hash"], summary="three")
    outbox.add(r1)
    outbox.add(r2)
    outbox.add(r3)
    assert outbox.prune("z3-r0", 2) == 2
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [3]
    assert outbox.prune("z3-r0", 2) == 0


def test_agents_sorted_and_last_highest_seq(outbox):
    key = generate_key()
    key2 = generate_key()
    outbox.add(_record(key2, agent="alpha", seq=1, summary="a"))
    outbox.add(_record(key, agent="z3-r0", seq=1, summary="z1"))
    outbox.add(_record(key, agent="z3-r0", seq=3, summary="z3"))
    assert outbox.agents() == ["alpha", "z3-r0"]
    last = outbox.last("z3-r0")
    assert last["seq"] == 3
    assert last["summary"] == "z3"
    assert outbox.last("nobody") is None


def test_pending_limit(outbox):
    key = generate_key()
    for i in (1, 2, 3):
        outbox.add(_record(key, seq=i, summary=f"r{i}"))
    got = outbox.pending("z3-r0", limit=2)
    assert [r["seq"] for r in got] == [1, 2]
