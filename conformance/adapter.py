"""The interface a harness under test implements, and the reference adapter for cli_agent.

A third-party harness (another language, an MCU, ...) is tested through a small
Python adapter that drives it. Implement these methods:

    name                      -> the member name (str)
    public_key                -> the member's Ed25519 public key (bytes)
    append(type, summary, payload)  -> the signed record (dict), queued, not yet sent
    flush()                   -> send everything queued to LTMS; return nothing
    restart()                 -> stop and start the harness again (state reloaded from disk)
    server_head()             -> {"last_ack_seq", "last_hash"} as LTMS sees this member
    server_records()          -> this member's records as LTMS stores them, oldest first

``append`` must raise (any exception) for a type the harness refuses to send.
"""
from __future__ import annotations

from pathlib import Path

from cli_agent.client import LTMSClient
from cli_agent.member_link import MemberLink
from cli_agent.outbox import Outbox
from ltms_contract import load_key


class CliAgentAdapter:
    """Drives this repository's own harness: MemberLink + outbox + direct HTTPS."""

    def __init__(self, server: str | None, key: str, state_dir: str, http=None) -> None:
        self.server, self.key_path, self.state_dir, self.http = server, key, Path(state_dir), http
        self.name, self._key = load_key(key)
        self.public_key = bytes(self._key.verify_key)
        self._open()

    def _open(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.client = LTMSClient(self.server, key_path=self.key_path, http=self.http)
        self.outbox = Outbox(self.state_dir / "outbox.db")
        self.link = MemberLink(self.name, self._key, self.outbox, self.state_dir / "head.json",
                               lambda *a, **k: None, "conformance")

    def append(self, type: str, summary: str, payload: dict) -> dict:
        if type == "system":
            raise ValueError("system records are reserved for the server")
        return self.link.append(type, summary=summary, payload=payload)

    def flush(self) -> None:
        while True:
            recs = self.outbox.pending(self.name, 100)
            if not recs:
                return
            res = self.client._call("POST", "/api/records", json={"records": recs})
            head = res["heads"][self.name]
            if self.outbox.prune(self.name, head["last_ack_seq"]) == 0 or not res.get("ok", True):
                return

    def restart(self) -> None:
        self.outbox.close()
        self._open()

    def server_head(self) -> dict:
        return self.client.sync_head()

    def server_records(self) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self.client.records(agent=self.name, limit=200, offset=offset)
            out += page
            if len(page) < 200:
                return sorted(out, key=lambda r: r["seq"])
            offset += 200
