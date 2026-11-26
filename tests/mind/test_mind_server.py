"""One wake against the real server app (the test suite's in-process LTMS), so the
agenda's queries and the written records are checked by the API, not just the fake."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ltms_mind.brains.base import Brain, Proposal
from ltms_mind.config import DreamCondition, MindConfig
from ltms_mind.gate import LexicalEmbedder
from ltms_mind.loop import Mind
from server.services import fleet_key
from server.tests.conftest import admin, app, config, db_exec, db_url, http, mesh  # noqa: F401 - fixtures
from server.tests.test_swarms import Home


class Answerer(Brain):
    name = "answerer"

    def __init__(self) -> None:
        self.tasks = []

    def propose(self, task, context):
        self.tasks.append(task)
        if task.kind == "answer":
            return [Proposal("answer", "The dock is by the north wall.")]
        return []


def test_a_wake_against_the_server_answers_a_question(mesh, tmp_path):
    me = mesh.member("mind-a", kind="agent")
    asker = mesh.member("asker-b")
    asker.login()
    asker.append("communication", summary="Where is the dock?", peers=["mind-a"], tags=["question"])
    asker.flush()

    cfg = MindConfig(server="http://testserver", key_path=str(tmp_path / "unused.key.json"),
                     state_path=str(tmp_path / "mind-a.mind.json"), model_family="qwen",
                     dream=DreamCondition("never"))
    brain = Answerer()
    now = datetime.now(timezone.utc)
    out = Mind(cfg, me, brain, embedder=LexicalEmbedder(), now=lambda: now, health=lambda: {"disk": "ok"}).wake()
    assert out["written"] == 1 and out["unsent"] == 0

    asker.sync_head()
    rows = asker.records(agent="mind-a", limit=20)
    by_tag = {t: r for r in rows for t in r.get("tags") or []}
    assert by_tag["answer"]["refs"] == {"answers": "asker-b:1"} and by_tag["answer"]["peers"] == ["asker-b"]
    assert "mind_cycle" in by_tag and "health" in by_tag

    out = Mind(cfg, me, brain, embedder=LexicalEmbedder(), now=lambda: now, health=lambda: {"disk": "ok"}).wake()
    assert out["tasks"] == 0  # answered, so not on the agenda again


# ------------------------------------------------------------------ directives (#341)
@pytest.fixture
def fleet_key_file(app, tmp_path):
    app.state.config.fleet_key_path = str(tmp_path / "fleet-key.json")
    yield
    fleet_key._cache.clear()


def _get(member, path):
    member.login()
    return member.http.get(path, headers={"Authorization": f"Bearer {member.token}"})


def test_members_list_their_swarms_open_directives_and_recent_cancels(mesh, admin, db_exec, fleet_key_file):
    home, other = Home(mesh, db_exec), Home(mesh, db_exec, name="far-swarm")
    me, stranger, free = mesh.member("mind-a", kind="agent"), mesh.member("far-b"), mesh.member("free-c")
    home.add(me)
    other.add(stranger)
    mine = admin.post("/api/admin/directives", json={"scope": {"swarm": "echo-base"}, "text": "Dock by 22:00"}).json()
    every = admin.post("/api/admin/directives", json={"scope": {"all": True}, "text": "Report health"}).json()
    admin.post("/api/admin/directives", json={"scope": {"swarm": "far-swarm"}, "text": "Not for you"})
    gone = admin.post("/api/admin/directives", json={"scope": {"all": True}, "text": "Hold"}).json()
    admin.post(f"/api/admin/directives/{gone['id']}/cancel")

    items = _get(me, "/api/members/me/directives").json()["directives"]
    assert {(i["kind"], i["id"]) for i in items} == {("directive", mine["id"]), ("directive", every["id"]),
                                                    ("cancel", gone["id"])}
    d = next(i for i in items if i["id"] == mine["id"])
    assert d["statement"] == mine["statement"] and d["signature"] == mine["signature"] and d["signer"] == "fleet"
    # A free agent gets the all-swarm ones only; a stranger's swarm doesn't leak.
    free_ids = {i["id"] for i in _get(free, "/api/members/me/directives").json()["directives"]}
    assert free_ids == {every["id"], gone["id"]}
    assert _get(stranger, "/api/members/me/directives").status_code == 200


def test_a_wake_acknowledges_and_reports_on_a_directive(mesh, admin, db_exec, tmp_path, fleet_key_file):
    home = Home(mesh, db_exec)
    me = mesh.member("mind-a", kind="agent")
    home.add(me)
    d = admin.post("/api/admin/directives", json={"scope": {"swarm": "echo-base"}, "text": "Say hello"}).json()

    class Reporter(Brain):
        name = "reporter"

        def propose(self, task, context):
            if task.kind == "directive":
                return [Proposal("report", "Said hello to the swarm.", payload={"status": "done"})]
            return []

    cfg = MindConfig(server="http://testserver", key_path=str(tmp_path / "unused.key.json"),
                     state_path=str(tmp_path / "mind-a.mind.json"), model_family="claude",
                     dream=DreamCondition("never"))
    now = datetime.now(timezone.utc)
    out = Mind(cfg, me, Reporter(), embedder=LexicalEmbedder(), now=lambda: now, health=None).wake()
    assert out["duties"]["directive_accepted"] == 1 and out["written"] == 1 and out["unsent"] == 0

    (row,) = admin.get("/api/admin/directives").json()["directives"]
    assert row["delivery"]["received"] == 1 and row["delivery"]["accepted"] == 1
    rows = me.records(agent="mind-a", tag="directive-report", limit=5)
    assert rows[0]["payload"]["directive"] == d["id"] and rows[0]["refs"]["acts_on"].startswith("mind-a:")
