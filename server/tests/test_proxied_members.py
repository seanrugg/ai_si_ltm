"""Proxied members (#311): the member says who holds its key; admins can change it."""
from __future__ import annotations

import pytest

from cli_agent.client import LTMSClient, LTMSError
from ltms_contract import generate_key


def _member(admin, name):
    return next(m for m in admin.get("/api/admin/members").json()["members"] if m["name"] == name)


def test_enroll_proxied_is_shown_and_editable(http, admin):
    vector = LTMSClient(name="vector", signing_key=generate_key(), http=http)
    vector.enroll("robot", ["contribute"], proxied_by="sidecar on hub-host")
    m = _member(admin, "vector")
    assert m["proxied_by"] == "sidecar on hub-host" and m["kind"] == "robot"

    r = admin.patch(f"/api/admin/members/{m['id']}/proxied", json={"proxied_by": "sidecar on new-host"})
    assert r.status_code == 200 and r.json()["proxied_by"] == "sidecar on new-host"
    r = admin.patch(f"/api/admin/members/{m['id']}/proxied", json={"proxied_by": None})
    assert r.status_code == 200 and r.json()["proxied_by"] is None
    assert admin.patch(f"/api/admin/members/{m['id']}/proxied", json={}).status_code == 422
    assert admin.patch(f"/api/admin/members/{m['id']}/proxied", json={"proxied_by": "x" * 129}).status_code == 422


def test_ordinary_members_are_not_proxied(mesh, admin):
    mesh.member("z3-r0")
    assert _member(admin, "z3-r0")["proxied_by"] is None


def test_too_long_proxied_by_is_refused_at_enrollment(http):
    c = LTMSClient(name="long-one", signing_key=generate_key(), http=http)
    with pytest.raises(LTMSError):
        c.enroll("robot", ["contribute"], proxied_by="y" * 200)
