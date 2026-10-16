"""Enrollment requests and the challenge/token login."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from cli_agent.client import LTMSClient, LTMSError
from ltms_contract import auth_signing_message, generate_key, public_key_hex
from server.models import AuthChallenge, Token


def test_login_issues_working_token(mesh):
    z3 = mesh.member("z3-r0")
    tok = z3.login()
    assert tok["permissions"] == ["contribute", "recall"]
    assert tok["last_ack_seq"] == 0 and tok["last_hash"] == "0" * 64
    assert z3.sync_head()["last_ack_seq"] == 0


def test_pending_member_cannot_log_in(mesh):
    c = mesh.member("waiting", approve=False)
    with pytest.raises(LTMSError) as exc:
        c.login()
    assert exc.value.status == 403


def test_unknown_member_and_wrong_key(mesh, http):
    mesh.member("z3-r0")
    imposter = LTMSClient(name="z3-r0", signing_key=generate_key(), http=http)
    with pytest.raises(LTMSError) as exc:
        imposter.login()
    assert exc.value.status == 401 and exc.value.code == "auth_failed"
    nobody = LTMSClient(name="nobody", signing_key=generate_key(), http=http)
    with pytest.raises(LTMSError) as exc:
        nobody.login()
    assert exc.value.status == 403


def test_nonce_is_single_use(mesh, http):
    z3 = mesh.member("z3-r0")
    nonce = http.post("/api/auth/challenge", json={"name": "z3-r0"}).json()["nonce"]
    sig = z3.key.sign(auth_signing_message(nonce)).signature.hex()
    body = {"name": "z3-r0", "nonce": nonce, "sig": sig}
    assert http.post("/api/auth/token", json=body).status_code == 200
    assert http.post("/api/auth/token", json=body).status_code == 401


def test_bad_signature_burns_the_nonce(mesh, http):
    z3 = mesh.member("z3-r0")
    nonce = http.post("/api/auth/challenge", json={"name": "z3-r0"}).json()["nonce"]
    assert http.post("/api/auth/token", json={"name": "z3-r0", "nonce": nonce, "sig": "00" * 64}).status_code == 401
    good = z3.key.sign(auth_signing_message(nonce)).signature.hex()
    assert http.post("/api/auth/token", json={"name": "z3-r0", "nonce": nonce, "sig": good}).status_code == 401


def test_missing_or_malformed_bearer(http):
    assert http.get("/api/records").status_code == 401
    assert http.get("/api/records", headers={"Authorization": "Token abc"}).status_code == 401
    assert http.get("/api/records", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_expired_and_revoked_tokens(mesh, http, app, db_exec):
    z3 = mesh.member("z3-r0")
    z3.login()
    hdr = {"Authorization": f"Bearer {z3.token}"}
    assert http.get("/api/records/head", headers=hdr).status_code == 200
    db_exec(update(Token).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert http.get("/api/records/head", headers=hdr).status_code == 401
    z3.login()
    hdr = {"Authorization": f"Bearer {z3.token}"}
    db_exec(update(Token).values(revoked_at=datetime.now(timezone.utc)))
    assert http.get("/api/records/head", headers=hdr).status_code == 401


def test_expired_challenge(mesh, http, db_exec):
    z3 = mesh.member("z3-r0")
    nonce = http.post("/api/auth/challenge", json={"name": "z3-r0"}).json()["nonce"]
    db_exec(update(AuthChallenge).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    sig = z3.key.sign(auth_signing_message(nonce)).signature.hex()
    assert http.post("/api/auth/token", json={"name": "z3-r0", "nonce": nonce, "sig": sig}).status_code == 401


@pytest.mark.parametrize("body,status,code", [
    ({"name": "Bad Name", "kind": "robot"}, 422, "bad_name"),
    ({"name": "ok", "kind": "toaster"}, 422, "bad_kind"),
    ({"name": "ok", "kind": "robot", "public_key": "abcd"}, 422, "bad_public_key"),
    ({"name": "ok", "kind": "robot", "permissions": ["admin"]}, 422, "bad_permissions"),
])
def test_enroll_validation(http, body, status, code):
    payload = {"public_key": public_key_hex(generate_key()), **body}
    r = http.post("/api/auth/enroll", json=payload)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_enroll_duplicates_and_pending_cap(http, app):
    key = public_key_hex(generate_key())
    assert http.post("/api/auth/enroll", json={"name": "a", "kind": "robot", "public_key": key}).status_code == 202
    assert http.post("/api/auth/enroll", json={"name": "a", "kind": "robot",
                                               "public_key": public_key_hex(generate_key())}).status_code == 409
    assert http.post("/api/auth/enroll", json={"name": "b", "kind": "robot", "public_key": key}).status_code == 409
    app.state.config.max_pending_enrollments = 1
    r = http.post("/api/auth/enroll", json={"name": "c", "kind": "robot", "public_key": public_key_hex(generate_key())})
    assert r.status_code == 429


def test_enroll_rate_limited_per_ip(http, app):
    app.state.limiters["enroll"].burst = 2
    codes = [http.post("/api/auth/enroll", json={"name": f"m{i}", "kind": "robot",
                                                  "public_key": public_key_hex(generate_key())}).status_code
             for i in range(4)]
    assert codes[:2] == [202, 202] and codes[-1] == 429
