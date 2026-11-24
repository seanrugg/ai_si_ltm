"""One wake against the real server app (the test suite's in-process LTMS), so the
agenda's queries and the written records are checked by the API, not just the fake."""
from __future__ import annotations

from datetime import datetime, timezone

from ltms_mind.brains.base import Brain, Proposal
from ltms_mind.config import DreamCondition, MindConfig
from ltms_mind.gate import LexicalEmbedder
from ltms_mind.loop import Mind
from server.tests.conftest import admin, app, config, db_url, http, mesh  # noqa: F401 - fixtures


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
