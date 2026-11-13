"""Swarms list metrics and swarm filters (issue #330)."""
from __future__ import annotations

from sqlalchemy import event

from server.tests.test_swarms import Home, sign


def test_metrics_and_filters_across_two_swarms_and_a_free_agent(mesh, admin, db_exec, app):
    home = Home(mesh, db_exec)
    away = Home(mesh, db_exec, name="away-team")
    hub, z3, cam = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0"), mesh.member("cam")
    rover, free = mesh.member("rover"), mesh.member("claude", kind="assistant")
    home.add(z3)
    home.add(cam, role="observer")
    home.appoint(hub)
    away.add(rover)
    hub.relay(sign(z3, 2))
    rover.append("event", summary="away")
    rover.flush()
    free.append("thought", summary="free")
    free.flush()

    # Count queries: describe() must not grow with the number of swarms.
    queries = []
    engine = app.state.engine.sync_engine
    listen = lambda *a, **k: queries.append(1)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listen)
    try:
        swarms = {s["name"]: s for s in admin.get("/api/admin/swarms").json()}
    finally:
        event.remove(engine, "before_cursor_execute", listen)
    assert len(queries) <= 8

    m = swarms["echo-base"]["metrics"]
    assert (m["hubs"], m["members"], m["observers"]) == (1, 1, 1)
    assert m["records_24h"] == 2 and m["records_7d"] == 2 and m["last_record_at"] and m["hub_last_seen_at"]
    a = swarms["away-team"]["metrics"]
    assert (a["hubs"], a["members"], a["records_24h"], a["hub_last_seen_at"]) == (0, 1, 1, None)

    tl = admin.get("/api/admin/timeline", params={"swarm": "echo-base"}).json()["records"]
    assert {r["agent"] for r in tl} == {"z3-r0"}
    vol = admin.get("/api/admin/volume", params={"swarm": "away-team"}).json()["rows"]
    assert {r["agent"] for r in vol} == {"rover"}
    assert admin.get("/api/admin/timeline", params={"swarm": "nope"}).json()["error"]["code"] == "no_swarm"
    assert admin.get("/api/admin/volume", params={"swarm": "nope"}).status_code == 404
