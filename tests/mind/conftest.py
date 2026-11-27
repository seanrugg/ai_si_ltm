"""A fake LTMS client and a scripted brain for the mind's tests (#339).

The fake signs real records with the contract (so chains are checked) and keeps
the "server" in memory. ``records(**filters)`` answers newest first, as the API does.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ltms_contract import GENESIS_HASH, build_record, generate_key
from ltms_mind.brains.base import Brain, Proposal
from ltms_mind.config import DreamCondition, MindConfig
from ltms_mind.gate import LexicalEmbedder
from ltms_mind.loop import Mind

NOW = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)


def ts(day: int, hour: int = 12) -> str:
    return f"2026-11-{day:02d}T{hour:02d}:00:00Z"


class FakeServer:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.heads: dict[str, tuple[int, str]] = {}
        self.charter_read = False
        self.charter_reads = 0
        self.mind = {}
        self.online = True
        self.swarm = None
        self.datasets = []
        self.items = {}
        self.reads = []
        self.directives = None   # None: an old server without the endpoint
        self.fleet_public = None

    def add(self, rec: dict) -> None:
        self.records.append(rec)

    def other(self, key, agent: str, type: str, *, at: str, **header) -> dict:
        """A record by another member (signed with its own key)."""
        seq, prev = self.heads.get(agent, (0, GENESIS_HASH))
        rec = build_record(key, agent=agent, seq=seq + 1, prev_hash=prev, type=type, ts=at, **header)
        self.heads[agent] = (rec["seq"], rec["hash"])
        self.add(rec)
        return rec


class FakeClient:
    def __init__(self, server: FakeServer, name: str = "me") -> None:
        self.server, self.name, self.key = server, name, generate_key()
        self.seq, self.last_hash = 0, GENESIS_HASH
        self.outbox: list[dict] = []
        self.auto_read_charter = True
        self.charter = None

    def login(self) -> dict:
        if not self.server.online:
            raise ConnectionError("offline")
        self.seq, self.last_hash = self.server.heads.get(self.name, (0, GENESIS_HASH))
        return {"token": "t", "permissions": ["contribute", "recall"], "last_ack_seq": self.seq,
                "last_hash": self.last_hash, "charter": {"read": self.server.charter_read},
                "mind": self.server.mind}

    def read_charter(self):
        self.server.charter_read = True
        self.server.charter_reads += 1
        return {"text": "charter"}

    def append(self, type: str, *, payload=None, ts=None, **header) -> dict:
        prev_seq = self.outbox[-1]["seq"] if self.outbox else self.seq
        prev_hash = self.outbox[-1]["hash"] if self.outbox else self.last_hash
        rec = build_record(self.key, agent=self.name, seq=prev_seq + 1, prev_hash=prev_hash, type=type,
                           payload=payload, ts=ts or "2026-11-24T03:00:00Z", **header)
        self.outbox.append(rec)
        return rec

    def flush(self, batch_size: int = 100):
        if not self.server.online:
            raise ConnectionError("offline")
        for r in self.outbox:
            assert r["seq"] == self.seq + 1 and r["prev_hash"] == self.last_hash, "chain broken"
            self.server.add(r)
            self.seq, self.last_hash = r["seq"], r["hash"]
        self.server.heads[self.name] = (self.seq, self.last_hash)
        self.outbox = []
        return [{"ok": True}]

    def swarm(self):
        return self.server.swarm

    def my_directives(self):
        if self.server.directives is None:
            raise RuntimeError("404 not found")
        return list(self.server.directives)

    def fleet_key(self):
        return self.server.fleet_public

    def records(self, *, limit=25, include_payload=True, from_=None, to=None, **f) -> list[dict]:
        out = []
        for r in self.server.records:
            if f.get("type") and r["type"] != f["type"]:
                continue
            if f.get("agent") and r["agent"] != f["agent"]:
                continue
            if f.get("peer") and f["peer"] not in (r.get("peers") or []):
                continue
            if f.get("tag") and f["tag"] not in (r.get("tags") or []):
                continue
            if f.get("outcome") and r.get("outcome") != f["outcome"]:
                continue
            if f.get("place") and r.get("place") != f["place"]:
                continue
            if from_ and r["ts"] < from_:
                continue
            if to and r["ts"] > to:
                continue
            out.append(r)
        return sorted(out, key=lambda r: r["ts"], reverse=True)[:limit]

    def datasets(self):
        return self.server.datasets

    def search_dataset(self, dataset_id, q, *, version=None, limit=20):
        return [{"key": k, "snippet": v[:40]} for k, v in self.server.items.get(dataset_id, {}).items()][:limit]

    def read_item(self, dataset_id, key, *, version=None, cycle=None):
        self.server.reads.append((dataset_id, key, cycle))
        return {"key": key, "text": self.server.items[dataset_id][key]}


class ScriptedBrain(Brain):
    """Returns whatever ``script(task)`` gives; records the tasks it saw."""

    name = "scripted"

    def __init__(self, script=None, tokens: int = 0) -> None:
        self.script = script or (lambda task: [])
        self.tokens = tokens
        self.seen = []

    def propose(self, task, context):
        self.seen.append((task, context))
        self.last_tokens = self.tokens
        out = self.script(task)
        return [Proposal(**p) if isinstance(p, dict) else p for p in out]


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def make_mind(tmp_path, server):
    def make(brain=None, client=None, health=None, now=NOW, **cfg) -> Mind:
        settings = dict(server="https://ltms.example.org", key_path=str(tmp_path / "me.key.json"),
                        state_path=str(tmp_path / "me.mind.json"), brain="scripted", model_family="qwen",
                        dream=cfg.pop("dream", None) or DreamCondition("always"))
        settings.update(cfg)
        c = client or make.client
        return Mind(MindConfig(**settings), c, brain or ScriptedBrain(), embedder=LexicalEmbedder(),
                    now=lambda: now, health=health)
    make.client = FakeClient(server)
    return make
