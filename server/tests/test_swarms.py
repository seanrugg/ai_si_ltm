"""Swarms, hub appointment, relayed ingest and provenance (issue #15)."""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from ltms_contract import generate_key, public_key_hex
from scripts import swarm_key
from server.models import Member, Record, RecordProvenance, Swarm
from server.services import swarms
from server.services.errors import ServiceError


# ------------------------------------------------------------------ helpers
class Home:
    """An echo-base swarm with a hub, built the way an operator would."""

    def __init__(self, mesh, db_exec, name="echo-base"):
        self.mesh, self.db_exec, self.name = mesh, db_exec, name
        self.root = generate_key()
        self.id = db_exec(lambda s: swarms.create_swarm(
            s, name=name, public_key_hex=public_key_hex(self.root), is_home=(name == "echo-base"))).id

    def swarm(self, s):
        return swarms.get_swarm(s, self.name)

    def add(self, client, role="member"):
        async def go(s):
            m = await s.scalar(select(Member).where(Member.name == client.name))
            await swarms.set_member(s, m, await self.swarm(s), role)
        self.db_exec(go)

    def appoint(self, client, *, root=None, ts=None):
        appt = swarm_key.appointment(root or self.root, self.id, client.name, public_key_hex(client.key), ts=ts)

        async def go(s):
            await swarms.appoint_hub(s, await self.swarm(s), appt)
        self.db_exec(go)


def provenance(db_exec, agent):
    async def go(s):
        rows = await s.execute(
            select(Record.seq, Swarm.name, Member.name)
            .join(RecordProvenance, RecordProvenance.record_id == Record.id)
            .outerjoin(Swarm, Swarm.id == RecordProvenance.swarm_id)
            .outerjoin(Member, Member.id == RecordProvenance.relay_member_id)
            .where(Record.agent == agent).order_by(Record.seq))
        return [tuple(r) for r in rows]
    return db_exec(go)


def sign(client, n, start_seq=None, prev=None, **header):
    """Sign n records for ``client`` without sending them (the robot's journal)."""
    recs = [client.append("event", summary=f"{client.name} #{i}", **header) for i in range(n)]
    client.outbox.clear()  # they travel via the hub, not via client.flush()
    client.seq, client.last_hash = recs[-1]["seq"], recs[-1]["hash"]
    return recs


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


# ------------------------------------------------------------------ done-when, from the issue
def test_hub_relays_member_records_with_provenance(mesh, admin, db_exec, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)

    recs = sign(z3, 3)
    res = hub.relay(recs)
    assert res["ok"] and res["accepted"] == 3
    assert res["heads"]["z3-r0"]["last_ack_seq"] == 3
    assert [r["agent"] for r in res["results"]] == ["z3-r0"] * 3

    # stored under Z3-R0's identity and chain, stamped with swarm + relay
    assert provenance(db_exec, "z3-r0") == [(1, "echo-base", "jetson-hub"), (2, "echo-base", "jetson-hub"),
                                             (3, "echo-base", "jetson-hub")]
    rep = {m["member"]: m for m in admin.get("/api/admin/integrity").json()["members"]}
    assert rep["z3-r0"]["status"] == "ok" and rep["z3-r0"]["records"] == 3

    # Z3-R0 can continue its own chain directly, e.g. when the Jetson is off
    z3.sync_head()
    z3.append("event", summary="direct while the hub is off")
    z3.flush()
    assert provenance(db_exec, "z3-r0")[-1] == (4, "echo-base", None)


def test_hub_own_records_and_free_agents(mesh, db_exec, home):
    hub, agent = mesh.member("jetson-hub", kind="relay"), mesh.member("claude", kind="assistant")
    home.appoint(hub)
    hub.append("decision", summary="Throttle MQTT for Vector")
    hub.flush()
    agent.append("thought", summary="Z3-R0 has been quiet today")
    agent.flush()
    assert provenance(db_exec, "jetson-hub") == [(1, "echo-base", None)]  # its own stream: no relay
    assert provenance(db_exec, "claude") == [(1, None, None)]  # free agent: no swarm, no relay


