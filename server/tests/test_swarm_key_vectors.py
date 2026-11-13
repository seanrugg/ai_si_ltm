"""Swarm key vectors shared with the dashboard (issue #122).

The dashboard generates root keys and signs hub appointments in the browser;
``dashboard/src/lib/swarmKey.test.js`` checks the same JSON. If Python or
JavaScript drifts, one of the two fails.
"""
from __future__ import annotations

import json
from pathlib import Path

from nacl.signing import SigningKey

from cli_agent.client import LTMSClient
from ltms_contract import parse_ts
from scripts import make_swarm_key_vectors as mk
from scripts import swarm_key
from server.services import swarms

VECTORS = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "lib" / "swarm-key-vectors.json"


def load():
    return json.loads(VECTORS.read_text())


def test_committed_vectors_match_python():
    assert VECTORS.read_text() == mk.render(), "run: python -m scripts.make_swarm_key_vectors"


def test_vectors_agree_with_the_cli_and_server_code():
    v = load()
    assert v["swarm_prefix"].encode() == swarms.SWARM_PREFIX == swarm_key.SWARM_PREFIX
    for k in v["keys"]:
        key = SigningKey(bytes.fromhex(k["seed_hex"]))
        assert bytes(key.verify_key).hex() == k["public_key"]
        assert swarm_key.swarm_id(k["public_key"]) == k["swarm_id"]
        assert swarms.swarm_id_for(bytes.fromhex(k["public_key"])) == k["swarm_id"]
    for a in v["appointments"]:
        root = SigningKey(bytes.fromhex(a["root_seed_hex"]))
        st = a["statement"]
        made = swarms.make_appointment(root, swarm_id=st["swarm"], hub=st["hub"], hub_key_hex=st["hub_key"],
                                       ts=st["ts"])
        assert made == {"statement": st, "sig": a["sig"]}
        assert swarms.appointment_message(st).hex() == a["message_hex"]
    for a in v["leader_appointments"]:  # #309
        root = SigningKey(bytes.fromhex(a["root_seed_hex"]))
        st = a["statement"]
        made = swarm_key.leader_appointment(root, st["swarm"], st["leader"], st["leader_key"], ts=st["ts"])
        assert made == {"statement": st, "sig": a["sig"]}


def test_server_accepts_an_appointment_signed_the_browser_way(http, admin, db_exec):
    """The vector appointment (what the dashboard POSTs) is accepted by the real endpoint."""
    v = load()
    a = v["appointments"][0]
    st = a["statement"]
    hub = LTMSClient(name=st["hub"], signing_key=SigningKey(bytes.fromhex(a["hub_seed_hex"])), http=http)
    res = hub.enroll("relay", ["contribute"])
    assert admin.post(f"/api/admin/members/{res['id']}/approve", json={}).status_code == 200

    r = admin.post("/api/admin/swarms", json={"name": "echo-base", "public_key": v["keys"][0]["public_key"]})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == st["swarm"]

    # The vector's ts is fixed, so check the signature path at that time, then
    # the HTTP path with the same algorithm and a fresh ts.
    async def at_signing_time(s):
        return await swarms.appoint_hub(s, await swarms.get_swarm(s, "echo-base"),
                                        {"statement": st, "sig": a["sig"]}, now=parse_ts(st["ts"]))
    assert db_exec(at_signing_time).role == "hub"

    root = SigningKey(bytes.fromhex(a["root_seed_hex"]))
    fresh = swarms.make_appointment(root, swarm_id=st["swarm"], hub=st["hub"], hub_key_hex=st["hub_key"])
    r = admin.post("/api/admin/swarms/echo-base/hub", json=fresh)
    assert r.status_code == 200, r.text
    assert r.json() == {"swarm": "echo-base", "hub": st["hub"]}
