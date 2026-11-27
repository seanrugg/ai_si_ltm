"""Member spec sheet (issue #332)."""
from __future__ import annotations

SHEET = {"hardware": {"board": "Jetson Orin Nano", "cores": 6, "ram_mib": 7619, "accelerators": ["Ampere GPU"]},
         "models": [{"name": "qwen2.5:1.5b", "quant": "q4_K_M", "runtime": "ollama", "where": "onboard"}],
         "software": {"os": "Ubuntu 22.04", "harness": "ltms-harness", "harness_version": "0.1"},
         "notes": "bench unit"}


def _put(member, body, path="/api/members/me/specs"):
    member.login()
    return member.http.put(path, json=body, headers={"Authorization": f"Bearer {member.token}"})


def _audits(db_exec):
    from sqlalchemy import func, select

    from server.models import AuditLog

    async def go(session):
        return await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "specs_change"))
    return db_exec(go)


def test_report_resolve_override_and_revert(mesh, admin, db_exec):
    hub = mesh.member("jetson-hub")
    r = _put(hub, SHEET)
    assert r.status_code == 200, r.text
    s = admin.get(f"/api/admin/members/{hub.member_id}/specs").json()["specs"]
    assert s["hardware"]["value"]["board"] == "Jetson Orin Nano" and s["hardware"]["source"] == "declared"
    assert s["hardware"]["reported_at"] and s["models"]["value"][0]["quant"] == "q4_K_M"

    p = admin.patch(f"/api/admin/members/{hub.member_id}/specs", json={"hardware": {"ram_mib": 8192}})
    hw = p.json()["specs"]["hardware"]
    assert hw["value"]["ram_mib"] == 8192 and hw["source"] == "mixed" and hw["admin_fields"] == ["ram_mib"]
    assert hw["declared"]["ram_mib"] == 7619  # never edits what the device reported
    hw = admin.patch(f"/api/admin/members/{hub.member_id}/specs", json={"hardware": {"ram_mib": None}}).json()["specs"]["hardware"]
    assert hw["value"]["ram_mib"] == 7619 and hw["source"] == "declared"


def test_identical_report_is_a_no_op_and_history_lists_changes(mesh, admin, db_exec):
    hub = mesh.member("jetson-hub")
    _put(hub, SHEET)
    _put(hub, SHEET)
    assert _audits(db_exec) == 1
    _put(hub, {**SHEET, "notes": "moved to the shelf"})
    assert _audits(db_exec) == 2
    h = admin.get(f"/api/admin/members/{hub.member_id}/specs/history").json()
    assert h["total"] == 2 and h["events"][0]["sections"] == ["notes"]
    assert h["events"][0]["new"] == {"notes": "moved to the shelf"} and h["events"][0]["actor"] == "jetson-hub"


def test_cognition_models_flow_into_the_spec_sheet(mesh, admin):
    v = mesh.member("vector")
    r = _put(v, {"location": "offboard", "models": [{"name": "qwen2.5:1.5b", "runs_on": "escapepod"}]},
             "/api/members/me/cognition")
    assert r.status_code == 200, r.text
    m = admin.get(f"/api/admin/members/{v.member_id}/specs").json()["specs"]["models"]
    assert m["value"] == [{"name": "qwen2.5:1.5b", "where": "offboard", "runtime": "escapepod"}]


def test_a_report_keeps_the_sections_it_leaves_out(mesh, admin, db_exec):
    """The sidecar's order: cognition writes the models at start, then the robot's
    sheet (hardware, software, notes) arrives on connect. The models must survive."""
    v = mesh.member("vector")
    _put(v, {"location": "offboard", "models": [{"name": "qwen2.5:1.5b", "runs_on": "escapepod"}]},
         "/api/members/me/cognition")
    r = _put(v, {"hardware": {"board": "Anki/DDL Vector"}, "software": {"harness": "robot-sidecar"},
                 "notes": "counts"})
    assert r.status_code == 200, r.text
    s = admin.get(f"/api/admin/members/{v.member_id}/specs").json()["specs"]
    assert s["models"]["value"] == [{"name": "qwen2.5:1.5b", "where": "offboard", "runtime": "escapepod"}]
    assert s["hardware"]["value"] == {"board": "Anki/DDL Vector"}
    h = admin.get(f"/api/admin/members/{v.member_id}/specs/history").json()
    assert all("models" not in e["sections"] for e in h["events"][:1])   # the sheet didn't touch models
    _put(v, {"hardware": {"board": "Anki/DDL Vector"}, "software": {"harness": "robot-sidecar"},
             "notes": "counts"})
    assert admin.get(f"/api/admin/members/{v.member_id}/specs/history").json()["total"] == h["total"]  # no-op


def test_a_section_sent_as_null_is_cleared_and_a_sent_section_replaced(mesh, admin):
    hub = mesh.member("jetson-hub")
    _put(hub, SHEET)
    r = _put(hub, {"models": None, "hardware": {"board": "Jetson Orin Nano Super"}})
    assert r.status_code == 200, r.text
    s = r.json()["specs"]
    assert s["models"]["value"] is None and s["models"]["source"] is None
    assert s["hardware"]["value"] == {"board": "Jetson Orin Nano Super"}   # a sent section is replaced whole
    assert s["software"]["value"]["harness"] == "ltms-harness" and s["notes"]["value"] == "bench unit"
    assert _put(hub, {"hardware": {"board": None}}).status_code == 422     # null fields are admin-only


def test_validation_and_auth(mesh, admin, http):
    hub = mesh.member("jetson-hub")
    for body, code in (({"gpu": {}}, 422), ({"hardware": {"color": "red"}}, 422),
                       ({"hardware": {"board": "x" * 65}}, 422),
                       ({"hardware": {"sensors": ["a"] * 17}}, 422),
                       ({"models": [{"name": "m", "where": "cloud"}]}, 422),
                       ({"notes": "x" * 501}, 422),
                       ({"models": [{"name": "m" * 60, "role": "r" * 60, "runtime": "x" * 60,
                                     "params": "p" * 60, "quant": "q" * 60}] * 16,
                         "hardware": {k: ["s" * 60] * 16 for k in ("sensors", "actuators", "accelerators")}}, 413)):
        assert _put(hub, body).status_code == code, body
    assert http.put("/api/members/me/specs", json=SHEET).status_code == 401
    assert http.get(f"/api/admin/members/{hub.member_id}/specs").status_code in (401, 403)
