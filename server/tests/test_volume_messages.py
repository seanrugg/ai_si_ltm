"""Volume page: swarm messages vs all records, per swarm (issue #302)."""
from __future__ import annotations

from server.models import AuditLog
from server.tests.test_swarms import Home


def test_swarm_message_share_and_summarised(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    z3 = mesh.member("z3-r0")
    home.add(z3)
    for _ in range(3):
        z3.append("communication", summary="hi", tags=["swarm-message"], peers=["vector"])
    z3.append("event", summary="tick")
    z3.flush()

    async def on(session):
        session.add(AuditLog(actor="system:lrs-forward", action="lrs_summary_on", detail={"member": "z3-r0"}))
    db_exec(on)

    (s,) = admin.get("/api/admin/volume", params={"days": 7}).json()["swarms"]
    assert s["swarm"] == "echo-base" and s["records"] == 4 and s["messages"] == 3 and s["share"] == 0.75
    assert len(s["days_over_half"]) == 1 and s["summarised"] == ["z3-r0"]
