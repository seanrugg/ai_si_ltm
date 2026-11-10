"""Memory wipe notification: the client learns when the server's memory_generation
moves ahead (issue #158). The server tells the device; what the device does with
that is adapter-specific, so the client only exposes a callback.
"""
from __future__ import annotations

import json

import pytest

from cli_agent.client import LTMSClient


def _wipe(admin, member):
    return admin.post(f"/api/admin/members/{member.member_id}/wipe",
                      json={"confirm": member.name})


def test_token_and_head_include_memory_generation(mesh, admin):
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall"])
    tok = z3.login()
    assert tok["memory_generation"] == 0
    head = z3.sync_head()
    assert head["memory_generation"] == 0


def test_wipe_bumps_memory_generation(mesh, admin):
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall"])
    assert _wipe(admin, z3).status_code == 200
    assert z3.login()["memory_generation"] == 1
    assert z3.sync_head()["memory_generation"] == 1
    assert _wipe(admin, z3).status_code == 200
    assert z3.login()["memory_generation"] == 2


def test_saved_generation_below_server_runs_callback_once(tmp_path, http, admin, mesh):
    state_file = tmp_path / "generation.json"
    state_file.write_text(json.dumps({"memory_generation": 1}))
    calls: list[int] = []
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall"])
    z3.state_path = str(state_file)
    z3.on_memory_wipe = lambda value: calls.append(value)

    # The server is at generation 2; the device last saw 1.
    assert _wipe(admin, z3).status_code == 200
    assert _wipe(admin, z3).status_code == 200

    z3.login()
    assert calls == [2]
    assert json.loads(state_file.read_text()) == {"memory_generation": 2}

    z3.login()
    assert calls == [2]  # not called again now that the device is caught up


def test_no_state_file_saves_without_callback(tmp_path, http, admin, mesh):
    state_file = tmp_path / "generation.json"  # does not exist yet
    calls: list[int] = []
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall"])
    z3.state_path = str(state_file)
    z3.on_memory_wipe = lambda value: calls.append(value)

    assert _wipe(admin, z3).status_code == 200  # server generation 1

    z3.login()
    assert calls == []  # not known yet: no callback
    assert json.loads(state_file.read_text()) == {"memory_generation": 1}


def test_callback_raise_does_not_save(tmp_path, http, admin, mesh):
    state_file = tmp_path / "generation.json"
    state_file.write_text(json.dumps({"memory_generation": 1}))
    calls: list[int] = []
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall"])
    z3.state_path = str(state_file)

    def boom(value: int) -> None:
        calls.append(value)
        raise RuntimeError("wipe handler failed")

    z3.on_memory_wipe = boom
    assert _wipe(admin, z3).status_code == 200
    assert _wipe(admin, z3).status_code == 200  # server generation 2

    with pytest.raises(RuntimeError):
        z3.login()
    assert calls == [2]
    assert json.loads(state_file.read_text()) == {"memory_generation": 1}  # unchanged

    # A crash re-runs the callback on the next login.
    with pytest.raises(RuntimeError):
        z3.login()
    assert calls == [2, 2]
    assert json.loads(state_file.read_text()) == {"memory_generation": 1}


def test_outbox_survives_wipe_detected_at_login(tmp_path, http, admin, mesh):
    state_file = tmp_path / "generation.json"
    state_file.write_text(json.dumps({"memory_generation": 0}))
    calls: list[int] = []
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall"])
    z3.state_path = str(state_file)
    z3.on_memory_wipe = lambda value: calls.append(value)

    z3.login()  # learns generation 0, saves it, no callback
    assert calls == []
    z3.append("event", summary="pending")
    assert len(z3.outbox) == 1

    assert _wipe(admin, z3).status_code == 200  # server generation 1

    z3.login()  # the wipe is detected at login
    assert calls == [1]
    assert len(z3.outbox) == 1  # the pending record survived

    z3.flush()  # and it gets delivered
    assert len(z3.outbox) == 0
