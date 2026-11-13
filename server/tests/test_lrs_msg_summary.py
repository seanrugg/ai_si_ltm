"""Swarm-message summaries to the LRS (issue #301). LTMS keeps every message."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from server.config import load_config
from server.models import AuditLog, Record
from server.services import lrs as lrs_service
from server.tests.conftest import ADMIN_PASS, ADMIN_USER
from server.tests.test_lrs_forward import _endpoint, _endpoint_row, _forward
from server.xapi.translate import EXT

# Two days ahead of the real clock: members stamp ts from the real clock, and ingest
# rejects ts more than 300s ahead of received_at, so the fake clock must stay ahead.
T = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=12, minute=30, second=0, microsecond=0)


@pytest.fixture
def config(db_url):
    return load_config(
        database_url=db_url, admin_username=ADMIN_USER, admin_password=ADMIN_PASS, cookie_secure=False,
        enable_scheduler=False, ingest_rate=1000, recall_rate=1000, secret_key=Fernet.generate_key().decode(),
        lrs_batch_size=100, lrs_max_batches_per_run=3, public_base_url="https://ltms.example.org",
        lrs_msg_summary_per_hour=2, lrs_msg_summary_sustain_hours=2, lrs_msg_summary_window_minutes=5)


class _Clock:
    at = T


class _FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.at


@pytest.fixture(autouse=True)
def received_clock(monkeypatch):
    """Records are append-only, so set received_at by controlling ingest's clock."""
    from server.services import ingest
    monkeypatch.setattr(ingest, "datetime", _FakeDatetime)


def _say_at(member, times, db_exec, peers=("vector",)):
    """One swarm message received at each time; returns their record ids."""
    for at in times:
        _Clock.at = at
        member.append("communication", summary="msg", tags=["swarm-message"], peers=list(peers))
        member.flush()

    async def go(session):
        rows = await session.scalars(select(Record.id).where(Record.agent == member.name).order_by(Record.id))
        return list(rows)[-len(times):]
    return db_exec(go)


def _capture():
    sent = []

    def handler(req):
        sent.extend(json.loads(req.content))
        from httpx import Response
        return Response(200, json=[])
    return sent, handler


def _summaries(sent):
    return [s for s in sent if s["object"]["definition"]["type"].endswith("/message-summary")]


def test_noisy_member_is_summarised_per_window_and_open_windows_wait(admin, mesh, http, app, db_exec, monkeypatch):
    _endpoint(admin, "lrs", scope={"types": ["communication"]})
    z3, quiet = mesh.member("z3-r0"), mesh.member("vector")
    ten, eleven, noon = T.replace(hour=10, minute=10), T.replace(hour=11, minute=10), T.replace(hour=12, minute=0)
    ids = _say_at(z3, [ten + timedelta(minutes=i) for i in range(3)]
                  + [eleven + timedelta(minutes=i) for i in range(3)]
                  + [noon + timedelta(minutes=i) for i in range(4)] + [T.replace(minute=27)], db_exec)
    _say_at(quiet, [T.replace(minute=28)], db_exec)

    monkeypatch.setattr(lrs_service, "_now", lambda: T - timedelta(minutes=1))  # 12:29: the 12:25 window is open
    sent, handler = _capture()
    _forward(app, http, handler)
    summaries = _summaries(sent)
    assert [s["result"]["extensions"][EXT + "message-summary/count"] for s in summaries] == [3, 3, 4]
    first = summaries[0]["result"]["extensions"]
    assert first[EXT + "message-summary/first"] == "z3-r0:1" and first[EXT + "message-summary/last"] == "z3-r0:3"
    assert first[EXT + "message-summary/peers"] == ["vector"]
    assert first[EXT + "message-summary/window-start"] == T.replace(hour=10, minute=10).isoformat()
    # The 12:25 window is still open, so the batch stops before it (and before vector's later message).
    assert _endpoint_row(db_exec, "lrs").cursor == ids[9]

    monkeypatch.setattr(lrs_service, "_now", lambda: T + timedelta(minutes=10))
    sent.clear()
    _forward(app, http, handler)
    assert [s["result"]["extensions"][EXT + "message-summary/count"] for s in _summaries(sent)] == [1]
    plain = [s for s in sent if s not in _summaries(sent)]
    assert [s["actor"]["account"]["name"] for s in plain] == ["vector"]  # under the limit: one each

    async def audits(session):
        return [(a.action, a.detail["member"]) for a in await session.scalars(
            select(AuditLog).where(AuditLog.action.in_(("lrs_summary_on", "lrs_summary_off"))).order_by(AuditLog.id))]
    assert db_exec(audits) == [("lrs_summary_on", "z3-r0")]


def test_a_quiet_hour_ends_summary_mode(admin, mesh, http, app, db_exec, monkeypatch):
    _endpoint(admin, "lrs", scope={"types": ["communication"]})
    z3 = mesh.member("z3-r0")
    _say_at(z3, [T.replace(hour=10, minute=i) for i in range(3)] + [T.replace(hour=11, minute=i) for i in range(3)],
            db_exec)
    monkeypatch.setattr(lrs_service, "_now", lambda: T)
    sent, handler = _capture()
    _forward(app, http, handler)
    assert len(_summaries(sent)) == 2

    # One message in the 12:00 hour (under the limit), so at 13:00 the last full hour was quiet.
    _say_at(z3, [T.replace(hour=12, minute=59) + timedelta(seconds=59.5)], db_exec)
    monkeypatch.setattr(lrs_service, "_now", lambda: T.replace(hour=13))
    sent.clear()
    _forward(app, http, handler)
    assert _summaries(sent) == [] and len(sent) == 1

    async def last(session):
        return (await session.scalars(select(AuditLog.action).where(
            AuditLog.action.in_(("lrs_summary_on", "lrs_summary_off"))).order_by(AuditLog.id.desc()))).first()
    assert db_exec(last) == "lrs_summary_off"
