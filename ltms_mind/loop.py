"""One wake of the member mind (#338, #339).

    duties → (paused? stop) → mode → agenda → brain → gate → write → cycle event

- **Duties** (every wake, cheap): sign in, send anything left in the outbox, read
  the charter if it's unread, introduce myself once per swarm, report health
  when it changes.
- **Mode**: *dreaming* when the brain can dream and the dream condition holds;
  otherwise *awake*. Awake wakes only answer what's addressed to me and check
  numeric predictions. Dreaming wakes open a ``DreamCycle`` (#240) and also
  verify, check other predictions, reflect and study.
- **Silence is the default.** With nothing on the agenda, or nothing past the
  gate, the only record is the cycle event (an awake ``mind_cycle`` event, or
  the dream cycle's start and end events, which carry the same counts).

One writer per key: the mind signs with the member's key over HTTPS. Don't run
it beside another process that signs with the same key (see docs/mind.md).
"""
from __future__ import annotations

import json
import logging
import os
import random
import shutil
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable

from cli_agent.client import DreamCycle
from cli_agent.introductions import INTRO_TAG, Introducer

from ltms_mind import gate as gate_mod
from ltms_mind.agenda import Agenda, iso, parse_ts
from ltms_mind.brains.base import RECORD, Brain, Context, Proposal, Task
from ltms_mind.budget import Budget
from ltms_mind.config import MindConfig

log = logging.getLogger("ltms_mind")

CYCLE_TAG = "mind_cycle"
MIND_TAG = "mind"
# The ref a task's own subject must carry, by task kind: the brain can't point elsewhere.
TASK_REF = {"answer": "answers", "review": "reviews", "check": "checks"}


# ---------------------------------------------------------------------------- state
class State:
    """A small JSON file beside the key: last cycle, cursors, daily counts."""

    def __init__(self, path: str) -> None:
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, k, default=None):
        return self.data.get(k, default)

    def __setitem__(self, k, v) -> None:
        self.data[k] = v

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)


