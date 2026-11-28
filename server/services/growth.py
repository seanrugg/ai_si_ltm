"""The growth ledger (#342): learning, not volume, per member, swarm and fleet.

Derived from records at read time, by the conventions in ``docs/mind.md``;
nothing new is stored. Built on what the server already counts:

- action outcomes and record volume: ``recall.outcome_groups`` and
  ``recall.volume_by_day`` (the same queries as ``/admin/outcomes`` and
  ``/admin/volume``);
- promoted lessons: ``policy_lifecycle.classify`` (policies in force) and
  ``records._ref_targets`` (what a policy cites);
- model families: each mind record's ``payload.model_family``, written with the
  record, so a later model change doesn't rewrite history. A record without one
  falls back to the author's spec sheet model (``ltms_mind.config.family_of``).

What's new here:

- **Cross-family review.** A lesson's status is its latest verdict from a
  reviewer of a *different* model family. Same-family reviews are only counted.
  When two reviewers of other families disagree on the same day, ``refuted``
  wins (err on the safe side). An unknown family on either side never counts as
  cross-family.
- **Predictions.** Checks (``refs.checks``) score them: counts, and a Brier
  score over the ones that gave a ``payload.probability``.
- **Mind cycles.** Their ``gated`` and ``rejected`` reasons, ``written``,
  ``brain_errors`` and ``tokens``, added up.

And the mind's settings per member (pause, budgets), kept in
``runtime_settings`` and handed to the member at sign-in.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Member, Record
from server.services import policy_lifecycle, recall, runtime_settings, specs
from server.services.errors import ServiceError
from server.services.records import _ref_targets

VERDICTS = ("verified", "refuted", "unsure")
LIST_MAX = 50     # lessons listed per member, newest first (the totals count them all)
CYCLES_MAX = 10   # recent mind cycles listed per member
SETTINGS_PREFIX = "mind:"
BUDGET_KEYS = ("calls_per_wake", "tokens_per_wake", "writes_per_day")
MAX_BUDGET = 1_000_000


def _key(r: dict) -> str:
    return f"{r['agent']}:{r['seq']}"


def _payload(r: dict) -> dict:
    p = r.get("payload")
    return p if isinstance(p, dict) else {}


def _tags(r: dict) -> list:
    return r.get("tags") or []


def _ref(r: dict, name: str) -> list[str]:
    v = (r.get("refs") or {}).get(name)
    return [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)]


def _family_from_specs(member: Member) -> str | None:
    from ltms_mind.config import family_of
    models = (specs.resolve(member).get("models") or {}).get("value") or []
    for m in models:
        fam = family_of(m.get("name") if isinstance(m, dict) else None)
        if fam:
            return fam
    return None


async def _records(session: AsyncSession, *, since: datetime, types: str, agents: list[str] | None = None,
                   tag: str | None = None) -> list[dict]:
    """Records with payloads, oldest first: mind records are few, so no paging."""
    stmt = recall.build_query(session, recall.RecordFilter(type=types, tag=tag, since=since), recall.Scope())
    if agents is not None:
        stmt = stmt.where(Record.agent.in_(agents))
    recs = list(await session.scalars(stmt.order_by(Record.ts, Record.id)))
    payloads = await recall.fetch_payloads(session, recs)
    return [recall.to_wire(r, payloads.get(r.id)) for r in recs]


def _empty() -> dict:
    return {
        "lessons": {"proposed": 0, "verified": 0, "refuted": 0, "unsure": 0, "unreviewed": 0, "promoted": 0,
                    "studied": 0, "same_family_reviews": 0},
        "reviews_given": {"total": 0, "verified": 0, "refuted": 0, "unsure": 0, "cross_family": 0},
        "predictions": {"made": 0, "checked": 0, "true": 0, "false": 0, "unclear": 0, "brier": None},
        "questions": {"asked": 0, "answered_by_others": 0},
        "answers": {"given": 0},
        "directives": {"reported": 0, "done": 0, "partial": 0, "couldnt": 0},
        "actions": {"total": 0, "outcomes": {}},
        "cycles": {"count": 0, "dreams": 0, "paused": 0, "written": 0, "brain_errors": 0, "tokens": 0,
                   "gated": {}, "rejected": {}},
        "volume": {"records": 0},
        "learning_ratio": None,
        "daily": {},   # day -> {records, lessons, verified}: the Growth tab's trend
    }


def _day(d: dict, day: str) -> dict:
    return d.setdefault(day, {"records": 0, "lessons": 0, "verified": 0})


async def ledger(session: AsyncSession, agents: list[str], *, days: int = 30,
                 now: datetime | None = None) -> dict[str, dict]:
    """The growth ledger of each member in ``agents``, over the last ``days`` days."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    out = {a: {"member": a, "days": days, "families": [], **_empty()} for a in agents}
    if not agents:
        return out

    members = {m.name: m for m in await session.scalars(select(Member).where(Member.name.in_(agents)))}
    fallback: dict[str, str | None] = {}

    async def family(r: dict) -> str | None:
        fam = _payload(r).get("model_family")
        if isinstance(fam, str) and fam.strip():
            return fam.strip().lower()
        agent = r.get("agent")
        if agent not in fallback:
            m = members.get(agent) or await session.scalar(select(Member).where(Member.name == agent))
            fallback[agent] = _family_from_specs(m) if m is not None else None
        return fallback[agent]

    mine = set(agents)
    # Every lesson in the window: a member's reviews of other members' lessons need their family.
    all_lessons = await _records(session, since=since, types="lesson")
    lessons = [r for r in all_lessons if r["agent"] in mine]
    # Reviews, checks and answers may come from any member, about this member's records.
    reviews = await _records(session, since=since, types="thought", tag="review")
    predictions = await _records(session, since=since, types="thought", tag="prediction", agents=agents)
    checks = await _records(session, since=since, types="thought", tag="prediction-check")
    questions = await _records(session, since=since, types="communication", tag="question", agents=agents)
    answers = await _records(session, since=since, types="communication", tag="answer")
    reports = await _records(session, since=since, types="communication", tag="directive-report", agents=agents)
    cycles = await _records(session, since=since, types="event", tag="mind_cycle", agents=agents)
    energy = await _records(session, since=since, types="event", tag="energy-outcome", agents=agents)

    # ---- lessons and their reviews
    lesson_family = {_key(r): (r["agent"], await family(r)) for r in all_lessons}
    for r in lessons:
        out[r["agent"]]["lessons"]["proposed"] += 1
        out[r["agent"]]["lessons"]["studied"] += int(bool(_payload(r).get("sources")))
        _day(out[r["agent"]]["daily"], str(r.get("ts", ""))[:10])["lessons"] += 1
    reviewers: dict[str, list[dict]] = defaultdict(list)   # lesson key -> every review, for the list
    cross: dict[str, list[tuple[str, str]]] = defaultdict(list)   # lesson key -> [(day, verdict)]
    for r in reviews:
        verdict = _payload(r).get("verdict")
        if verdict not in VERDICTS:
            continue
        rfam = await family(r)
        if r["agent"] in mine:
            g = out[r["agent"]]["reviews_given"]
            g["total"] += 1
            g[verdict] += 1
        for target in _ref(r, "reviews"):
            if target in lesson_family and lesson_family[target][0] in mine:
                reviewers[target].append({"key": _key(r), "by": r["agent"], "family": rfam, "verdict": verdict,
                                          "reasons": str(_payload(r).get("reasons") or "")[:300],
                                          "ts": r.get("ts")})
            author = lesson_family.get(target)
            if author is None:
                if r["agent"] in mine:  # a review of a lesson outside this window or member set
                    target_agent = target.rpartition(":")[0]
                    tm = members.get(target_agent) or await session.scalar(
                        select(Member).where(Member.name == target_agent))
                    tfam = _family_from_specs(tm) if tm is not None else None
                    if rfam and tfam and rfam != tfam:
                        out[r["agent"]]["reviews_given"]["cross_family"] += 1
                continue
            a_agent, a_fam = author
            is_cross = bool(rfam and a_fam and rfam != a_fam)
            if r["agent"] in mine and is_cross:
                out[r["agent"]]["reviews_given"]["cross_family"] += 1
            if a_agent not in mine:
                continue
            if is_cross:
                cross[target].append((str(r.get("ts", ""))[:10], verdict, str(r.get("ts", ""))))
            elif r["agent"] != a_agent:
                out[a_agent]["lessons"]["same_family_reviews"] += 1

    in_force = await _in_force_citations(session)
    for r in lessons:
        k, L = _key(r), out[r["agent"]]["lessons"]
        verdicts = sorted(cross.get(k, []), key=lambda v: v[2])
        if not verdicts:
            status = "unreviewed"
        else:
            last_day = verdicts[-1][0]
            same_day = {v for d, v, _ in verdicts if d == last_day}
            status = "refuted" if "refuted" in same_day else verdicts[-1][1]
            if status == "verified":
                _day(out[r["agent"]]["daily"], last_day)["verified"] += 1
        L[status] += 1
        if k in in_force:
            L["promoted"] += 1
        out[r["agent"]].setdefault("lesson_list", []).append({
            "key": k, "summary": r.get("summary"), "ts": r.get("ts"), "family": lesson_family[k][1],
            "status": status, "promoted": k in in_force, "reviews": reviewers.get(k, [])})

    # ---- predictions and their checks
    by_pred = {_key(r): r for r in predictions}
    for r in predictions:
        out[r["agent"]]["predictions"]["made"] += 1
    briers: dict[str, list[float]] = defaultdict(list)
    seen_checked: set[str] = set()
    for c in checks:
        result = _payload(c).get("outcome")
        for target in _ref(c, "checks"):
            pred = by_pred.get(target)
            if pred is None or target in seen_checked or result not in ("true", "false", "unclear"):
                continue
            seen_checked.add(target)
            P = out[pred["agent"]]["predictions"]
            P["checked"] += 1
            P[result] += 1
            prob = _payload(pred).get("probability")
            if result in ("true", "false") and isinstance(prob, (int, float)) and not isinstance(prob, bool) \
                    and 0 <= prob <= 1:
                briers[pred["agent"]].append((prob - (1.0 if result == "true" else 0.0)) ** 2)
    for a, scores in briers.items():
        out[a]["predictions"]["brier"] = round(sum(scores) / len(scores), 4)

    # ---- questions and answers
    asked = {_key(r): r["agent"] for r in questions}
    for r in questions:
        out[r["agent"]]["questions"]["asked"] += 1
    answered: set[str] = set()
    for r in answers:
        if r["agent"] in mine:
            out[r["agent"]]["answers"]["given"] += 1
        for target in _ref(r, "answers"):
            asker = asked.get(target)
            if asker is not None and r["agent"] != asker and target not in answered:
                answered.add(target)
                out[asker]["questions"]["answered_by_others"] += 1

    # ---- directive reports
    for r in reports:
        D = out[r["agent"]]["directives"]
        D["reported"] += 1
        status = _payload(r).get("status")
        if status in ("done", "partial", "couldnt"):
            D[status] += 1

    # ---- energy outcomes (members that have them)
    for r in energy:
        oc = _payload(r).get("outcome") or r.get("outcome")
        if isinstance(oc, str):
            e = out[r["agent"]].setdefault("energy", {})
            name = oc.replace("-", "_")
            e[name] = e.get(name, 0) + 1

    # ---- mind cycles: the awake event, or a dream's end
    for r in cycles:
        p = _payload(r)
        if "dream_cycle" in _tags(r) and p.get("phase") != "end":
            continue
        counts = p.get("mind") if "dream_cycle" in _tags(r) else p
        if not isinstance(counts, dict):
            continue
        C = out[r["agent"]]["cycles"]
        out[r["agent"]].setdefault("recent_cycles", []).append({
            "key": _key(r), "ts": r.get("ts"), "mode": counts.get("mode"), "brain": counts.get("brain"),
            "considered": counts.get("considered", 0), "written": counts.get("written", 0),
            "paused": bool(counts.get("paused")), "gated": counts.get("gated") or {},
            "rejected": [x for x in (counts.get("rejected") or []) if isinstance(x, dict)][:10]})
        C["count"] += 1
        C["dreams"] += int(counts.get("mode") == "dreaming")
        C["paused"] += int(bool(counts.get("paused")))
        C["written"] += int(counts.get("written") or 0)
        C["brain_errors"] += int(counts.get("brain_errors") or 0)
        C["tokens"] += int((counts.get("budget") or {}).get("tokens") or 0)
        for reason, n in (counts.get("gated") or {}).items():
            C["gated"][reason] = C["gated"].get(reason, 0) + int(n or 0)
        for rej in counts.get("rejected") or []:
            if isinstance(rej, dict) and isinstance(rej.get("reason"), str):
                C["rejected"][rej["reason"]] = C["rejected"].get(rej["reason"], 0) + 1

    # ---- action outcomes and volume: the shared queries
    for g in await recall.outcome_groups(session, since=since, agents=agents):
        A = out[g["agent"]]["actions"]
        A["total"] += g["total"]
        for oc, n in g["outcomes"].items():
            A["outcomes"][oc] = A["outcomes"].get(oc, 0) + n
    for row in await recall.volume_by_day(session, since=since, where=[Record.agent.in_(agents)]):
        out[row["agent"]]["volume"]["records"] += row["records"]
        _day(out[row["agent"]]["daily"], str(row["day"]))["records"] += row["records"]

    # The families each member wrote with, from its own mind records.
    for r in lessons + reviews + predictions + questions + answers + reports:
        if r["agent"] in mine:
            fam = _payload(r).get("model_family")
            if isinstance(fam, str) and fam.strip() and fam.strip().lower() not in out[r["agent"]]["families"]:
                out[r["agent"]]["families"].append(fam.strip().lower())
    for g in out.values():
        g["families"] = sorted(g["families"])
        g["lesson_list"] = sorted(g.get("lesson_list", []), key=lambda x: str(x["ts"]), reverse=True)[:LIST_MAX]
        g["recent_cycles"] = sorted(g.get("recent_cycles", []), key=lambda x: str(x["ts"]), reverse=True)[:CYCLES_MAX]
        g["daily"] = dict(sorted(g["daily"].items()))
        written = g["volume"]["records"]
        g["learning_ratio"] = round(g["lessons"]["verified"] / written, 6) if written else None
    return out


