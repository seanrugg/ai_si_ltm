"""/admin/timeline's grouped `type` filter: `sensed` and `perception` (issue #181).

`sensed` means a perception tagged telemetry; `perception` means one that is
not. Everything else keeps its plain type meaning.
"""


def _populate(mesh):
    z3 = mesh.member("z3-r0")
    z3.append("perception", summary="Obstacle ahead", payload={"value": 512})
    z3.append("perception", summary="Range window", tags=["telemetry"], payload={"kind": "sensor", "sensor": "front-tof"})
    z3.append("decision", summary="Pass on the left")
    z3.append("action", summary="Drove around", outcome="success")
    z3.flush()
    return z3


def _types(admin, **params):
    res = admin.get("/api/admin/timeline", params={"limit": 100, **params})
    assert res.status_code == 200
    return {(r["type"], tuple(r.get("tags") or [])) for r in res.json()["records"]}


def test_default_hides_sensed(mesh, admin):
    _populate(mesh)
    assert ("perception", ("telemetry",)) not in _types(admin)
    assert ("perception", ()) in _types(admin)


def test_sensed_returns_only_telemetry_tagged_perceptions(mesh, admin):
    _populate(mesh)
    assert _types(admin, type="sensed") == {("perception", ("telemetry",))}


def test_perception_excludes_telemetry_tagged(mesh, admin):
    _populate(mesh)
    assert _types(admin, type="perception") == {("perception", ())}


def test_sensed_and_plain_types_or_together(mesh, admin):
    _populate(mesh)
    assert _types(admin, type="sensed,decision") == {("perception", ("telemetry",)), ("decision", ())}


def test_sensed_plus_perception_covers_all_perceptions(mesh, admin):
    _populate(mesh)
    assert _types(admin, type="sensed,perception") == {("perception", ("telemetry",)), ("perception", ())}


def test_unknown_type_is_422(mesh, admin):
    _populate(mesh)
    res = admin.get("/api/admin/timeline", params={"type": "bogus"})
    assert res.status_code == 422
    res = admin.get("/api/admin/timeline", params={"type": "decision,bogus"})
    assert res.status_code == 422