def save_outbox(path: str, outbox: list[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(outbox, f)
    os.replace(tmp, path)


def load_outbox(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            out = json.load(f)
        return out if isinstance(out, list) else []
    except (OSError, ValueError):
        return []


def continue_chain(saved: list[dict], seq: int, last_hash: str) -> list[dict]:
    """The saved records that still extend the server's head, in order. A record the
    server already has is dropped; a gap or a fork drops the rest (and is logged)."""
    out, want_seq, want_prev = [], seq + 1, last_hash
    for r in sorted(saved, key=lambda r: r.get("seq", 0)):
        if r.get("seq", 0) <= seq:
            continue
        if r.get("seq") != want_seq or r.get("prev_hash") != want_prev:
            log.warning("saved outbox doesn't continue the chain at seq %s; %d record(s) dropped",
                        r.get("seq"), len(saved) - len(out))
            break
        out.append(r)
        want_seq, want_prev = r["seq"] + 1, r["hash"]
    return out


# ---------------------------------------------------------------------------- health
def _level(value: float, warn: float, bad: float, higher_is_worse: bool = True) -> str:
    if higher_is_worse:
        return "bad" if value >= bad else "warn" if value >= warn else "ok"
    return "bad" if value <= bad else "warn" if value <= warn else "ok"


def host_health(path: str = "/") -> dict:
    """Coarse levels (ok, warn, bad), so a report means a real change, not noise."""
    out = {}
    try:
        du = shutil.disk_usage(path)
        out["disk"] = _level(du.free / du.total * 100, 20, 10, higher_is_worse=False)
    except OSError:
        pass
    try:
        out["load"] = _level(os.getloadavg()[0] / (os.cpu_count() or 1), 1.0, 2.0)
    except (OSError, AttributeError):
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            mem = {l.split(":")[0]: int(l.split()[1]) for l in f if l.split(":")[0] in ("MemTotal", "MemAvailable")}
        out["memory"] = _level(mem["MemAvailable"] / mem["MemTotal"] * 100, 20, 10, higher_is_worse=False)
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        pass
    return out


# ---------------------------------------------------------------------------- dream cycle
class MindDream(DreamCycle):
    """A dream cycle whose end event also carries the mind's counts, so a dreaming
    wake writes one start/end pair and no separate ``mind_cycle`` event. It doesn't
    flush on exit: the loop flushes and saves the outbox."""

    def __init__(self, client, counts: dict) -> None:
        super().__init__(client, jitter_s=0)
        self.counts = counts

    def __exit__(self, exc_type, exc, tb) -> bool:
        payload = {"cycle": self.id, "phase": "end", "items_read": self.items_read, "lessons": self.lessons,
                   "mind": self.counts}
        if exc_type is not None:
            payload["error"] = exc_type.__name__
        self.client.append("event", summary="Dream cycle ended", tags=[self.TAG, CYCLE_TAG], payload=payload)
        return False


# ---------------------------------------------------------------------------- the mind
class Mind:
    def __init__(self, cfg: MindConfig, client, brain: Brain, *, embedder=None,
                 now: Callable[[], datetime] | None = None, health: Callable[[], dict] | None = host_health,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.cfg, self.client, self.brain = cfg, client, brain
        self.embedder = embedder or gate_mod.make_embedder(cfg.embedder)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.health = health if cfg.health else None
        self.monotonic = monotonic
        self.state = State(cfg.state_path)

    # ------------------------------------------------------------------ duties
    def _duties(self, tok: dict, counts: Counter) -> dict | None:
        status = tok.get("charter")
        if isinstance(status, dict) and status.get("read") is False:
            if self.client.read_charter() is not None:
                counts["charter_read"] += 1
        swarm = self.client.swarm()
        if swarm:
            intro = Introducer(self.client.name, self.cfg.intro_path, self._send_intro, reply_policy="off")
            if intro.check(swarm) is not None:
                counts["introduced"] += 1
        if self.health is not None:
            levels = self.health()
            before = self.state.get("health")
            if levels and levels != before:
                changed = sorted(k for k in levels if (before or {}).get(k) != levels[k])
                text = ", ".join(f"{k} {levels[k]}" for k in changed) or "reported"
                self._append("perception", summary=f"Health: {text}"[:500], tags=["health", "internal"],
                             payload={"levels": levels, "previous": before})
                self.state["health"] = levels
                counts["health_reported"] += 1
        return swarm

    def _send_intro(self, text: str, payload: dict, reply_to: str | None) -> dict:
        header = {"summary": text, "tags": [INTRO_TAG]}
        if reply_to:
            header["refs"] = {"reply_to": reply_to}
        return self._append("communication", payload=payload, **header)

    def _append(self, type: str, **kw) -> dict:
        if self.cfg.place and "place" not in kw:
            kw["place"] = self.cfg.place
        return self.client.append(type, **kw)

    # ------------------------------------------------------------------ dreaming?
    def _dream_due(self, now: datetime) -> bool:
        cond = self.cfg.dream
        if not self.brain.can_dream or cond.mode == "never":
            return False
        if cond.mode == "always":
            return True
        if cond.mode == "window":
            local = now.astimezone()
            if cond.in_window(local.hour * 60 + local.minute):
                return True
            day = local.date().isoformat()
            done = self.state.get("day_dreams") or {}
            return done.get(day, 0) < self.cfg.day_dreams
        # idle: charging, or no decision/action of mine for idle_min minutes
        try:
            mine = self.client.records(agent=self.client.name, limit=50)
        except Exception as exc:  # noqa: BLE001
            log.warning("idle check failed: %s", exc)
            return False
        for r in mine:  # newest first
            if r.get("type") == "perception" and "telemetry" in (r.get("tags") or []):
                if (r.get("payload") or {}).get("charging") is True:
                    return True
                break
        busy = [parse_ts(r.get("ts")) for r in mine if r.get("type") in ("decision", "action")]
        busy = [t for t in busy if t is not None]
        return not busy or now - max(busy) >= timedelta(minutes=cond.idle_min)

    def _note_dream(self, now: datetime) -> None:
        cond = self.cfg.dream
        local = now.astimezone()
        if cond.mode == "window" and not cond.in_window(local.hour * 60 + local.minute):
            day = local.date().isoformat()
            done = self.state.get("day_dreams") or {}
            self.state["day_dreams"] = {day: done.get(day, 0) + 1}

    # ------------------------------------------------------------------ proposals → records
    def _write(self, task: Task, p: Proposal, cycle: MindDream | None) -> dict:
        rtype, kind_tags = RECORD[p.kind]
        tags = list(dict.fromkeys([MIND_TAG, *kind_tags, *p.tags]))[:16]
        payload = dict(p.payload or {})
        if p.sources:
            payload["sources"] = p.sources
        if self.cfg.model_family:
            payload.setdefault("model_family", self.cfg.model_family)
        payload["task"] = {"category": task.category, "key": task.key}
        header = {"summary": p.summary, "tags": tags}
        if p.refs:
            header["refs"] = p.refs
        if p.peers:
            header["peers"] = p.peers[:16]
        if cycle is not None:
            payload["cycle"] = cycle.id
            if rtype == "lesson":
                cycle.lessons += 1
        return self._append(rtype, payload=payload, **header)

    def _bind(self, task: Task, p: Proposal) -> Proposal:
        """Tie a proposal to its task: the task's subject goes in the ref its kind needs,
        and an answer goes to whoever asked."""
        ref = TASK_REF.get(task.kind)
        expected = {"answer": "answer", "review": "review", "check": "prediction-check"}.get(task.kind)
        if ref and p.kind == expected:
            p.refs = {**(p.refs or {}), ref: task.key}
        if p.kind == "answer" and task.inputs.get("asker"):
            p.peers = [task.inputs["asker"]]
        return p

    def _run_tasks(self, tasks: list[Task], ctx: Context, gate: gate_mod.Gate, budget: Budget,
                   counts: dict, cycle: MindDream | None) -> None:
        gated = counts.setdefault("gated", {})
        for i, task in enumerate(tasks):
            why = budget.can_call()
            if why:
                counts["stopped"] = f"budget_{why}"
                counts["skipped"] = len(tasks) - i
                return
            if task.kind == "study" and cycle is not None:
                try:
                    task.inputs["content"] = cycle.read(task.inputs["dataset"], task.inputs["item"],
                                                        version=task.inputs["version"])
                except Exception as exc:  # noqa: BLE001 - an unreadable item is skipped, not retried forever
                    log.warning("study read %s failed: %s", task.key, exc)
                read = set(self.state.get("read_items") or [])
                read.add(task.key)
                self.state["read_items"] = sorted(read)[-2000:]
            try:
                proposals = self.brain.propose(task, ctx) or []
            except Exception as exc:  # noqa: BLE001 - a brain failure drops this task only
                log.warning("brain failed on %s: %s", task.key, exc)
                budget.charge(getattr(self.brain, "last_tokens", 0))
                counts["brain_errors"] = counts.get("brain_errors", 0) + 1
                continue
            budget.charge(getattr(self.brain, "last_tokens", 0))
            for p in proposals:
                counts["considered"] = counts.get("considered", 0) + 1
                if not isinstance(p, Proposal):
                    gated[gate_mod.BAD_PROPOSAL] = gated.get(gate_mod.BAD_PROPOSAL, 0) + 1
                    continue
                reason = gate.check(self._bind(task, p))
                if reason is None and not budget.can_write():
                    reason = gate_mod.OVER_BUDGET
                if reason:
                    gated[reason] = gated.get(reason, 0) + 1
                    log.info("gated %s (%s): %s", p.kind, reason, p.rationale[:200])
                    continue
                self._write(task, p, cycle)
                budget.wrote()
                counts["written"] = counts.get("written", 0) + 1

    def _corpus(self, swarm: dict | None) -> list[str]:
        roster = {m.get("name") for m in (swarm or {}).get("roster") or []}
        texts = []
        for params in ({"agent": self.client.name, "limit": self.cfg.corpus}, {"limit": self.cfg.corpus}):
            try:
                rows = self.client.records(include_payload=False, **params)
            except Exception as exc:  # noqa: BLE001
                log.warning("novelty corpus query failed: %s", exc)
                continue
            texts += [r.get("summary") or "" for r in rows
                      if "agent" in params or r.get("agent") in roster]
        return texts

    # ------------------------------------------------------------------ one wake
    def wake(self) -> dict:
        now = self.now()
        counts: dict = {"mode": "awake", "brain": self.brain.name}
        duty = Counter()
        self.client.auto_read_charter = False  # the duty reads it, and counts it
        try:
            tok = self.client.login()
        except Exception as exc:  # noqa: BLE001 - offline: the outbox waits for the next wake
            log.warning("sign-in failed, nothing done this wake: %s", exc)
            return {"offline": True, "error": str(exc)[:200]}
        self.client.outbox = continue_chain(load_outbox(self.cfg.outbox_path), self.client.seq, self.client.last_hash)
        duty["outbox_restored"] = len(self.client.outbox)
        self._flush(duty)
        self.client.auto_read_charter = True
        swarm = self._duties(tok, duty)
        counts["duties"] = {k: v for k, v in duty.items() if v}

        paused = self.cfg.paused or bool((tok.get("mind") or {}).get("paused"))
        if paused:
            counts["paused"] = True
            self._cycle_event(counts)
            return self._finish(now, counts)

        dreaming = self._dream_due(now)
        written_today = (self.state.get("writes") or {}).get(now.astimezone().date().isoformat(), 0)
        budget = Budget(calls=self.cfg.brain_calls, tokens=self.cfg.tokens, seconds=self.cfg.seconds,
                        daily_writes=self.cfg.daily_writes, written_today=written_today, clock=self.monotonic)
        agenda = Agenda(self.client, me=self.client.name, swarm=swarm, family=self.cfg.model_family, now=now,
                        since=parse_ts(self.state.get("last_cycle")), lookback_days=self.cfg.lookback_days,
                        corpus=self.cfg.corpus, study_query=self.cfg.study_query,
                        read_items=set(self.state.get("read_items") or []))
        tasks = agenda.build(weights=self.cfg.weights, max_tasks=self.cfg.max_tasks, dreaming=dreaming)
        counts["tasks"] = len(tasks)
        ctx = Context(member=self.client.name, swarm=(swarm or {}).get("name"), family=self.cfg.model_family,
                      now=iso(now), mode="dreaming" if dreaming else "awake")
        gate = gate_mod.Gate(self.embedder, self.cfg.novelty, self._corpus(swarm) if tasks else [])
        if dreaming:
            counts["mode"] = "dreaming"
            with MindDream(self.client, counts) as cycle:
                self._run_tasks(tasks, ctx, gate, budget, counts, cycle)
                counts["budget"] = budget.used()
            self._note_dream(now)
        else:
            self._run_tasks(tasks, ctx, gate, budget, counts, None)
            counts["budget"] = budget.used()
            self._cycle_event(counts)
        self.state["writes"] = {now.astimezone().date().isoformat(): budget.written_today}
        return self._finish(now, counts)

    def _cycle_event(self, counts: dict) -> None:
        mode = "paused" if counts.get("paused") else counts["mode"]
        text = f"Mind cycle ({mode}): {counts.get('tasks', 0)} tasks, {counts.get('written', 0)} written"
        self._append("event", summary=text, tags=[CYCLE_TAG], payload=counts)

    def _flush(self, duty: Counter | None = None) -> None:
        if not self.client.outbox:
            return
        n = len(self.client.outbox)
        try:
            self.client.flush()
            if duty is not None:
                duty["outbox_flushed"] += n
        except Exception as exc:  # noqa: BLE001 - kept for the next wake
            log.warning("flush failed; %d record(s) kept for the next wake: %s", len(self.client.outbox), exc)

    def _finish(self, now: datetime, counts: dict) -> dict:
        self._flush()
        save_outbox(self.cfg.outbox_path, self.client.outbox)
        counts["unsent"] = len(self.client.outbox)
        self.state["last_cycle"] = iso(now)
        self.state.save()
        log.info("wake done: %s", json.dumps(counts, default=str))
        return counts


def run_forever(mind_factory: Callable[[], Mind], every: float, jitter: float,
                sleep: Callable[[float], None] = time.sleep, wakes: int | None = None) -> None:
    """Wake now, then every ``every`` seconds plus a random 0..``jitter``."""
    n = 0
    while wakes is None or n < wakes:
        try:
            mind_factory().wake()
        except Exception:  # noqa: BLE001 - one bad wake must not end the loop
            log.exception("wake failed")
        n += 1
        if wakes is not None and n >= wakes:
            break
        sleep(every + random.uniform(0, jitter))