async def _in_force_citations(session: AsyncSession) -> set[str]:
    """Record keys that an in-force policy cites: a lesson cited there is promoted."""
    policies = list(await session.scalars(select(Record).where(Record.type == "policy")))
    status = policy_lifecycle.classify(policies)
    cited: set[str] = set()
    for p in policies:
        if status.get(policy_lifecycle.policy_key(p), {}).get("status") == "in_force":
            cited |= _ref_targets(p.refs)
    return cited


def roll_up(per_member: dict[str, dict], *, days: int) -> dict:
    """The same keys summed over members (no averaged ratios), plus a member list."""
    total = {"days": days, **_empty()}
    briers: list[tuple[float, int]] = []
    for g in per_member.values():
        for section, value in g.items():
            if section in ("member", "days", "families", "learning_ratio"):
                continue
            if isinstance(value, dict):
                dest = total.setdefault(section, {})
                _add(dest, value)
        if g["predictions"]["brier"] is not None:
            n = g["predictions"]["true"] + g["predictions"]["false"]
            briers.append((g["predictions"]["brier"], max(n, 1)))
    if briers:
        total["predictions"]["brier"] = round(sum(b * n for b, n in briers) / sum(n for _, n in briers), 4)
    else:
        total["predictions"]["brier"] = None
    written = total["volume"]["records"]
    total["learning_ratio"] = round(total["lessons"]["verified"] / written, 6) if written else None
    total["daily"] = dict(sorted(total.get("daily", {}).items()))
    total["families"] = sorted({f for g in per_member.values() for f in g["families"]})
    total["members"] = sorted(
        ({"name": g["member"], "learning_ratio": g["learning_ratio"], "verified_lessons": g["lessons"]["verified"]}
         for g in per_member.values()),
        key=lambda m: (-m["verified_lessons"], m["name"]))
    return total


