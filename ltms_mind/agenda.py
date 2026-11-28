"""The agenda: candidate work built from real signals, in a fixed order (#338).

1. **obligations**: directives I accepted and haven't reported on as done or
   couldn't (#341), then questions and messages addressed to me, and questions
   put to my whole swarm, that nobody from me has answered yet.
2. **verify**: lessons by members of a *different* model family that nobody has
   reviewed yet.
3. **predictions**: my predictions past ``check_after`` with no check yet.
4. **reflect**: my swarm's failed, stuck, stopped or abandoned outcomes that no
   lesson cites.
5. **study**: dataset items I haven't read, found with the study query.

Each category's weight (config) is the most tasks it may give one wake; the whole
agenda is capped at ``max_tasks``. Awake wakes only take obligations and
predictions with a numeric measure; the rest needs a dream (``loop.py``).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from ltms_mind.brains.base import Task

log = logging.getLogger("ltms_mind.agenda")

FAILURE_OUTCOMES = ("failed", "stuck", "reflex_stop", "abandoned")
SKIP_INBOX_TAGS = {"introduction", "answer", "directive-ack", "mind_cycle"}
COUNT_FILTERS = ("type", "tag", "agent", "place", "outcome")

# Model families, matched at the start of a model name (lowercase). The first match wins.
FAMILIES = ("claude", "gpt", "o1", "o3", "o4", "gemini", "gemma", "llama", "qwen", "mistral", "mixtral",
            "phi", "deepseek", "glm", "nemotron", "granite", "command", "olmo", "smollm", "hermes", "kimi")


def family_of(model: str | None) -> str | None:
    """``qwen3:4b`` → qwen, ``claude-opus-5-5`` → claude; None if unknown."""
    m = (model or "").strip().lower()
    m = m.rsplit("/", 1)[-1]  # org/model
    m = re.sub(r"^(genie|llamacpp|ollama|ai-hub-models):", "", m)
    for fam in FAMILIES:
        if m.startswith(fam):
            return "openai" if fam in ("gpt", "o1", "o3", "o4") else fam
    return None


def roster_families(swarm: dict | None) -> dict[str, str | None]:
    """Member → model family, from the roster's spec summary line (#334: "board · model where")."""
    out = {}
    for m in (swarm or {}).get("roster") or []:
        summary = m.get("summary") or ""
        model = summary.split(" · ")[-1].split(" ")[0] if summary else ""
        out[m.get("name")] = family_of(model)
    return out


def key_of(rec: dict) -> str:
    return f"{rec['agent']}:{rec['seq']}"


READ_TRIES = 3        # a read that hits the rate limit waits 1 s, then 2 s, then gives up
EVIDENCE_MAX = 5      # cited records a reviewer is shown, per lesson
EVIDENCE_CHARS = 1500  # of each one's payload


def ref_keys(rec: dict) -> set[str]:
    """Every ``agent:seq`` a record's refs point at."""
    out = set()
    for v in (rec.get("refs") or {}).values():
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and re.match(r"^[a-z0-9][a-z0-9._-]{0,63}:\d+$", x):
                out.add(x)
    return out


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def numeric_measure(rec: dict) -> dict | None:
    """``{"count": {type/tag/agent/place/outcome}, "op": ">=", "value": 3}``, checked or None."""
    m = (rec.get("payload") or {}).get("measure")
    if not isinstance(m, dict) or not isinstance(m.get("count"), dict):
        return None
    if m.get("op") not in ("<", "<=", ">", ">=", "==", "!="):
        return None
    try:
        float(m.get("value"))
    except (TypeError, ValueError):
        return None
    if not set(m["count"]) <= set(COUNT_FILTERS):
        return None
    return m


