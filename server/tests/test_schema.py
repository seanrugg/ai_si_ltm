"""config/schema.sql must match the models, and concurrent ingest must serialize."""
import asyncio

import pytest
from sqlalchemy import select

from ltms_contract import GENESIS_HASH, build_record, generate_key, public_key_hex
from scripts.dump_schema import OUT, render
from server.models import Record
from server.services import enrollment
from server.services.ingest import ingest_batch


def test_schema_sql_is_current():
    assert OUT.read_text() == render(), "run: python -m scripts.dump_schema"


def test_concurrent_batches_for_one_member_serialize(app, http, db_exec):
    """Two batches race to write seq 1. With the row lock, one wins and the other
    is told 'conflict' — never a 500 from the unique constraint."""
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("needs real row locks (set LTMS_TEST_DATABASE_URL)")
    key = generate_key()

    async def setup(s):
        m = await enrollment.request_enrollment(s, name="z3-r0", kind="robot", public_key_hex=public_key_hex(key),
                                                permissions=["contribute"], operator=None, note=None,
                                                remote_ip=None, max_pending=10)
        await enrollment.approve(s, m.id, admin="test")
        return m.id

    member_id = db_exec(setup)
    a = build_record(key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event", summary="a")
    b = build_record(key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event", summary="b")

    async def race(_session):
        async def one(rec):
            async with app.state.sessionmaker() as s:
                res = await ingest_batch(s, member_id, [rec])
                await asyncio.sleep(0.2)  # hold the lock while the other waits
                await s.commit()
                return res.results[0]
        return await asyncio.gather(one(a), one(b))

    results = db_exec(race)
    assert sorted(r["status"] for r in results) == ["accepted", "rejected"]
    assert [r.get("code") for r in results if r["status"] == "rejected"] == ["conflict"]
    assert len(db_exec(select(Record.id)).all()) == 1


def test_app_starts_with_scheduler(config):
    from fastapi.testclient import TestClient
    from server.main import create_app

    app = create_app(config.model_copy(update={"enable_scheduler": True}))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"] is True
        assert client.get("/docs").status_code == 404  # no public pages


def test_serves_built_dashboard_when_configured(config, tmp_path):
    from fastapi.testclient import TestClient
    from server.main import create_app

    (tmp_path / "index.html").write_text("<!doctype html><title>LTMS</title>")
    app = create_app(config.model_copy(update={"dashboard_dir": str(tmp_path)}))
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200 and "LTMS" in page.text
        assert page.headers["x-frame-options"] == "DENY"
        assert client.get("/api/health").json()["ok"] is True
        assert client.get("/api/admin/me").status_code == 401  # API still wins over static files