def _add(dest: dict, src: dict) -> None:
    for k, v in src.items():
        if k == "brier":
            continue
        if isinstance(v, dict):
            _add(dest.setdefault(k, {}), v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            dest[k] = (dest.get(k) or 0) + v


# ------------------------------------------------------------------ mind settings
def _settings_key(member: Member) -> str:
    return f"{SETTINGS_PREFIX}{member.id}"


def _validate_settings(body: dict, current: dict) -> dict:
    if not isinstance(body, dict) or not set(body) <= {"paused", "budget"}:
        raise ServiceError(422, "bad_mind_settings", "body may have paused (true/false) and budget")
    out = {"paused": bool(current.get("paused")), "budget": dict(current.get("budget") or {})}
    if "paused" in body:
        if not isinstance(body["paused"], bool):
            raise ServiceError(422, "bad_mind_settings", "paused must be true or false")
        out["paused"] = body["paused"]
    if "budget" in body:
        b = body["budget"]
        if not isinstance(b, dict) or not set(b) <= set(BUDGET_KEYS):
            raise ServiceError(422, "bad_mind_settings", f"budget keys: {', '.join(BUDGET_KEYS)}")
        for k, v in b.items():
            if v is None:
                out["budget"].pop(k, None)
            elif isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_BUDGET:
                raise ServiceError(422, "bad_mind_settings", f"budget.{k} is a whole number from 0, or null")
            else:
                out["budget"][k] = v
    return out


def settings_dict(row) -> dict:
    value = (row.value if row is not None else None) or {}
    return {"paused": bool(value.get("paused")),
            "budget": {k: v for k, v in (value.get("budget") or {}).items() if k in BUDGET_KEYS}}


async def get_settings(session: AsyncSession, member: Member) -> dict:
    return settings_dict(await runtime_settings.get_setting(session, _settings_key(member)))


async def set_settings(session: AsyncSession, member: Member, body: dict, *, admin: str) -> dict:
    """Pause a mind or lower its budgets. The member's loop never goes above its own
    ``MIND_*`` caps, whatever is set here (reduce-only)."""
    old = await get_settings(session, member)
    new = _validate_settings(body, old)
    if new != old:
        await runtime_settings.set_setting(session, _settings_key(member), new, updated_by=admin)
        session.add(AuditLog(member_id=member.id, actor=admin, action="mind_settings_change",
                             detail={"old": old, "new": new}))
    return new


async def for_sign_in(session: AsyncSession, member: Member) -> dict | None:
    """What the member's mind is told at sign-in, or None when nothing is set."""
    row = await runtime_settings.get_setting(session, _settings_key(member))
    return settings_dict(row) if row is not None else None