class Agenda:
    def __init__(self, client, *, me: str, swarm: dict | None, family: str | None, now: datetime,
                 since: datetime | None, lookback_days: int, corpus: int, study_query: str,
                 read_items: set[str], directives: list[dict] | None = None, sleep=time.sleep) -> None:
        self.client, self.me, self.swarm, self.family, self.now = client, me, swarm, family, now
        self.sleep = sleep
        self.lookback = now - timedelta(days=lookback_days)
        self.since = since or self.lookback
        self.corpus, self.study_query, self.read_items = corpus, study_query, read_items
        self.roster = {m.get("name") for m in (swarm or {}).get("roster") or []} - {None}
        self.families = roster_families(swarm)
        self.directives = directives or []

    def _read(self, fn, *args, **kw):
        """A read that waits out the server's rate limit (429), at most READ_TRIES times.
        A wake makes a burst of reads; dropping one silently loses a whole category."""
        for attempt in range(READ_TRIES):
            try:
                return fn(*args, **kw)
            except Exception as exc:  # noqa: BLE001 - re-raised below unless it's a 429 worth waiting out
                if getattr(exc, "status", None) != 429 or attempt == READ_TRIES - 1:
                    raise
                self.sleep(2 ** attempt)
        return None  # unreachable

    def _records(self, **params) -> list[dict]:
        params.setdefault("limit", self.corpus)
        try:
            return self._read(self.client.records, **params)
        except Exception as exc:  # noqa: BLE001 - one failed query drops that category, not the wake
            log.warning("records %s failed: %s", params, exc)
            return []

    def _evidence(self, keys) -> list[dict]:
        """The records a lesson cites, so a reviewer can check it against them (#521).
        At most EVIDENCE_MAX; a key that can't be read says so, so "unsure" is possible."""
        out = []
        for key in sorted(keys)[:EVIDENCE_MAX]:
            agent, _, seq = key.rpartition(":")
            try:
                rec = self._read(self.client.record, agent, int(seq))
            except Exception as exc:  # noqa: BLE001 - an unreadable citation is evidence too: it's missing
                log.warning("evidence %s unreadable: %s", key, exc)
                rec = None
            if rec is None:
                out.append({"key": key, "found": False})
                continue
            payload = json.dumps(rec.get("payload"), default=str, ensure_ascii=False)
            out.append({"key": key, "found": True, "type": rec.get("type"), "ts": rec.get("ts"),
                        "summary": rec.get("summary"), "outcome": rec.get("outcome"), "tags": rec.get("tags"),
                        "payload": payload if len(payload) <= EVIDENCE_CHARS else payload[:EVIDENCE_CHARS] + "…"})
        return out

    def _in_swarm(self, rec: dict) -> bool:
        return rec.get("agent") in self.roster

    # ------------------------------------------------------------------ categories
    def directive_tasks(self) -> list[Task]:
        """Accepted directives with no ``done`` or ``couldnt`` report of mine yet."""
        if not self.directives:
            return []
        closed, partial = set(), {}
        for r in self._records(agent=self.me, tag="directive-report"):
            pl = r.get("payload") or {}
            if pl.get("status") in ("done", "couldnt"):
                closed.add(pl.get("directive"))
            elif pl.get("directive"):
                partial.setdefault(pl["directive"], []).append(r.get("summary") or "")
        tasks = []
        for d in self.directives:
            if d["id"] in closed:
                continue
            stmt = d["statement"]
            issuer = stmt.get("issued_by")
            who = f"your swarm leader {issuer}" if d.get("signer") == "leader" else f"the fleet ({issuer})"
            tasks.append(Task("obligations", "directive", d["id"],
                              f"You accepted a directive from {who}: {stmt.get('text', '')}. Do what you can "
                              "toward it with what you know, then report: done, partial, or couldnt (with "
                              "the reason). Ask a swarm member for help with a question if that would get it done.",
                              {"directive": stmt, "ack": d["ack"], "signer": d.get("signer"),
                               "issuer": issuer if issuer in self.roster else None,
                               "earlier_reports": partial.get(d["id"], [])[:5]}))
        return tasks

    def obligations(self) -> list[Task]:
        tasks = self.directive_tasks()
        answered = set()
        for r in self._records(agent=self.me, tag="answer", from_=iso(self.lookback)):
            answered |= ref_keys(r)
        inbox = [r for r in self._records(peer=self.me, from_=iso(self.since)) if r.get("agent") != self.me]
        inbox += [r for r in self._records(tag="question", from_=iso(self.since))
                  if r.get("agent") != self.me and self._in_swarm(r) and not r.get("peers")]
        seen = set()
        for r in sorted(inbox, key=lambda r: r.get("ts") or ""):
            k = key_of(r)
            if k in seen or k in answered or SKIP_INBOX_TAGS & set(r.get("tags") or []):
                continue
            if r.get("type") != "communication":
                continue
            seen.add(k)
            ask = "question" in (r.get("tags") or [])
            tasks.append(Task("obligations", "answer", k,
                              f"{r['agent']} {'asked' if ask else 'wrote to'} you: {r.get('summary', '')}",
                              {"record": r, "asker": r["agent"]}))
        return tasks

    def verify(self) -> list[Task]:
        if not self.family:
            return []  # without my own family I can't tell which lessons are from another one
        reviewed = set()
        for r in self._records(tag="review", from_=iso(self.lookback)):
            reviewed |= {v for k, v in (r.get("refs") or {}).items() if k == "reviews" and isinstance(v, str)}
        tasks = []
        for r in self._records(type="lesson", from_=iso(self.lookback)):
            if r.get("agent") == self.me or key_of(r) in reviewed:
                continue
            fam = (r.get("payload") or {}).get("model_family") or self.families.get(r.get("agent"))
            if not fam or fam == self.family:
                continue
            tasks.append(Task("verify", "review", key_of(r),
                              f"Check this lesson by {r['agent']} ({fam}) against its cited evidence, then give a "
                              f"verdict (verified, refuted or unsure) with reasons: {r.get('summary', '')}",
                              {"record": r, "family": fam, "cited": sorted(ref_keys(r)),
                               "evidence": self._evidence(ref_keys(r)),
                               "sources": (r.get("payload") or {}).get("sources") or []}))
        return tasks

    def predictions(self, numeric_only: bool) -> list[Task]:
        checked = set()
        for r in self._records(agent=self.me, tag="prediction-check"):
            checked |= ref_keys(r)
        tasks = []
        for r in self._records(agent=self.me, tag="prediction"):
            due = parse_ts((r.get("payload") or {}).get("check_after"))
            if key_of(r) in checked or due is None or due > self.now:
                continue
            measure = numeric_measure(r)
            if numeric_only and measure is None:
                continue
            inputs = {"prediction": r}
            if measure is not None:
                rows = self._records(**measure["count"], from_=r.get("ts"), to=iso(due))
                inputs["observed"] = float(len(rows))
            tasks.append(Task("predictions", "check", key_of(r),
                              f"Your prediction is due for checking: {(r.get('payload') or {}).get('claim')}",
                              inputs))
        return tasks

    def reflect(self) -> list[Task]:
        cited = set()
        for r in self._records(type="lesson", from_=iso(self.lookback)):
            cited |= ref_keys(r)
        tasks = []
        for outcome in FAILURE_OUTCOMES:
            for r in self._records(outcome=outcome, from_=iso(self.lookback)):
                if (self._in_swarm(r) or r.get("agent") == self.me) and key_of(r) not in cited:
                    tasks.append(Task("reflect", "reflect", key_of(r),
                                      f"{r['agent']} recorded outcome '{outcome}': {r.get('summary', '')}. "
                                      "What can the swarm learn from it? Write a lesson that cites it, or a question.",
                                      {"record": r}))
        return sorted(tasks, key=lambda t: t.inputs["record"].get("ts") or "", reverse=True)

    def study(self) -> list[Task]:
        query = self.study_query or (self.swarm or {}).get("name") or ""
        try:
            sets = self.client.datasets()
        except Exception as exc:  # noqa: BLE001
            log.warning("datasets failed: %s", exc)
            return []
        tasks = []
        for ds in sets:
            ver = ds.get("current_version")
            if ver is None or ds.get("status") not in (None, "approved", "active"):
                continue
            try:
                hits = self.client.search_dataset(ds["id"], query, version=ver, limit=10) if query else []
            except Exception as exc:  # noqa: BLE001
                log.warning("search %s failed: %s", ds.get("name"), exc)
                continue
            for h in hits:
                item = f"{ds['id']}@{ver}/{h['key']}"
                if item in self.read_items:
                    continue
                tasks.append(Task("study", "study", item,
                                  f"Study '{h['key']}' from the dataset '{ds.get('name')}'. Write a lesson only if "
                                  "the material supports one, citing it as a source.",
                                  {"dataset": ds["id"], "version": ver, "item": h["key"],
                                   "dataset_name": ds.get("name"), "snippet": h.get("snippet")}))
        return tasks

    # ------------------------------------------------------------------ the whole agenda
    def build(self, *, weights: dict, max_tasks: int, dreaming: bool) -> list[Task]:
        """Categories in their fixed order, each up to its weight, all up to ``max_tasks``."""
        plan = [("obligations", self.obligations)]
        if dreaming:
            plan += [("verify", self.verify), ("predictions", lambda: self.predictions(False)),
                     ("reflect", self.reflect), ("study", self.study)]
        else:
            plan += [("predictions", lambda: self.predictions(True))]
        out: list[Task] = []
        for name, fn in plan:
            room = min(weights.get(name, 0), max_tasks - len(out))
            if room <= 0:
                continue
            out += fn()[:room]
        return out
