"""Ingest: chain rules, batch policy, idempotent retries, append-only storage."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from ltms_contract import GENESIS_HASH, build_record
from server.models import Record, RecordPayload


def post(member, records):
    member._ensure_token()
    return member.http.post("/api/records", json={"records": records},
                            headers={"Authorization": f"Bearer {member.token}"})


def test_single_and_batch(mesh):
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="hello", peers=["vector"], payload={"text": "hi"})
    z3.flush()
    assert z3.seq == 1
    for i in range(5):
        z3.append("event", summary=f"tick {i}")
    res = z3.flush()[0]
    assert res["ok"] and res["accepted"] == 5 and res["last_ack_seq"] == 6
    assert z3.sync_head() == {"name": "z3-r0", "last_ack_seq": 6, "last_hash": z3.last_hash}


def test_wrong_agent_rejected(mesh):
    z3, vec = mesh.member("z3-r0"), mesh.member("vector")
    forged = build_record(z3.key, agent="vector", seq=1, prev_hash=GENESIS_HASH, type="event")
    r = post(z3, [forged]).json()
    assert not r["ok"] and r["results"][0]["code"] == "wrong_agent"
    # And another member's genuine record can't be posted with my token either.
    genuine = build_record(vec.key, agent="vector", seq=1, prev_hash=GENESIS_HASH, type="event")
    assert post(z3, [genuine]).json()["results"][0]["code"] == "wrong_agent"


def test_system_and_policy_types_are_restricted(mesh):
    z3 = mesh.member("z3-r0")
    rec = build_record(z3.key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="system")
    assert post(z3, [rec]).json()["results"][0]["code"] == "type_forbidden"
    rec = build_record(z3.key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="policy")
    assert post(z3, [rec]).json()["results"][0]["code"] == "type_forbidden"
    boss = mesh.member("operator", kind="assistant", permissions=["contribute", "policy"])
    boss.append("policy", summary="No driving after 9pm")
    assert boss.flush()[0]["accepted"] == 1


@pytest.mark.parametrize("make,code", [
    (lambda k: build_record(k, agent="z3-r0", seq=2, prev_hash="ab" * 32, type="event"), "gap"),
    (lambda k: {**build_record(k, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event"), "summary": "x"}, "hash"),
    (lambda k: {**build_record(k, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event", payload={"a": 1}),
                "payload": {"a": 2}}, "payload_hash"),
    (lambda k: build_record(k, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event",
                            ts=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()), "future_ts"),
])
def test_rejections(mesh, make, code):
    z3 = mesh.member("z3-r0")
    r = post(z3, [make(z3.key)]).json()
    assert not r["ok"] and r["results"][0]["code"] == code and r["accepted"] == 0


def test_signature_by_other_key_rejected(mesh):
    from ltms_contract import generate_key
    z3 = mesh.member("z3-r0")
    rec = build_record(generate_key(), agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event")
    assert post(z3, [rec]).json()["results"][0]["code"] == "sig"


def test_broken_prev_hash(mesh):
    z3 = mesh.member("z3-r0")
    z3.append("event"); z3.flush()
    rec = build_record(z3.key, agent="z3-r0", seq=2, prev_hash="ab" * 32, type="event")
    assert post(z3, [rec]).json()["results"][0]["code"] == "chain"


def test_valid_prefix_is_kept(mesh):
    z3 = mesh.member("z3-r0")
    good = [z3.append("event", summary=f"e{i}") for i in range(3)]
    bad = {**z3.append("event", summary="e3"), "summary": "tampered"}
    after = z3.append("event", summary="e4")
    r = post(z3, [*good, bad, after]).json()
    assert [x["status"] for x in r["results"]] == ["accepted"] * 3 + ["rejected", "not_processed"]
    assert r["last_ack_seq"] == 3 and not r["ok"]


def test_retry_is_idempotent_and_fork_is_rejected(mesh):
    z3 = mesh.member("z3-r0")
    recs = [z3.append("event", summary=f"e{i}") for i in range(3)]
    assert post(z3, recs).json()["accepted"] == 3
    again = post(z3, recs).json()
    assert again["ok"] and again["duplicates"] == 3 and again["accepted"] == 0
    fork = build_record(z3.key, agent="z3-r0", seq=2, prev_hash=recs[0]["hash"], type="event", summary="other")
    assert post(z3, [fork]).json()["results"][0]["code"] == "conflict"


def test_buffered_old_records_are_accepted(mesh):
    """A robot that was offline for a day must still be able to upload."""
    z3 = mesh.member("z3-r0")
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    z3.append("perception", ts=old, summary="seen while offline")
    assert z3.flush()[0]["accepted"] == 1


def test_batch_limits_and_permissions(mesh, app):
    z3 = mesh.member("z3-r0")
    app.state.config.max_batch = 2
    recs = [z3.append("event") for _ in range(3)]
    r = post(z3, recs)
    assert r.status_code == 413
    reader = mesh.member("reader", permissions=["recall"])
    rec = build_record(reader.key, agent="reader", seq=1, prev_hash=GENESIS_HASH, type="event")
    assert post(reader, [rec]).status_code == 403


def test_ingest_rejection_is_audited(mesh, admin):
    z3 = mesh.member("z3-r0")
    post(z3, [build_record(z3.key, agent="z3-r0", seq=5, prev_hash="ab" * 32, type="event")])
    log = admin.get("/api/admin/recall-log", params={"action": "ingest_reject"}).json()["entries"]
    assert log and log[0]["detail"]["code"] == "gap"


def test_records_table_is_append_only(mesh, db_exec):
    z3 = mesh.member("z3-r0")
    z3.append("event", summary="original"); z3.flush()
    for stmt in (update(Record).values(summary="edited"), delete(Record)):
        with pytest.raises((DBAPIError, IntegrityError)):
            db_exec(stmt)
    # Payloads, by design, can be deleted (retention).
    db_exec(delete(RecordPayload))
    assert db_exec(select(Record.summary)).scalar() == "original"


def test_thought_records_are_accepted_and_filterable(mesh):
    z3 = mesh.member("z3-r0")
    z3.append("perception", summary="Vector beeped")
    z3.append("thought", summary="Vector may want to play", refs={"because": "z3-r0:1"})
    z3.append("decision", summary="Turn toward Vector", refs={"because": "z3-r0:2"})
    z3.flush()
    got = z3.records(type="thought")
    assert [r["summary"] for r in got] == ["Vector may want to play"]
