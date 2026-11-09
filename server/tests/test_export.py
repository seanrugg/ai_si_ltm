"""Admin export of records as JSONL and CSV (issue #153)."""
from __future__ import annotations

import csv
import hashlib
import io
import json

from sqlalchemy import delete, select

from ltms_contract import canonicalize, verify_signature
from ltms_contract.record import record_signing_message
from server.models import AuditLog, RecordPayload
from server.services import export as export_service

INJECTION = '=HYPERLINK("x")'


def _jsonl(admin, **params):
    r = admin.get("/api/admin/export", params={"format": "jsonl", **params})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.headers["content-disposition"].startswith('attachment; filename="ltms-export-')
    lines = [json.loads(line) for line in r.text.splitlines()]
    assert lines[0]["kind"] == "ltms-export" and lines[0]["version"] == 1
    return lines[0], lines[1:]


def _csv(admin, **params):
    r = admin.get("/api/admin/export", params={"format": "csv", **params})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    return rows


def _seed(mesh, admin):
    z3 = mesh.member("z3-r0")
    z3.append("event", summary=INJECTION, payload={"note": "-1 starts with a minus", "n": 1}, tags=["@tag"])
    z3.append("thought", summary="plain thought", payload={"why": "because"}, place="bay 2")
    z3.flush()
    k2 = mesh.member("k2-s0")
    k2.append("communication", summary="hello", payload={"text": "hi"})
    k2.flush()
    return z3, k2


def test_jsonl_lines_verify_with_the_contract(admin, mesh):
    _seed(mesh, admin)
    _, recs = _jsonl(admin)
    assert len(recs) == 3
    for line in recs:
        header = line["header"]
        assert isinstance(header, str)
        assert hashlib.sha256(header.encode("utf-8")).hexdigest() == line["hash"]
        assert verify_signature(bytes.fromhex(line["public_key"]), record_signing_message(line["hash"]), line["sig"])
        assert hashlib.sha256(canonicalize(line["payload"])).hexdigest() == json.loads(header)["payload_hash"]
        assert line["payload_purged"] is False
        assert set(line["provenance"]) == {"swarm", "relay", "received_at"}
    # Summaries are exported unchanged in JSONL.
    assert json.loads(recs[0]["header"])["summary"] == INJECTION


def test_purged_payload_is_null_in_both_formats(admin, mesh, db_exec):
    _seed(mesh, admin)

    async def purge(session):
        first = (await session.scalars(select(RecordPayload.record_id).order_by(RecordPayload.record_id))).first()
        await session.execute(delete(RecordPayload).where(RecordPayload.record_id == first))
        await session.commit()
    db_exec(purge)
    _, recs = _jsonl(admin)
    assert recs[0]["payload"] is None and recs[0]["payload_purged"] is True
    assert recs[1]["payload_purged"] is False
    rows = _csv(admin)
    assert rows[0]["payload"] == "" and rows[0]["payload_purged"] == "true"
    assert rows[1]["payload_purged"] == "false"


def test_csv_columns_and_formula_injection(admin, mesh):
    _seed(mesh, admin)
    r = admin.get("/api/admin/export", params={"format": "csv"})
    assert r.text.splitlines()[0].split(",") == export_service.CSV_COLUMNS
    rows = _csv(admin)
    assert rows[0]["summary"] == "'" + INJECTION
    assert rows[0]["tags"] == '["@tag"]'  # JSON string starts with [, so it's left alone
    assert json.loads(rows[0]["payload"]) == {"note": "-1 starts with a minus", "n": 1}
    assert rows[1]["place"] == "bay 2"


def test_safe_cell_covers_every_risky_start():
    for ch in ("=", "+", "-", "@", "\t", "\r"):
        assert export_service.safe_cell(ch + "x") == "'" + ch + "x"
    assert export_service.safe_cell("ok") == "ok"
    assert export_service.safe_cell(None) == ""
    assert export_service.safe_cell(-3) == "'-3"


def test_filters(admin, mesh):
    _seed(mesh, admin)
    assert [json.loads(x["header"])["agent"] for x in _jsonl(admin, agent="k2-s0")[1]] == ["k2-s0"]
    assert [json.loads(x["header"])["type"] for x in _jsonl(admin, type="event,thought")[1]] == ["event", "thought"]
    assert _jsonl(admin, since="2999-01-01")[1] == []
    assert len(_jsonl(admin, until="2999-01-01T00:00:00Z")[1]) == 3
    assert _jsonl(admin, until="2000-01-01")[1] == []
    head, _ = _jsonl(admin, agent="k2-s0", type="communication")
    assert head["filters"]["agent"] == "k2-s0" and head["filters"]["type"] == ["communication"]


def test_swarm_filter(admin, mesh):
    z3 = mesh.member("z3-r0")
    k2 = mesh.member("k2-s0")
    r = admin.post("/api/admin/swarms", json={"name": "echo-base", "public_key": "ab" * 32})
    assert r.status_code == 200, r.text
    assert admin.post("/api/admin/swarms/echo-base/members", json={"member": "z3-r0", "role": "member"}).status_code == 200
    z3.append("event", summary="in the swarm", payload={})
    z3.flush()
    k2.append("event", summary="free agent", payload={})
    k2.flush()
    _, recs = _jsonl(admin, swarm="echo-base")
    assert [json.loads(x["header"])["summary"] for x in recs] == ["in the swarm"]
    assert recs[0]["provenance"]["swarm"] == "echo-base"
    assert _csv(admin, swarm="echo-base")[0]["swarm"] == "echo-base"


def test_audit_row_has_filters_but_no_content(admin, mesh, db_exec):
    _seed(mesh, admin)
    _jsonl(admin, agent="z3-r0")

    async def go(session):
        return (await session.scalars(select(AuditLog).where(AuditLog.action == "record_export"))).one()
    row = db_exec(go)
    text = json.dumps(row.detail)
    assert row.detail["filters"]["agent"] == "z3-r0" and row.detail["format"] == "jsonl"
    assert "HYPERLINK" not in text and "because" not in text


def test_more_than_one_page_arrives_once_each_in_order(admin, mesh, monkeypatch):
    monkeypatch.setattr(export_service, "BATCH", 7)  # several pages without writing 500+ records
    z3 = mesh.member("z3-r0")
    for i in range(23):
        z3.append("event", summary=f"tick {i}", payload={"i": i})
    z3.flush()
    _, recs = _jsonl(admin)
    seqs = [json.loads(x["header"])["seq"] for x in recs]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)) == 23
    assert len(_csv(admin)) == 23


def test_bad_requests_and_auth(admin, http):
    for params in ({"format": "xml"}, {"type": "nonsense"}, {"since": "yesterday"},
                   {"since": "2026-02-01", "until": "2026-01-01"}):
        r = admin.get("/api/admin/export", params={"format": "jsonl", **params} if "format" not in params else params)
        assert r.status_code == 422, params
    assert http.get("/api/admin/export").status_code == 401