def test_relay_refuses_outsiders_and_forgeries(mesh, db_exec, home):
    hub, z3, stranger = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0"), mesh.member("stranger")
    home.add(z3)
    home.appoint(hub)

    res = hub.relay(sign(stranger, 1))
    assert res["results"][0]["code"] == "not_in_swarm"

    forged = sign(z3, 1)[0]
    forged["summary"] = "Z3-R0 says the hub may drive the car"  # altered after signing
    res = hub.relay([forged])
    assert res["results"][0]["status"] == "rejected" and res["results"][0]["code"] in ("hash", "sig")

    hub_key_forgery = dict(sign(z3, 1)[0])
    # a record re-signed by the hub under Z3-R0's name fails Z3-R0's key
    from ltms_contract import build_record
    fake = build_record(hub.key, agent="z3-r0", seq=1, prev_hash="0" * 64, type="event",
                        ts=hub_key_forgery["ts"], summary="forged by the hub", payload={})
    res = hub.relay([fake])
    assert res["results"][0]["code"] == "sig"
    assert provenance(db_exec, "z3-r0") == []


def test_moving_a_member_keeps_old_provenance(mesh, db_exec, home):
    other = Home(mesh, db_exec, name="rogue-squadron")
    z3 = mesh.member("z3-r0")
    home.add(z3)
    z3.append("event", summary="in echo-base")
    z3.flush()
    other.add(z3)  # move
    z3.append("event", summary="in rogue-squadron")
    z3.flush()
    assert provenance(db_exec, "z3-r0") == [(1, "echo-base", None), (2, "rogue-squadron", None)]


