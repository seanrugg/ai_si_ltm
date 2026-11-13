"""The hub publishes its downlink on the swarm bus (issue #305)."""
from __future__ import annotations

import json
import types
from datetime import datetime, timezone

import pytest

from cli_agent.hub_downlink import Downlink
from cli_agent.member_main import drain_messages
from cli_agent.mqtt_messages import encode_record

D = {"id": "d1", "status": "open", "statement": "{}", "signature": "ab", "signer": "fleet", "issued_by": "admin:a",
     "expires_at": "2026-11-20T00:00:00Z"}
REC = {"agent": "claude", "seq": 3, "type": "communication", "summary": "hello swarm", "hash": "h3", "sig": "s",
       "peers": ["swarm:sw-1234abcd"]}


class FakeClient:
    def __init__(self, pages):
        self.pages, self.seen = list(pages), []

    def downlink(self, cursor=None):
        self.seen.append(cursor)
        if not self.pages:
            return {"items": [], "cursor": cursor}
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def _hub():
    published = []
    return types.SimpleNamespace(prefixes=["sw-1234abcd"], skip=set(),
                                 publish=lambda t, p, retain=False: published.append((t, p, retain))), published


def test_directives_retained_cancels_clear_messages_on_say(tmp_path):
    hub, pub = _hub()
    client = FakeClient([
        {"items": [{"kind": "directive", "directive": D}, {"kind": "message", "record": REC}], "cursor": "c1"},
        {"items": [{"kind": "cancel", "id": "d1", "statement": "{}", "signature": "cd"}], "cursor": "c2"},
    ])
    dl = Downlink(client, hub, str(tmp_path / "dl.json"))
    assert dl.poll(datetime(2026, 11, 13, tzinfo=timezone.utc)) == 2
    assert pub[0][0] == "sw-1234abcd/fleet/d1" and pub[0][2] is True and json.loads(pub[0][1])["signature"] == "ab"
    assert pub[1] == ("sw-1234abcd/claude/say", encode_record(REC), False) and "h3" in hub.skip
    assert json.loads((tmp_path / "dl.json").read_text()) == {"cursor": "c1", "open": {"d1": D["expires_at"]}}

    pub.clear()
    dl.poll(datetime(2026, 11, 13, tzinfo=timezone.utc))
    assert pub[0] == ("sw-1234abcd/fleet/d1", b"", True)  # retained directive cleared
    assert pub[1][0] == "sw-1234abcd/fleet" and pub[1][2] is False
    assert client.seen[-1] == "c1"
    assert Downlink(client, hub, str(tmp_path / "dl.json")).state["cursor"] == "c2"  # survives a restart


def test_a_failed_pull_keeps_the_cursor_and_expired_ones_are_cleared(tmp_path):
    hub, pub = _hub()
    dl = Downlink(FakeClient([{"items": [{"kind": "directive", "directive": D}], "cursor": "c1"},
                              ConnectionError("down")]), hub, str(tmp_path / "dl.json"))
    dl.poll(datetime(2026, 11, 13, tzinfo=timezone.utc))
    with pytest.raises(ConnectionError):
        dl.poll()
    assert dl.state["cursor"] == "c1"
    pub.clear()
    dl.client.pages = [{"items": [], "cursor": "c1"}]
    dl.poll(datetime(2026, 11, 21, tzinfo=timezone.utc))
    assert pub == [("sw-1234abcd/fleet/d1", b"", True)] and dl.state["open"] == {}


def test_members_accept_messages_addressed_to_the_swarm():
    import queue
    q = queue.Queue()
    q.put(("sw-1234abcd/claude/say", encode_record(REC)))
    got = []
    assert drain_messages(q, "z3-r0", "sw-1234abcd", got.append) == 1
    assert got[0]["summary"] == "hello swarm"
