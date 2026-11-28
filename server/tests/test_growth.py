"""The growth ledger and mind settings (#342): learning, not volume."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from server.models import AuditLog


def lesson(m, summary, family, evidence=None):
    m.append("lesson", summary=summary, tags=["mind"], refs={"evidence": evidence or ["q3-u3:56"]},
             payload={"model_family": family})
    return f"{m.name}:{m.outbox[-1]['seq']}"


def review(m, target, verdict, family):
    m.append("thought", summary=f"Review of {target}", tags=["mind", "review"], refs={"reviews": target},
             payload={"verdict": verdict, "reasons": "checked", "model_family": family})


def growth(admin, m, days=30):
    r = admin.get(f"/api/admin/members/{m.member_id}/growth", params={"days": days})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def fleet(mesh):
    vec = mesh.member("v3-c7r")                                    # Qwen
    mac = mesh.member("m4-c2", kind="agent")                       # Gemma
    q3 = mesh.member("q3-u3")                                      # another Qwen
    return vec, mac, q3


def test_a_lesson_counts_as_verified_only_by_another_model_family(admin, fleet):
    vec, mac, q3 = fleet
    cross = lesson(vec, "show_icon takes five icon names", "qwen")
    same = lesson(vec, "Ramps over 12 degrees stall", "qwen")
    disputed = lesson(vec, "Dust lowers lidar range", "qwen")
    lesson(vec, "Never reviewed", "qwen")
    vec.flush()
    review(mac, cross, "verified", "gemma")
    review(q3, same, "verified", "qwen")                            # same family: counted, never decides
    review(mac, disputed, "verified", "gemma")
    mac.flush()
    q3.flush()
    g = growth(admin, vec)
    L = g["lessons"]
    assert L["proposed"] == 4 and L["verified"] == 2 and L["unreviewed"] == 2 and L["same_family_reviews"] == 1
    assert g["families"] == ["qwen"]
    assert g["learning_ratio"] == round(2 / g["volume"]["records"], 6)
    m = growth(admin, mac)["reviews_given"]
    assert m == {"total": 2, "verified": 2, "refuted": 0, "unsure": 0, "cross_family": 2}
    assert growth(admin, q3)["reviews_given"]["cross_family"] == 0


def test_refuted_wins_when_other_families_disagree_the_same_day(admin, mesh, fleet):
    vec, mac, _ = fleet
    glm = mesh.member("g1-m4", kind="agent")
    k = lesson(vec, "Dust lowers lidar range", "qwen")
    vec.flush()
    review(mac, k, "verified", "gemma")
    mac.flush()
    review(glm, k, "refuted", "glm")
    glm.flush()
    L = growth(admin, vec)["lessons"]
    assert L["refuted"] == 1 and L["verified"] == 0


def test_family_is_the_one_used_at_the_time(admin, fleet):
    """M4-C2 switches from Gemma to Qwen: its earlier Gemma reviews still count as cross-family."""
    vec, mac, _ = fleet
    k1 = lesson(vec, "First lesson", "qwen")
    k2 = lesson(vec, "Second lesson", "qwen")
    vec.flush()
    review(mac, k1, "verified", "gemma")
    review(mac, k2, "verified", "qwen")                             # after the switch
    mac.flush()
    assert growth(admin, vec)["lessons"]["verified"] == 1
    assert growth(admin, mac)["families"] == ["gemma", "qwen"]


def test_a_lesson_cited_by_an_in_force_policy_is_promoted(admin, mesh, fleet):
    vec, _, _ = fleet
    k = lesson(vec, "Slow down near the dock", "qwen")
    vec.flush()
    lead = mesh.member("l3-ad", kind="agent", permissions=["contribute", "recall", "policy"])
    lead.append("policy", summary="Approach the dock slowly", refs={"from": k})
    lead.flush()
    assert growth(admin, vec)["lessons"]["promoted"] == 1


def test_predictions_questions_directives_and_energy(admin, fleet):
    vec, mac, q3 = fleet
    vec.append("thought", summary="Battery dies within 80 min off dock", tags=["mind", "prediction"],
               payload={"claim": "dies", "measure": "minutes", "check_after": "2026-12-01T00:00:00Z",
                        "probability": 0.7, "model_family": "qwen"})
    pred = f"v3-c7r:{vec.outbox[-1]['seq']}"
    vec.append("thought", summary="Unscored guess", tags=["mind", "prediction"],
               payload={"claim": "x", "measure": "y", "check_after": "2026-12-01T00:00:00Z"})
    vec.append("communication", summary="Which icon did the caller ask for?", tags=["mind", "question"],
               peers=["q3-u3"])
    q = f"v3-c7r:{vec.outbox[-1]['seq']}"
    vec.append("communication", summary="directive x: done", tags=["mind", "directive-report"],
               payload={"directive": "x", "status": "done"})
    vec.append("communication", summary="directive y: couldnt", tags=["mind", "directive-report"],
               payload={"directive": "y", "status": "couldnt", "reason": "no light"})
    for oc in ("powered-off", "powered-off", "docked"):
        vec.append("event", summary="off-dock episode ended", tags=["body", "energy", "energy-outcome"],
                   payload={"outcome": oc})
    vec.flush()
    mac.append("thought", summary="Checked", tags=["mind", "prediction-check"], refs={"checks": pred},
               payload={"outcome": "true", "evidence": ["v3-c7r:1"]})
    mac.flush()
    q3.append("communication", summary="The heart icon", tags=["mind", "answer"], refs={"answers": q},
              peers=["v3-c7r"])
    q3.flush()
    g = growth(admin, vec)
    assert g["predictions"] == {"made": 2, "checked": 1, "true": 1, "false": 0, "unclear": 0, "brier": 0.09}
    assert g["questions"] == {"asked": 1, "answered_by_others": 1}
    assert g["directives"] == {"reported": 2, "done": 1, "partial": 0, "couldnt": 1}
    assert g["energy"] == {"powered_off": 2, "docked": 1}
    assert growth(admin, q3)["answers"]["given"] == 1 and "energy" not in growth(admin, q3)


def test_mind_cycles_and_actions_add_up(admin, fleet):
    vec, _, _ = fleet
    vec.append("event", summary="Mind cycle (awake)", tags=["mind_cycle"],
               payload={"mode": "awake", "brain": "none", "tasks": 1, "gated": {}, "budget": {"tokens": 0}})
    vec.append("event", summary="Dream cycle started", tags=["dream_cycle"], payload={"phase": "start"})
    vec.append("event", summary="Dream cycle ended", tags=["dream_cycle", "mind_cycle"], payload={
        "phase": "end", "mind": {"mode": "dreaming", "written": 2, "brain_errors": 1, "budget": {"tokens": 1198},
                                 "gated": {"bad_proposal": 1, "bad_ref": 2},
                                 "rejected": [{"reason": "bad_ref"}, {"reason": "bad_ref"}, {"reason": "brain_error"}]}})
    vec.append("action", summary="drive_on_charger", outcome="success")
    vec.append("action", summary="drive_on_charger", outcome="failed")
    vec.flush()
    g = growth(admin, vec)
    assert g["cycles"] == {"count": 2, "dreams": 1, "paused": 0, "written": 2, "brain_errors": 1, "tokens": 1198,
                           "gated": {"bad_proposal": 1, "bad_ref": 2}, "rejected": {"bad_ref": 2, "brain_error": 1}}
    assert g["actions"] == {"total": 2, "outcomes": {"success": 1, "failed": 1}}
    assert g["volume"]["records"] == 5


def test_swarm_and_fleet_roll_ups_are_sums(admin, mesh, fleet):
    vec, mac, q3 = fleet
    k = lesson(vec, "show_icon takes five icon names", "qwen")
    vec.flush()
    review(mac, k, "verified", "gemma")
    mac.flush()
    r = admin.get("/api/admin/growth")
    assert r.status_code == 200, r.text
    total = r.json()
    each = [growth(admin, m) for m in (vec, mac, q3)]
    assert total["lessons"]["verified"] == sum(g["lessons"]["verified"] for g in each) == 1
    assert total["volume"]["records"] == sum(g["volume"]["records"] for g in each)
    assert total["members"][0] == {"name": "v3-c7r", "learning_ratio": each[0]["learning_ratio"],
                                   "verified_lessons": 1}
    assert total["families"] == ["gemma", "qwen"]
    assert admin.get("/api/admin/swarms/nowhere/growth").status_code == 404


def test_mind_settings_are_audited_and_reach_the_member_at_sign_in(admin, fleet, db_exec):
    vec, _, _ = fleet
    assert "mind" not in vec.login()                               # nothing set yet
    url = f"/api/admin/members/{vec.member_id}/mind"
    assert admin.get(url).json() == {"paused": False, "budget": {}}
    r = admin.patch(url, json={"paused": True, "budget": {"calls_per_wake": 3, "writes_per_day": 5}})
    assert r.status_code == 200, r.text
    assert vec.login()["mind"] == {"paused": True, "budget": {"calls_per_wake": 3, "writes_per_day": 5}}
    assert admin.patch(url, json={"budget": {"calls_per_wake": None}}).json()["budget"] == {"writes_per_day": 5}
    for bad in ({"paused": "yes"}, {"budget": {"calls_per_wake": -1}}, {"budget": {"turbo": 1}}, {"other": 1}):
        assert admin.patch(url, json=bad).status_code == 422, bad
    rows = db_exec(lambda s: s.scalars(select(AuditLog).where(AuditLog.action == "mind_settings_change")))
    assert len(list(rows)) == 2


def test_growth_needs_an_admin(http, fleet):
    vec, _, _ = fleet
    for url in (f"/api/admin/members/{vec.member_id}/growth", "/api/admin/growth",
                f"/api/admin/members/{vec.member_id}/mind"):
        assert http.get(url).status_code == 401
