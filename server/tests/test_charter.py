"""The fleet charter: publish, required reading, the write gate (issue #255)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from cli_agent.client import LTMSError
from server.models import AuditLog, DatasetRead, Member
from server.tests.test_datasets_read import data_dir  # noqa: F401
from server.tests.test_swarms import Home

V1 = "# Fleet charter\n\n## Article 1\n\nHumans stay in charge.\n"
V2 = V1 + "\n## Article 2\n\nReport incidents.\n"


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _publish(admin, text):
    r = admin.put("/api/admin/charter", content=text.encode(), headers={"Content-Type": "text/markdown"})
    assert r.status_code == 200, r.text
    return r.json()


def _quiet(member):
    """A client that doesn't read the charter by itself, to test the gate."""
    member.auto_read_charter = False
    member.login()
    return member


def _rejected_code(member):
    with pytest.raises(LTMSError) as err:
        member.flush()
    return err.value.code


def _age_enrollment(db_exec, name, days):
    async def go(session):
        await session.execute(update(Member).where(Member.name == name).values(
            enrolled_at=datetime.now(timezone.utc) - timedelta(days=days)))
    db_exec(go)


def test_no_charter_no_gate(mesh):
    z3 = _quiet(mesh.member("z3-r0"))
    z3.append("event", summary="hello")
    z3.flush()
    assert "charter" not in z3.sync_head()


def test_publish_hash_and_identical_is_a_noop(admin, db_exec):
    first = _publish(admin, V1)
    assert first["published"] and first["version"] == 1
    assert first["hash"] == hashlib.sha256(V1.encode()).hexdigest()
    again = _publish(admin, V1)
    assert not again["published"] and again["version"] == 1
    assert _publish(admin, V2)["version"] == 2
    rows = db_exec(lambda s: s.scalars(select(AuditLog.detail).where(AuditLog.action == "charter_publish")))
    assert [d["version"] for d in rows] == [1, 2]
    # JSON works too, and the charter stays out of the dataset catalog.
    r = admin.put("/api/admin/charter", json={"markdown": V2})
    assert r.json()["published"] is False
    assert admin.get("/api/admin/datasets").json()["datasets"] == []
    assert admin.post("/api/admin/datasets", json={"name": "fleet-charter", "kind": "upload",
                                                   "licence": "x"}).json()["error"]["code"] == "name_taken"


def test_a_new_member_must_read_before_writing(admin, mesh, db_exec):
    _publish(admin, V1)
    z3 = _quiet(mesh.member("z3-r0"))
    head = z3.sync_head()
    assert head["charter"]["read"] is False and head["charter"]["version"] == 1
    z3.append("event", summary="first words")
    assert _rejected_code(z3) == "charter_unread"
    assert z3.records(agent="z3-r0") == []  # recall still works, nothing stored
    got = z3.read_charter()
    assert got["markdown"] == V1 and got["version"] == 1
    z3.flush()
    assert z3.sync_head()["charter"] == {"version": 1, "hash": got["hash"], "read": True, "due": None}
    reads = db_exec(lambda s: s.scalars(select(DatasetRead.item_key)))
    assert list(reads) == ["charter.md"]


def test_the_client_reads_by_itself(admin, mesh):
    _publish(admin, V1)
    z3 = mesh.member("z3-r0")  # auto_read_charter is on by default
    z3.login()
    z3.append("event", summary="hello")
    z3.flush()
    # And after an update, a rejected flush reads the new version and sends again.
    _publish(admin, V2)
    z3.append("event", summary="after the update")
    z3.flush()


def test_existing_members_get_a_grace_period(admin, mesh, db_exec, monkeypatch):
    _publish(admin, V1)
    z3 = _quiet(mesh.member("z3-r0"))
    z3.read_charter()
    _age_enrollment(db_exec, "z3-r0", 30)
    _publish(admin, V2)
    head = z3.sync_head()["charter"]
    assert head["read"] is False and head["version"] == 2
    due = datetime.fromisoformat(head["due"])
    assert timedelta(days=13) < due - datetime.now(timezone.utc) <= timedelta(days=14)
    z3.append("event", summary="still within the grace period")
    z3.flush()

    # 15 days later the writes pause until it reads v2.
    import server.services.ingest as ingest
    real = ingest.datetime

    class Later(real):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + timedelta(days=15)
    monkeypatch.setattr(ingest, "datetime", Later)
    z3.append("event", summary="after the grace period", ts=datetime.now(timezone.utc).isoformat())
    assert _rejected_code(z3) == "charter_unread"
    z3.read_charter()
    z3.flush()


def test_a_relayed_record_is_gated_on_its_author(admin, mesh, db_exec, home):
    _publish(admin, V1)
    hub = _quiet(mesh.member("jetson-hub", permissions=("contribute", "recall", "relay")))
    vector = _quiet(mesh.member("vector"))
    home.add(hub)
    home.add(vector)
    home.appoint(hub)
    hub.read_charter()  # the hub has read it; the author hasn't
    rec = vector.append("event", summary="relayed")
    vector.outbox.clear()
    res = hub.relay([rec])
    assert not res["ok"] and res["results"][0]["code"] == "charter_unread"
    vector.read_charter()
    assert hub.relay([rec])["ok"]


def test_read_status_lists_who_has_and_hasnt(admin, mesh):
    _publish(admin, V1)
    a, b = _quiet(mesh.member("z3-r0")), _quiet(mesh.member("vector"))
    a.read_charter()
    view = admin.get("/api/admin/charter").json()
    assert view["current"]["markdown"] == V1 and view["grace_days"] == 14
    rows = {m["name"]: m for m in view["members"]}
    assert rows["z3-r0"]["read"] and rows["z3-r0"]["read_at"] and rows["z3-r0"]["due"] is None
    assert not rows["vector"]["read"] and rows["vector"]["overdue"]  # joined after: due at once
    _publish(admin, V2)
    assert [v["version"] for v in admin.get("/api/admin/charter").json()["versions"]] == [2, 1]
    assert admin.get("/api/admin/charter/versions/1").json()["markdown"] == V1


def test_limits(admin, mesh):
    assert admin.put("/api/admin/charter", content=b"   ").json()["error"]["code"] == "bad_charter"
    r = admin.put("/api/admin/charter", content=b"x" * (256 * 1024 + 1))
    assert r.status_code == 413
    z3 = _quiet(mesh.member("z3-r0"))
    r = z3.http.get("/api/charter", headers={"Authorization": f"Bearer {z3.token}"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_charter"
