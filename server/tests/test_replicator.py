"""Hub replicator: relay the outbox and prune only on LTMS acks."""
import pytest

from cli_agent.outbox import Outbox
from cli_agent.replicator import replicate_once

from server.tests.test_swarms import Home, sign


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


@pytest.fixture
def outbox(tmp_path):
    return Outbox(tmp_path / "outbox.sqlite")


def _setup(mesh, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    return hub, z3


def test_replicate_relays_and_prunes_on_ack(mesh, home, outbox):
    hub, z3 = _setup(mesh, home)
    recs = sign(z3, 3)  # three signed records, not sent
    for r in recs:
        outbox.add(r)

    res = replicate_once(hub, outbox)

    assert res["heads"]["z3-r0"]["last_ack_seq"] == 3
    assert res["errors"] == {}
    assert outbox.pending("z3-r0") == []


def test_replicate_duplicates_idempotent(mesh, home, outbox):
    hub, z3 = _setup(mesh, home)
    recs = sign(z3, 3)
    for r in recs:
        outbox.add(r)
    replicate_once(hub, outbox)  # accepted and pruned

    # add the same 3 again (same hashes) and relay once more: duplicates, no errors
    for r in recs:
        outbox.add(r)
    res = replicate_once(hub, outbox)

    assert res["errors"] == {}
    assert outbox.pending("z3-r0") == []
    assert res["heads"]["z3-r0"]["last_ack_seq"] == 3


def test_replicate_tampered_record_stops_and_keeps_rest(mesh, home, outbox):
    hub, z3 = _setup(mesh, home)
    recs = sign(z3, 3)
    recs[1]["summary"] = "changed"  # tampered after signing, not re-signed
    for r in recs:
        outbox.add(r)

    res = replicate_once(hub, outbox)

    assert res["heads"]["z3-r0"]["last_ack_seq"] == 1
    assert res["errors"]["z3-r0"]["code"] == "hash"
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [2, 3]


def test_replicate_outsider_not_pruned(mesh, home, outbox):
    hub, z3 = _setup(mesh, home)
    stranger = mesh.member("stranger")  # enrolled, but not in echo-base
    recs = sign(stranger, 1)
    for r in recs:
        outbox.add(r)

    res = replicate_once(hub, outbox)

    assert res["errors"]["stranger"]["code"] == "not_in_swarm"
    assert res["heads"] == {}
    assert outbox.pending("stranger") == [recs[0]]
