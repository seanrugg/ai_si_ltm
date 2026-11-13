"""A member's swarm identity for clients (#292): ID, topic ID, current name, role."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.models import Swarm
from server.services import swarms
from server.services.swarm_identity import topic_id_for
from server.tests.test_swarms import Home


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def test_member_gets_id_topic_id_name_and_role(mesh, home):
    z3 = mesh.member("z3-r0")
    home.add(z3, role="observer")
    s = z3.swarm()
    roster = s.pop("roster")  # #334: the swarm's active members
    assert s == {"id": home.id, "topic_id": "sw-" + home.id[:8], "name": "echo-base", "role": "observer", "leader": None}
    assert [m["name"] for m in roster] == ["z3-r0"]


def test_hub_sees_role_hub(mesh, home):
    hub = mesh.member("jetson-hub", kind="relay")
    home.appoint(hub)
    assert hub.swarm()["role"] == "hub"


def test_rename_changes_the_name_not_the_ids(mesh, home, db_exec):
    z3 = mesh.member("z3-r0")
    home.add(z3)
    before = z3.swarm()

    async def go(s):
        await swarms.rename_swarm(s, await swarms.get_swarm(s, "echo-base"), "base-two")
    db_exec(go)
    after = z3.swarm()
    assert after["name"] == "base-two"
    assert (after["id"], after["topic_id"]) == (before["id"], before["topic_id"])


def test_free_agent_gets_null(mesh):
    assert mesh.member("drifter").swarm() is None
    r = mesh.member("drifter2").http.get("/api/swarm")  # unauthenticated
    assert r.status_code == 401


def test_topic_id_collision_gives_the_newer_swarm_more_characters():
    a = "abcdef01" + "1" * 56
    b = "abcdef01" + "2" * 56
    c = "abcdef01" + "1111" + "3" * 52
    assert topic_id_for(a, []) == "sw-abcdef01"
    assert topic_id_for(b, [a]) == "sw-abcdef012222"
    assert topic_id_for(c, [a, b]) == "sw-abcdef0111113333"
    assert topic_id_for(a, [a]) == "sw-abcdef01"  # itself never counts


def test_forced_collision_in_the_database(db_exec):
    """Two stored swarms whose IDs share the first 8 hex characters: the newer one is longer."""
    import os
    from server.services.swarm_identity import topic_id
    base = os.urandom(4).hex()
    older_id, newer_id = base + "a" * 56, base + "b" * 56
    now = datetime.now(timezone.utc)

    async def go(s):
        s.add(Swarm(id=older_id, name="alpha", public_key=os.urandom(32), created_at=now - timedelta(days=1)))
        s.add(Swarm(id=newer_id, name="beta", public_key=os.urandom(32), created_at=now))
        await s.flush()
        return (await topic_id(s, await s.get(Swarm, older_id)),
                await topic_id(s, await s.get(Swarm, newer_id)))
    old_topic, new_topic = db_exec(go)
    assert old_topic == "sw-" + older_id[:8]
    assert new_topic == "sw-" + newer_id[:12]
