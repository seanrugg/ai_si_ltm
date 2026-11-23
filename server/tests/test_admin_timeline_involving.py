"""/admin/timeline ``involving`` (#477): a member's own records plus records naming it as a peer."""


def _populate(mesh):
    robot = mesh.member("v3-c7r", kind="robot")
    robot.append("perception", summary="body health", tags=["telemetry", "internal"])
    robot.flush()
    agent = mesh.member("w1-n6", kind="agent")
    agent.append("action", summary="mcp__vector__vector_say ok (1650 ms)", peers=["v3-c7r"], outcome="success")
    agent.append("event", summary="Hermes session started")
    agent.append("action", summary="told someone else", peers=["z3-r0"])
    agent.flush()


def _summaries(admin, **params):
    res = admin.get("/api/admin/timeline", params={"limit": 100, "type": "sensed,self_report,action,event", **params})
    assert res.status_code == 200
    return {(r["agent"], r["summary"]) for r in res.json()["records"]}


def test_involving_adds_records_that_name_the_member(mesh, admin):
    _populate(mesh)
    assert _summaries(admin, agent="v3-c7r", involving="true") == {
        ("v3-c7r", "body health"),
        ("w1-n6", "mcp__vector__vector_say ok (1650 ms)"),
    }


def test_without_involving_only_own_records(mesh, admin):
    _populate(mesh)
    assert _summaries(admin, agent="v3-c7r") == {("v3-c7r", "body health")}
    assert _summaries(admin, agent="v3-c7r", involving="false") == {("v3-c7r", "body health")}


def test_involving_keeps_other_filters_and_needs_a_member(mesh, admin):
    _populate(mesh)
    assert _summaries(admin, agent="v3-c7r", involving="true", type="action") == {
        ("w1-n6", "mcp__vector__vector_say ok (1650 ms)")}
    assert _summaries(admin, agent="v3-c7r", involving="true", q="say") == {
        ("w1-n6", "mcp__vector__vector_say ok (1650 ms)")}
    # Without a member, involving is ignored.
    assert len(_summaries(admin, involving="true")) == 4