# ------------------------------------------------------------------ who may relay
def test_only_the_appointed_hub_relays(mesh, db_exec, home):
    hub, z3, vector = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0"), mesh.member("vector")
    home.add(z3)
    home.add(vector)

    # a plain member can't submit a peer's records
    res = vector.relay(sign(z3, 1))
    assert res["results"][0]["code"] == "wrong_agent"
    rewind(db_exec, z3)  # that record was never stored

    # appointments must be signed by this swarm's root key, for this member's key, recently
    for kwargs, code in (({"root": generate_key()}, "bad_appointment"),
                         ({"ts": (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()}, "stale_appointment")):
        with pytest.raises(ServiceError) as e:
            home.appoint(hub, **kwargs)
        assert e.value.code == code
    bad = swarm_key.appointment(home.root, home.id, "jetson-hub", public_key_hex(generate_key()))
    with pytest.raises(ServiceError) as e:
        db_exec(lambda s: _apply(s, home, bad))
    assert e.value.code == "bad_appointment"

    home.appoint(hub)
    assert hub.relay(sign(z3, 1))["ok"]

    # appointing a replacement ends the old hub's relay rights at once
    new_hub = mesh.member("jetson-2", kind="relay")
    home.appoint(new_hub)
    res = hub.relay(sign(z3, 1))
    assert res["results"][0]["code"] == "wrong_agent"
    perms = db_exec(lambda s: s.scalar(select(Member.permissions).where(Member.name == "jetson-hub")))
    assert "relay" not in perms
    rewind(db_exec, z3)  # refused, never stored: sign it again and send through the new hub
    assert new_hub.relay(sign(z3, 1))["ok"]


async def _apply(s, home, appt):
    await swarms.appoint_hub(s, await home.swarm(s), appt)


def rewind(db_exec, client):
    """Reset a robot's local chain state to what the server stored."""
    row = db_exec(lambda s: s.execute(select(Member.last_ack_seq, Member.last_hash)
                                      .where(Member.name == client.name)))
    client.seq, client.last_hash = row.one()


def test_revoked_swarm_or_hub_stops_relaying(mesh, admin, db_exec, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    db_exec(lambda s: _revoke(s, home))
    res = hub.relay(sign(z3, 1))
    assert res["results"][0]["code"] == "wrong_agent"
    # and the old members are free agents now
    rewind(db_exec, z3)
    z3.append("event", summary="free now")
    z3.flush()
    assert provenance(db_exec, "z3-r0") == [(1, None, None)]


async def _revoke(s, home):
    await swarms.revoke_swarm(s, await home.swarm(s))


def test_mixed_batch_and_valid_prefix(mesh, db_exec, home):
    hub, z3, vector = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0"), mesh.member("vector")
    home.add(z3)
    home.add(vector)
    home.appoint(hub)
    own = hub.append("event", summary="broker up")
    hub.outbox.clear()
    a, b = sign(z3, 2)
    v = sign(vector, 1)[0]
    bad = dict(sign(vector, 1)[0], seq=7)  # a gap: breaks Vector's chain
    after = sign(z3, 1)[0]
    res = hub.relay([own, a, v, b, bad, after])
    statuses = [(r["agent"], r["status"]) for r in res["results"]]
    assert statuses == [("jetson-hub", "accepted"), ("z3-r0", "accepted"), ("vector", "accepted"),
                        ("z3-r0", "accepted"), ("vector", "rejected"), ("z3-r0", "not_processed")]
    assert res["last_ack_seq"] == 1  # the hub's own head
    assert {k: v["last_ack_seq"] for k, v in res["heads"].items()} == {"jetson-hub": 1, "vector": 1, "z3-r0": 2}
    # resend is idempotent: duplicates, then the rest goes through
    res = hub.relay([a, b, after])
    assert [r["status"] for r in res["results"]] == ["duplicate", "duplicate", "accepted"]


def test_relay_can_read_member_heads_only_in_its_swarm(mesh, http, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    mesh.member("stranger")  # enrolled, but not in echo-base
    home.add(z3)
    home.appoint(hub)
    hub.relay(sign(z3, 2))
    assert hub.head_of("z3-r0")["last_ack_seq"] == 2
    from cli_agent.client import LTMSError
    with pytest.raises(LTMSError) as e:
        hub.head_of("stranger")
    assert e.value.code == "not_in_swarm"
    with pytest.raises(LTMSError):
        z3.head_of("jetson-hub")  # not a hub


def test_membership_rules(mesh, db_exec, home):
    z3 = mesh.member("z3-r0")
    home.add(z3, role="observer")
    home.add(z3, role="member")  # role change closes the old row
    with pytest.raises(ServiceError) as e:
        home.add(z3, role="hub")
    assert e.value.code == "bad_role"
    listing = db_exec(swarms.describe)
    assert listing[0]["name"] == "echo-base" and listing[0]["home"] is True
    assert listing[0]["members"] == [{"name": "z3-r0", "role": "member", "since": listing[0]["members"][0]["since"]}]
    with pytest.raises(ServiceError) as e:  # only one home swarm
        db_exec(lambda s: swarms.create_swarm(s, name="second-home",
                                              public_key_hex=public_key_hex(generate_key()), is_home=True))
    assert e.value.code == "home_exists"
    assert home.id == swarm_key.swarm_id(public_key_hex(home.root))


def test_manage_swarm_commands(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from cli_agent.client import LTMSClient
    from server.config import load_config
    from server.main import create_app
    from server.manage import main

    cfg = load_config(database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db", enable_scheduler=False,
                      cookie_secure=False)
    keyfile = tmp_path / "echo-base.swarm.json"
    assert swarm_key.main(["create", "echo-base", "--out", str(keyfile)]) == 0
    assert swarm_key.main(["create", "echo-base", "--out", str(keyfile)]) == 1  # never overwrite
    pub = [ln.split()[-1] for ln in capsys.readouterr().out.splitlines() if ln.startswith("public key")][0]
    assert keyfile.stat().st_mode & 0o077 == 0

    with TestClient(create_app(cfg)) as http:
        hub = LTMSClient(name="jetson-hub", signing_key=generate_key(), http=http)
        z3 = LTMSClient(name="z3-r0", signing_key=generate_key(), http=http)
        for c, kind in ((hub, "relay"), (z3, "robot")):
            c.enroll(kind, ["contribute", "recall"])
            assert main(["approve", c.name], config=cfg) == 0

        assert main(["swarm-create", "echo-base", "--public-key", pub, "--home"], config=cfg) == 0
        assert main(["swarm-add", "z3-r0", "--swarm", "echo-base"], config=cfg) == 0
        capsys.readouterr()
        assert swarm_key.main(["appoint-hub", "--key", str(keyfile), "--hub", "jetson-hub",
                               "--hub-key", public_key_hex(hub.key)]) == 0
        appt = capsys.readouterr().out
        monkeypatch.setattr("sys.stdin", io.StringIO(appt))
        assert main(["swarm-appoint-hub", "--swarm", "echo-base", "-"], config=cfg) == 0
        assert main(["swarm-list"], config=cfg) == 0
        listing = json.loads(capsys.readouterr().out.split("\n", 1)[1])
        assert {m["name"]: m["role"] for m in listing[0]["members"]} == {"jetson-hub": "hub", "z3-r0": "member"}

        assert hub.relay(sign(z3, 1))["ok"]
        assert main(["swarm-revoke", "echo-base"], config=cfg) == 1  # needs --yes
        assert main(["swarm-revoke", "echo-base", "--yes"], config=cfg) == 0
        assert main(["swarm-add", "z3-r0", "--swarm", "nowhere"], config=cfg) == 1


def test_relay_and_direct_writes_race_safely(app, mesh, db_exec, home):
    """The hub relays Z3-R0's seq 1 while Z3-R0 posts a different seq 1 directly.
    The author's row lock serializes them: one is stored, the other is a conflict.
    The hub's batch locks two authors; ordered locking means no deadlock (timeout)."""
    import asyncio

    from ltms_contract import GENESIS_HASH, build_record
    from server.services.ingest import ingest_batch

    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("needs real row locks (set LTMS_TEST_DATABASE_URL)")
    hub, z3, vector = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0"), mesh.member("vector")
    home.add(z3)
    home.add(vector)
    home.appoint(hub)
    import uuid
    ids = {c.name: uuid.UUID(c.member_id) for c in (hub, z3)}
    a = build_record(z3.key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event", summary="via hub")
    b = build_record(z3.key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event", summary="direct")
    v1 = build_record(vector.key, agent="vector", seq=1, prev_hash=GENESIS_HASH, type="event", summary="v")

    async def race(_session):
        async def one(submitter, recs):
            async with app.state.sessionmaker() as s:
                res = await ingest_batch(s, ids[submitter], recs)
                await asyncio.sleep(0.2)  # hold the locks while the other waits
                await s.commit()
                return [r for r in res.results if r["agent"] == "z3-r0"][0]
        return await asyncio.wait_for(asyncio.gather(one("jetson-hub", [v1, a]), one("z3-r0", [b])), timeout=10)

    results = db_exec(race)
    assert sorted(r["status"] for r in results) == ["accepted", "rejected"]
    assert [r.get("code") for r in results if r["status"] == "rejected"] == ["conflict"]


def test_relay_permission_only_comes_with_the_hub_role(mesh, admin, db_exec, home):
    sneaky = mesh.member("sneaky", permissions=("contribute", "relay"))  # asks for relay at enrollment
    perms = lambda name: db_exec(lambda s: s.scalar(select(Member.permissions).where(Member.name == name)))  # noqa: E731
    assert "relay" not in perms("sneaky")
    r = admin.patch(f"/api/admin/members/{sneaky.member_id}/permissions",
                    json={"permissions": ["contribute", "relay"]})
    assert r.status_code == 200 and "relay" not in perms("sneaky")

    hub = mesh.member("jetson-hub", kind="relay")
    home.appoint(hub)
    # editing the hub's other permissions keeps relay while it's the hub
    r = admin.patch(f"/api/admin/members/{hub.member_id}/permissions",
                    json={"permissions": ["contribute", "recall:scoped"], "scope": {"types": ["lesson"]}})
    assert r.status_code == 200 and perms("jetson-hub") == ["contribute", "recall:scoped", "relay"]
