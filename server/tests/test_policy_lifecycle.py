"""Policy lifecycle: proposals, in-force, retired.

Covers the context pack selection and the ``/api/admin/policies`` endpoint.
"""
import pytest

from server.tests.conftest import ADMIN_USER, ADMIN_PASS


def _policies(admin, status=None):
    params = {"status": status} if status else None
    return admin.get("/api/admin/policies", params=params).json()["policies"]


def _by_summary(policies):
    return {p["summary"]: p for p in policies}


def test_proposal_is_listed_separately(mesh, admin):
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall", "policy"])
    z3.append("policy", summary="Charter v1", tags=["proposal"])
    z3.append("policy", summary="Quiet hours")
    z3.flush()

    pack = z3.context()
    assert [r["summary"] for r in pack["proposals"]] == ["Charter v1"]
    assert [r["summary"] for r in pack["policies"]] == ["Quiet hours"]
    assert all(r["status"] == "proposed" for r in pack["proposals"])
    assert pack["count"] == 2


def test_supersedes_retires_old_policy(mesh, admin):
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall", "policy"])
    z3.append("policy", summary="Charter v1")
    old_seq = z3.outbox[-1]["seq"]
    z3.append("policy", summary="Charter v2", refs={"supersedes": f"z3-r0:{old_seq}"})
    z3.flush()

    pack = z3.context()
    assert [r["summary"] for r in pack["policies"]] == ["Charter v2"]

    by = _by_summary(_policies(admin))
    assert by["Charter v1"]["status"] == "retired"
    assert by["Charter v1"]["retired_by"] == f"z3-r0:2"
    assert by["Charter v2"]["status"] == "in_force"


def test_revokes_removes_without_replacement(mesh, admin):
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall", "policy"])
    z3.append("policy", summary="Rule")
    old_seq = z3.outbox[-1]["seq"]
    z3.append("policy", summary="Revocation", refs={"revokes": f"z3-r0:{old_seq}"})
    z3.flush()

    pack = z3.context()
    assert [r["summary"] for r in pack["policies"]] == ["Revocation"]

    by = _by_summary(_policies(admin))
    assert by["Rule"]["status"] == "retired"
    assert by["Revocation"]["status"] == "in_force"


def test_proposal_refs_do_not_retire(mesh, admin):
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall", "policy"])
    z3.append("policy", summary="Rule")
    old_seq = z3.outbox[-1]["seq"]
    z3.append("policy", summary="Proposed repeal", tags=["proposal"],
              refs={"supersedes": f"z3-r0:{old_seq}"})
    z3.flush()

    pack = z3.context()
    assert [r["summary"] for r in pack["policies"]] == ["Rule"]
    assert [r["summary"] for r in pack["proposals"]] == ["Proposed repeal"]

    by = _by_summary(_policies(admin))
    assert by["Rule"]["status"] == "in_force"
    assert by["Proposed repeal"]["status"] == "proposed"


def test_admin_policies_status_filter(mesh, admin):
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall", "policy"])
    z3.append("policy", summary="In force")
    z3.append("policy", summary="Proposal", tags=["proposal"])
    z3.flush()

    all_p = _policies(admin)
    assert {p["summary"] for p in all_p} == {"In force", "Proposal"}

    proposed = _policies(admin, status="proposed")
    assert [p["summary"] for p in proposed] == ["Proposal"]

    in_force = _policies(admin, status="in_force")
    assert [p["summary"] for p in in_force] == ["In force"]

    retired = _policies(admin, status="retired")
    assert retired == []
