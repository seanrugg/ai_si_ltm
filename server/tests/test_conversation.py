"""Conversation page: swarm view (default) and group member view (issue #121)."""
from __future__ import annotations

from server.tests.test_swarms import Home


def _setup(mesh, db_exec):
    """echo-base swarm with a hub, z3-r0 and vector as members."""
    home = Home(mesh, db_exec)
    hub, z3, vec = (mesh.member("jetson-hub", kind="relay"),
                    mesh.member("z3-r0"),
                    mesh.member("vector"))
    home.add(z3)
    home.add(vec)
    home.appoint(hub)
    return home, hub, z3, vec


def _relay(hub, client, rec):
    """Have the hub relay one record signed by ``client`` (no local flush)."""
    client.outbox.clear()
    client.seq, client.last_hash = rec["seq"], rec["hash"]
    hub.relay([rec])


def test_swarm_view_inside_outside_and_relay(mesh, admin, db_exec):
    _home, hub, z3, vec = _setup(mesh, db_exec)
    free = mesh.member("free-agent")

    # inside, relayed by the hub
    _relay(hub, z3, z3.append("communication", summary="z3 to vector", peers=["vector"]))
    # inside, written directly
    vec.append("communication", summary="vector to z3", peers=["z3-r0"]); vec.flush()
    # outside: free agent (no provenance swarm) addressed to echo-base member z3-r0
    free.append("communication", summary="free to z3", peers=["z3-r0"]); free.flush()
    # inside, written directly
    z3.append("communication", summary="z3 to vector direct", peers=["vector"]); z3.flush()

    conv = admin.get("/api/admin/swarm-conversation",
                     params={"swarm": "echo-base", "limit": 200}).json()
    by_summary = {r["summary"]: r for r in conv["records"]}
    assert set(by_summary) == {"z3 to vector", "vector to z3", "free to z3",
                               "z3 to vector direct"}

    assert by_summary["z3 to vector"]["from_outside"] is False
    assert by_summary["z3 to vector"]["relayed_by"] == "jetson-hub"
    assert by_summary["vector to z3"]["from_outside"] is False
    assert by_summary["vector to z3"]["relayed_by"] is None
    assert by_summary["free to z3"]["from_outside"] is True
    assert by_summary["free to z3"]["relayed_by"] is None
    assert by_summary["z3 to vector direct"]["from_outside"] is False
    assert by_summary["z3 to vector direct"]["relayed_by"] is None


def test_swarm_view_free(mesh, admin, db_exec):
    _home, _hub, z3, _vec = _setup(mesh, db_exec)
    free = mesh.member("free-agent")
    free.append("communication", summary="free one", peers=["z3-r0"]); free.flush()
    free.append("communication", summary="free two", peers=["z3-r0"]); free.flush()
    # z3 belongs to echo-base, so its records are not free-agent communications
    z3.append("communication", summary="z3 inside", peers=["vector"]); z3.flush()

    conv = admin.get("/api/admin/swarm-conversation",
                     params={"swarm": "free", "limit": 200}).json()
    assert [r["summary"] for r in conv["records"]] == ["free one", "free two"]
    assert all(r["from_outside"] is False for r in conv["records"])


def test_swarm_view_paging(mesh, admin, db_exec):
    _home, _hub, z3, vec = _setup(mesh, db_exec)
    for i in range(5):
        z3.append("communication", summary=f"msg {i}", peers=["vector"]); z3.flush()

    # newest page first on load, oldest first within the page
    page1 = admin.get("/api/admin/swarm-conversation",
                      params={"swarm": "echo-base", "limit": 2}).json()
    assert len(page1["records"]) == 2
    assert [r["summary"] for r in page1["records"]] == ["msg 3", "msg 4"]

    oldest = page1["records"][0]
    before = f"{oldest['ts']},{oldest['id']}"
    page2 = admin.get("/api/admin/swarm-conversation",
                      params={"swarm": "echo-base", "limit": 2, "before": before}).json()
    assert [r["summary"] for r in page2["records"]] == ["msg 1", "msg 2"]
    # no overlap with page 1
    assert {r["summary"] for r in page2["records"]}.isdisjoint(
        {r["summary"] for r in page1["records"]})


def test_member_view_three_members(mesh, admin, db_exec):
    _home, _hub, z3, vec = _setup(mesh, db_exec)
    c = mesh.member("vector-c")
    d = mesh.member("outsider")

    z3.append("communication", summary="a to b", peers=["vector"]); z3.flush()
    vec.append("communication", summary="b to c", peers=["vector-c"]); vec.flush()
    c.append("communication", summary="c to a", peers=["z3-r0"]); c.flush()
    z3.append("communication", summary="a to outsider", peers=["outsider"]); z3.flush()
    d.append("communication", summary="outsider to a", peers=["z3-r0"]); d.flush()

    conv = admin.get("/api/admin/conversation",
                     params={"members": "z3-r0,vector,vector-c", "limit": 200}).json()
    summaries = [r["summary"] for r in conv["records"]]
    assert "a to b" in summaries
    assert "b to c" in summaries
    assert "c to a" in summaries
    assert "a to outsider" not in summaries  # peer not listed
    assert "outsider to a" not in summaries  # author not listed


def test_member_view_max_members(mesh, admin, db_exec):
    _setup(mesh, db_exec)
    names = [f"m{i}" for i in range(17)]
    r = admin.get("/api/admin/conversation",
                  params={"members": ",".join(names), "limit": 200})
    assert r.status_code == 422, r.text


def test_member_view_member_a_member_b_still_works(mesh, admin, db_exec):
    _home, _hub, z3, vec = _setup(mesh, db_exec)
    vec.append("communication", summary="Hello Z3", peers=["z3-r0"]); vec.flush()
    z3.append("communication", summary="Hello Vector", peers=["vector"]); z3.flush()

    conv = admin.get("/api/admin/conversation",
                     params={"member_a": "z3-r0", "member_b": "vector"}).json()
    assert [r["summary"] for r in conv["records"]] == ["Hello Z3", "Hello Vector"]
