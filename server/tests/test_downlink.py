"""Downlink (issue #304): what a hub pulls for its swarm's bus."""
from __future__ import annotations

import pytest

from server.services import fleet_key
from server.tests.test_datasets_read import _get
from server.tests.test_swarms import Home


@pytest.fixture(autouse=True)
def fleet_key_file(app, tmp_path):
    app.state.config.fleet_key_path = str(tmp_path / "fleet-key.json")
    yield
    fleet_key._cache.clear()


def _pull(hub, cursor=None):
    params = {"since": cursor} if cursor else {}
    return _get(hub, "/api/relay/downlink", **params)


def test_hub_gets_its_swarms_and_all_swarm_directives_only(mesh, admin, db_exec):
    home, away = Home(mesh, db_exec), Home(mesh, db_exec, name="away-team")
    hub, other_hub = mesh.member("jetson-hub", kind="relay"), mesh.member("away-hub", kind="relay")
    home.appoint(hub)
    away.appoint(other_hub)
    mine = admin.post("/api/admin/directives", json={"scope": {"swarm": "echo-base"}, "text": "mine"}).json()
    admin.post("/api/admin/directives", json={"scope": {"swarm": "away-team"}, "text": "theirs"})
    every = admin.post("/api/admin/directives", json={"scope": {"all": True}, "text": "everyone"}).json()

    r = _pull(hub).json()
    assert {i["directive"]["id"] for i in r["items"]} == {mine["id"], every["id"]}
    assert r["items"][0]["directive"]["signature"]
    assert _pull(hub, r["cursor"]).json()["items"] == []  # nothing twice

    admin.post(f"/api/admin/directives/{mine['id']}/cancel")
    again = _pull(hub, r["cursor"]).json()["items"]
    assert [(i["kind"], i["id"]) for i in again] == [("cancel", mine["id"])] and again[0]["signature"]

    z3 = mesh.member("z3-r0")
    home.add(z3)
    assert _pull(z3).json()["error"]["code"] == "not_hub"


def test_messages_from_permitted_outsiders_are_delivered_as_signed(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    hub = mesh.member("jetson-hub", kind="relay")
    home.appoint(hub)
    topic = _get(hub, "/api/swarm").json()["swarm"]["topic_id"]
    cloud = mesh.member("claude", kind="assistant", permissions=("contribute", "speak:swarm"))
    nobody = mesh.member("nobody", kind="assistant")
    cloud.append("communication", summary="hello swarm", peers=[f"swarm:{topic}"])
    cloud.flush()
    nobody.append("communication", summary="let me in", peers=[f"swarm:{topic}"])
    nobody.flush()

    r = _pull(hub).json()
    msgs = [i["record"] for i in r["items"] if i["kind"] == "message"]
    assert [m["agent"] for m in msgs] == ["claude"]
    assert msgs[0]["summary"] == "hello swarm" and msgs[0]["sig"] and msgs[0]["hash"]
    cloud.append("communication", summary="second", peers=[f"swarm:{topic}"])
    cloud.flush()
    nxt = _pull(hub, r["cursor"]).json()
    assert [i["record"]["summary"] for i in nxt["items"]] == ["second"]
    assert _pull(hub, "!!notacursor").status_code == 422
