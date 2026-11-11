"""Observed cognition: where a robot's decisions are made (issue #199)."""
from __future__ import annotations

from sqlalchemy import select

from server.models import AuditLog, Member
from server.services import cognition


def _observe(admin, client):
    r = admin.post(f"/api/admin/members/{client.member_id}/cognition/observe")
    assert r.status_code == 200, r.text
    return r.json()["cognition"], r.json()


def _observed(db_exec, name):
    async def go(session):
        return (await session.scalar(select(Member).where(Member.name == name))).cognition_observed
    return db_exec(go)


def test_another_member_driving_the_body_is_offboard(mesh, admin, db_exec):
    body, brain = mesh.member("robot-a"), mesh.member("brain", kind="agent")
    for i in range(15):
        brain.append("action", summary=f"drive {i}", payload={"endpoint": "robot-a/body", "cmd": "fwd"})
    brain.append("action", summary="other robot", payload={"endpoint": "robot-b/body"})
    brain.flush()
    for i in range(5):
        body.append("decision", summary=f"own {i}", payload={"choice": "stop"})
    body.flush()
    resolved, member = _observe(admin, body)
    obs = _observed(db_exec, "robot-a")
    assert obs["evidence"] == {"off": 15, "on": 5} and obs["offboard_share"] == 0.75 and obs["value"] == "offboard"
    assert obs["days"] == 7 and obs["observed_at"]
    assert resolved == {"value": "offboard", "source": "observed", "offboard_share": 0.75}


def test_directed_by_and_offboard_models(mesh, admin, db_exec):
    z3, planner = mesh.member("z3-r0"), mesh.member("planner", kind="agent")
    plan = planner.append("decision", summary="plan")
    planner.flush()
    z3.report_cognition("onboard", [{"name": "big-model", "runs_on": "workstation"},
                                    {"name": "tiny-model", "runs_on": "z3-r0"}])
    for i in range(6):
        z3.append("action", summary="directed", payload={"directed_by": f"planner:{plan['seq']}"})
    for i in range(6):
        z3.append("decision", summary="remote model", payload={"model": "big-model"})
    for i in range(8):
        z3.append("decision", summary="local model", payload={"model": "tiny-model"})
    z3.append("action", summary="self-directed", payload={"directed_by": "z3-r0:1"})
    z3.flush()
    _observe(admin, z3)
    obs = _observed(db_exec, "z3-r0")
    assert obs["evidence"] == {"off": 12, "on": 9}
    assert obs["offboard_share"] == 12 / 21 and obs["value"] == "offboard"  # at least half


def test_too_few_events_keeps_the_evidence_without_a_value(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    for i in range(5):
        z3.append("decision", summary=f"own {i}")
    z3.flush()
    resolved, _ = _observe(admin, z3)
    obs = _observed(db_exec, "z3-r0")
    assert obs["value"] is None and obs["offboard_share"] is None and obs["evidence"] == {"off": 0, "on": 5}
    assert resolved["value"] is None


def test_admin_override_wins_non_robots_skipped_and_audit(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    for i in range(25):
        z3.append("decision", summary=f"own {i}")
    z3.flush()
    _observe(admin, z3)
    r = admin.patch(f"/api/admin/members/{z3.member_id}/cognition", json={"cognition": "offboard"})
    assert r.json()["cognition"] == {"value": "offboard", "source": "admin", "offboard_share": 0.0}
    assert _observed(db_exec, "z3-r0")["value"] == "onboard"  # the observation is kept, not overwritten

    agent = mesh.member("helper", kind="agent")
    r = admin.post(f"/api/admin/members/{agent.member_id}/cognition/observe")
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_a_robot"
    assert db_exec(lambda s: cognition.observe_all(s)) == {"observed": 1}

    async def audit(session):
        return [r.detail for r in await session.scalars(
            select(AuditLog).where(AuditLog.action == "cognition_change").order_by(AuditLog.id))]
    observed = [d for d in db_exec(audit) if d["source"] == "observed"]
    assert len(observed) == 1 and observed[0]["new"] == "onboard"
