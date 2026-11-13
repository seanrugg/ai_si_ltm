"""Swarm introductions (issue #334)."""
from __future__ import annotations

import types

from cli_agent.introductions import Introducer, intro_text
from cli_agent.member_main import make_intro_sender
from server.tests.test_datasets_read import _get
from server.tests.test_swarms import Home


def test_roster_has_names_roles_kinds_and_summaries_only(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    z3, cam, gone = mesh.member("z3-r0"), mesh.member("cam"), mesh.member("gone")
    home.add(z3)
    home.add(cam, role="observer")
    home.add(gone)
    z3.login()
    z3.http.put("/api/members/me/specs", headers={"Authorization": f"Bearer {z3.token}"},
                json={"hardware": {"board": "UNO Q", "ram_mib": 2048},
                      "models": [{"name": "qwen3:0.6b", "where": "onboard"}]})
    admin.post(f"/api/admin/members/{gone.member_id}/revoke", json={"reason": "test"})
    s = _get(cam, "/api/swarm").json()["swarm"]  # observers see the roster too
    assert s["name"] == "echo-base"
    assert s["roster"] == [{"name": "cam", "role": "observer", "kind": "robot", "summary": ""},
                           {"name": "z3-r0", "role": "member", "kind": "robot", "summary": "UNO Q 2 GB · qwen3:0.6b on board"}]
    assert _get(mesh.member("free"), "/api/swarm").json() == {"swarm": None, "leads": None}  # leads: #310


SWARM = {"id": "s1", "name": "echo-base", "role": "member",
         "roster": [{"name": "z3-r0", "role": "member", "kind": "robot", "summary": "UNO Q 2 GB"}]}


def _intro(tmp_path, policy="reply"):
    sent = []
    intro = Introducer("z3-r0", str(tmp_path / "introduced.json"),
                       lambda text, payload, reply_to: sent.append((text, payload, reply_to)) or {"seq": len(sent)},
                       reply_policy=policy, rand=lambda: 0.5)
    return intro, sent


def test_introduces_once_per_swarm_and_role(tmp_path):
    intro, sent = _intro(tmp_path)
    intro.check(SWARM)
    assert sent[0][0] == "Hello echo-base, I'm z3-r0, a robot joining as member (UNO Q 2 GB)."
    assert sent[0][1]["swarm"] == "s1" and sent[0][1]["specs_summary"] == "UNO Q 2 GB" and sent[0][2] is None
    again, sent2 = _intro(tmp_path)  # a restart: same state file
    again.check(SWARM)
    assert sent2 == []
    again.check({**SWARM, "role": "observer"})
    assert len(sent2) == 1
    assert intro.check(None) is None


def test_replies_once_after_a_delay_and_never_to_replies(tmp_path):
    intro, sent = _intro(tmp_path)
    intro.check(SWARM)
    sent.clear()
    hello = {"agent": "vector", "seq": 7, "tags": ["swarm-message", "introduction"]}
    assert intro.on_message(hello, now=100) is True
    assert intro.on_message(hello, now=101) is False  # once
    assert intro.on_message({**hello, "seq": 8, "refs": {"reply_to": "x:1"}}, now=101) is False
    assert intro.on_message({**hello, "agent": "z3-r0"}, now=101) is False  # its own
    assert intro.due(110) == [] and intro.due(115)  # random delay: 0.5 * 30 s
    assert sent[0][2] == "vector:7" and sent[0][0].startswith("Hi echo-base, z3-r0 here")
    off, sent_off = _intro(tmp_path, "off")
    off.check(SWARM)
    assert off.on_message({**hello, "seq": 9}, now=1) is False and off.due(100) == []


def test_bus_or_direct(tmp_path):
    calls = []
    link = types.SimpleNamespace(append=lambda t, **k: calls.append(("append", t, k)),
                                 send_message=lambda text, **k: calls.append(("say", text, k)))
    make_intro_sender(link, direct=True)("hi", {"a": 1}, "v:1")
    make_intro_sender(link, direct=False)("hi", {"a": 1}, None)
    assert calls[0] == ("append", "communication", {"payload": {"a": 1}, "summary": "hi", "tags": ["introduction"],
                                                    "refs": {"reply_to": "v:1"}})
    assert calls[1] == ("say", "hi", {"payload": {"a": 1}, "reply_to": None, "tags": ["introduction"]})
    assert len(intro_text("x", "s", {"kind": "agent", "summary": "y" * 600})) == 500
