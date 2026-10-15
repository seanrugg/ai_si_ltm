"""Recall: filtered query and context-pack composition.

Covers the member recall path (plan §"Member (Authorization)") through the API:
a type-filtered recall returns only matching records; a filtered recall that
matches nothing returns empty; and a context pack returns bounded results.
"""
from __future__ import annotations

from .conftest import _auth_header, genesis_prev_hash_hex, make_secret, signed_record


def _ingest(db_session, member, secret, seq, type, body, prev_hex):
    rec = signed_record(secret, seq=seq, body=body, prev_hash_hex=prev_hex, type=type)
    return rec


def test_recall_by_type(authed_client, db_session, active_member):
    secret = make_secret()
    genesis = _ingest(db_session, active_member, secret, 1, "communication", {"text": "a"}, genesis_prev_hash_hex())
    action = _ingest(
        db_session, active_member, secret, 2, "action", {"text": "b"}, genesis["prev_hash"]
    )
    from server.services.ingest import ingest_batch

    import asyncio

    asyncio.run(ingest_batch(db_session, active_member, [genesis, action]))

    resp = authed_client.get(
        "/api/records", params={"type": "action"}, headers=_auth_header(authed_client)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["type"] == "action"


def test_recall_filter_no_match(authed_client, db_session, active_member):
    secret = make_secret()
    genesis = _ingest(
        db_session, active_member, secret, 1, "communication", {"text": "a"}, genesis_prev_hash_hex()
    )
    from server.services.ingest import ingest_batch

    import asyncio

    asyncio.run(ingest_batch(db_session, active_member, [genesis]))

    resp = authed_client.get(
        "/api/records", params={"type": "decision"}, headers=_auth_header(authed_client)
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_recall_context(authed_client, db_session, active_member):
    secret = make_secret()
    genesis = _ingest(
        db_session, active_member, secret, 1, "communication", {"text": "a"}, genesis_prev_hash_hex()
    )
    action = _ingest(
        db_session, active_member, secret, 2, "action", {"text": "b", "place": "living-room"}, genesis["prev_hash"]
    )
    from server.services.ingest import ingest_batch

    import asyncio

    asyncio.run(ingest_batch(db_session, active_member, [genesis, action]))

    resp = authed_client.get(
        "/api/recall/context",
        params={"target": "action", "place": "living-room"},
        headers=_auth_header(authed_client),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "outcomes" in data
    assert data["total"] >= 1
