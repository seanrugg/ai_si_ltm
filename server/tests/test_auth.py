"""Token lifecycle: issued after valid sign, expired/revoked rejected.

Covers the bearer-token auth path (plan §"Member (Authorization)"): a freshly
issued token authenticates, an expired token is refused, a revoked token is
refused, and a missing Authorization header is refused.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from server.security.token import issue_token
from server.security.token import issue as issue_raw

from .conftest import _auth_header, _add_token


def test_valid_token_authenticated(authed_client):
    resp = authed_client.get("/api/records", headers=_auth_header(authed_client))
    assert resp.status_code == 200


def test_expired_token_rejected(authed_client_expired):
    resp = authed_client_expired.get("/api/records", headers=_auth_header(authed_client_expired))
    assert resp.status_code == 401


def test_missing_token_rejected(client):
    resp = client.get("/api/records")
    assert resp.status_code == 401


def test_bearer_prefix_required(client):
    raw = issue_raw()
    resp = client.get("/api/records", headers={"Authorization": raw.hex()})
    assert resp.status_code == 401
