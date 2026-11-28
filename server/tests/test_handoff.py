"""The local handoff: harnesses on a device record as the device, without its key (#521)."""
from __future__ import annotations

import dataclasses
import os
import stat
import threading

import pytest

from cli_agent.agent import verify_chain
from cli_agent.handoff import Handoff
from ltms_contract import ContractError

from .test_member_main import _make_link, _node


def test_submit_checks_the_contract_up_front(tmp_path):
    h = Handoff(tmp_path / "handoff.db")
    with pytest.raises(ContractError):
        h.submit("tweet", summary="not a record type")
    with pytest.raises(ContractError):
        h.submit("event", summary="x" * 501)
    with pytest.raises(ContractError):
        h.submit("event", summary="ok", ts="yesterday")
    with pytest.raises(ContractError):
        h.submit("event", summary="ok", agent="someone-else")   # a harness can't choose who signs
    with pytest.raises(ContractError):
        h.submit("action", summary="ok", outcome="great")
    assert h.pending() == 0
    assert stat.S_IMODE(os.stat(tmp_path / "handoff.db").st_mode) == 0o600


def test_the_member_signs_handed_off_records_into_its_one_chain(tmp_path):
    key, link, _sent, outbox = _make_link(tmp_path, name="m4-c5")
    harness = Handoff(tmp_path / "handoff.db")          # e.g. Hermes's plugin
    harness.submit("event", summary="Hermes session started", payload={"model": "gemma4:31b"},
                   tags=["hermes", "session"], source="hermes", ts="2026-11-27T22:00:00Z")
    harness.submit("action", summary="ran pytest", outcome="success", peers=["v3-c7r"],
                   refs={"about": "v3-c7r:71829"}, source="hermes")
    link.append("event", summary="heartbeat")            # the member's own record, same chain

    member = Handoff(tmp_path / "handoff.db")            # the member process's own connection
    assert member.pump(link) == 2 and member.pending() == 0
    recs = outbox.pending("m4-c5", 10)
    assert [r["summary"] for r in recs] == ["heartbeat", "Hermes session started", "ran pytest"]
    assert all(r["agent"] == "m4-c5" for r in recs)
    assert recs[1]["ts"] == "2026-11-27T22:00:00Z" and recs[1]["payload"] == {"model": "gemma4:31b"}
    assert recs[2]["outcome"] == "success" and recs[2]["peers"] == ["v3-c7r"]
    ok, msg = verify_chain(recs, bytes(key.verify_key))
    assert ok, msg
    assert member.pump(link) == 0                        # nothing is signed twice


def test_a_record_the_member_cant_sign_is_moved_aside_not_blocking(tmp_path, caplog):
    _, link, _, outbox = _make_link(tmp_path, name="m4-c5")
    h = Handoff(tmp_path / "handoff.db")
    h.submit("event", summary="first")
    h.submit("event", summary="second")
    h.submit("event", summary="third")

    real = link.append

    def append(type, **kw):
        if kw.get("summary") == "second":
            raise ContractError("payload_too_large", "too big after all")
        return real(type, **kw)

    link.append = append
    assert h.pump(link) == 2
    assert [r["summary"] for r in outbox.pending("m4-c5", 10)] == ["first", "third"]
    refused = h.refused()
    assert len(refused) == 1 and refused[0]["error"].startswith("ContractError")
    assert h.pending() == 0 and "refused event" in caplog.text


def test_many_harness_threads_one_signer(tmp_path):
    key, link, _, outbox = _make_link(tmp_path, name="m4-c5")
    path = tmp_path / "handoff.db"
    Handoff(path)

    def harness(n):
        h = Handoff(path)
        for i in range(10):
            h.submit("event", summary=f"h{n}-{i}", source=f"h{n}")

    threads = [threading.Thread(target=harness, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    member = Handoff(path)
    while member.pump(link, limit=7):
        pass
    recs = outbox.pending("m4-c5", 100)
    assert len(recs) == 40 and len({r["summary"] for r in recs}) == 40
    ok, msg = verify_chain(recs, bytes(key.verify_key))
    assert ok, msg


def test_the_member_process_pumps_the_handoff_each_step(tmp_path):
    node, _key, _clock, mqtts = _node(tmp_path, name="m4-c5")
    node.cfg = dataclasses.replace(node.cfg, handoff_path=str(tmp_path / "handoff.db"))
    node.start()
    mqtts[0].connected()
    Handoff(tmp_path / "handoff.db").submit("event", summary="from a harness", source="hermes")
    node.step()
    assert [r["summary"] for r in node.outbox.pending("m4-c5", 10)] == ["from a harness"]
    Handoff(tmp_path / "handoff.db").submit("event", summary="just before stop")
    node.stop()                                          # pumped once more on the way out
    from cli_agent.outbox import Outbox
    assert [r["summary"] for r in Outbox(tmp_path / "out.db").pending("m4-c5", 10)] == [
        "from a harness", "just before stop"]


def test_config_reads_the_handoff_path():
    from cli_agent.member_main import load_config
    env = {"LTMS_KEY_PATH": "k.json", "SWARM_TOPIC_ID": "sw-1a2b3c4d"}
    assert load_config(env).handoff_path is None
    assert load_config({**env, "MEMBER_HANDOFF_PATH": " /var/lib/m/handoff.db "}).handoff_path == "/var/lib/m/handoff.db"


def test_mac_facts_come_from_sysctl(monkeypatch):
    """#521: macOS has no /proc; the Mac Studio's spec sheet still gets its RAM, model and GPU."""
    from cli_agent import hardware

    values = {"hw.memsize": str(192 * hardware.GIB), "hw.model": "Mac14,14", "machdep.cpu.brand_string": "Apple M2 Ultra"}
    monkeypatch.setattr(hardware.sys, "platform", "darwin")
    monkeypatch.setattr(hardware, "_sysctl", values.get)
    monkeypatch.setattr(hardware.platform, "mac_ver", lambda: ("15.5", ("", "", ""), "arm64"))
    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    sheet = hardware.spec_sheet(hardware.detect("."), "ltms-member")
    assert sheet["hardware"]["ram_mib"] == 192 * 1024 and sheet["hardware"]["board"] == "Mac14,14"
    assert sheet["hardware"]["cpu"] == "Apple M2 Ultra" and "Apple GPU (Metal)" in sheet["hardware"]["accelerators"]
    assert sheet["software"]["os"] == "macOS 15.5"


def test_introductions_report_the_installed_version():
    """#531: it looked up "ltms-harness"; the package is "ltms"."""
    from importlib.metadata import version

    from cli_agent.introductions import harness_version
    assert harness_version() == version("ltms")
