"""Dream cycles and lesson sources (issue #240)."""
from __future__ import annotations

import pytest

from cli_agent.client import LTMSError
from server.tests.test_datasets_read import _dataset, _reads, data_dir  # noqa: F401


def _mine(member):
    return sorted(member.records(agent=member.name), key=lambda r: r["seq"])


def _cycle_records(member):
    return [(r["type"], (r.get("payload") or {})) for r in _mine(member)]


def test_a_cycle_writes_start_lessons_and_end(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    z3.login()
    did = _dataset(admin)
    slept = []
    with z3.dream_cycle(jitter_s=60, sleep=slept.append) as cycle:
        item = cycle.read(did, "drive.md")
        cycle.lesson("Reset the wheel when it stalls",
                     sources=[{"dataset": did, "version": item["version"], "item": "drive.md#stalls"}])
    assert len(slept) == 1 and 0 <= slept[0] <= 60
    recs = _cycle_records(z3)
    assert [t for t, _ in recs] == ["event", "lesson", "event"]
    start, lesson, end = (p for _, p in recs)
    assert start == {"cycle": cycle.id, "phase": "start"}
    assert lesson["cycle"] == cycle.id and lesson["sources"][0]["item"] == "drive.md#stalls"
    assert end == {"cycle": cycle.id, "phase": "end", "items_read": 1, "lessons": 1}
    assert [(k, str(c)) for k, _, c in _reads(db_exec)] == [("drive.md", cycle.id)]
    assert all("dream_cycle" in r["tags"] for r in _mine(z3) if r["type"] == "event")


def test_jitter_zero_does_not_sleep(mesh):
    z3 = mesh.member("z3-r0")
    z3.login()
    slept = []
    with z3.dream_cycle(jitter_s=0, sleep=slept.append):
        pass
    assert slept == []


def test_end_is_written_when_the_body_raises(mesh):
    z3 = mesh.member("z3-r0")
    z3.login()
    with pytest.raises(RuntimeError):
        with z3.dream_cycle(jitter_s=0) as cycle:
            cycle.lesson("half a thought")
            raise RuntimeError("battery low")
    recs = _cycle_records(z3)
    assert [t for t, _ in recs] == ["event", "lesson", "event"]
    assert recs[-1][1] == {"cycle": cycle.id, "phase": "end", "items_read": 0, "lessons": 1,
                           "error": "RuntimeError"}


SRC = {"dataset": "manuals", "version": 1, "item": "drive.md"}


@pytest.mark.parametrize("sources", [
    [SRC] * 17,
    "drive.md",
    [dict(SRC, version=0)],
    [dict(SRC, version=True)],
    [dict(SRC, version="1")],
    [dict(SRC, item="x" * 301)],
    [dict(SRC, item="")],
    [dict(SRC, extra=1)],
    [{"dataset": "manuals", "item": "drive.md"}],
])
def test_bad_sources_are_rejected(mesh, sources):
    z3 = mesh.member("z3-r0")
    z3.login()
    z3.append("lesson", summary="cites badly", payload={"sources": sources})
    with pytest.raises(LTMSError) as err:
        z3.flush()
    assert err.value.code == "bad_sources"


def test_valid_sources_are_accepted_on_any_type(mesh):
    z3 = mesh.member("z3-r0")
    z3.login()
    z3.append("lesson", summary="cites well", payload={"sources": [SRC] * 16})
    z3.append("decision", summary="also cites", payload={"sources": [dict(SRC, item="a#b" + "x" * 297)]})
    z3.flush()
    assert [r["summary"] for r in _mine(z3)] == ["cites well", "also cites"]
