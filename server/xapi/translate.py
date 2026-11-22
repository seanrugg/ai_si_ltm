"""Holocron record -> xAPI statement translator (AIAAP 0.10.1, mapping v1;
AIAAP 0.12.0, mapping v2 for agent tool calls and usage reports, #472).

A pure function: no I/O, no database, no network. The LRS forwarder (#61)
calls it to turn one LTMS record into one xAPI statement.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

MAPPING_VERSION = "1"
AI = "https://w3id.org/xapi-ai"
EXT = AI + "/extensions/"
PROFILE_VERSION = AI + "/profile/0.10.1"
# Dataset reads (#239) are defined in AIAAP 0.11.0.
PROFILE_VERSION_READS = AI + "/profile/0.11.0"
READ_RESOURCE = (AI + "/verbs/read-resource", "read resource")
READ_SOURCES = ("served", "reported")
# Derived from the 0.10.0 IRI (uuid5(NAMESPACE_URL,
# "https://w3id.org/xapi-ai/profile/0.10.0/holocron-mapping/1")) and fixed
# for mapping version 1: it does not follow PROFILE_VERSION, so statement ids
# never change (issue #179).
NAMESPACE = uuid.UUID("19413c7a-d33c-5129-b99b-07d709cef285")

VALID_XAPI_VERSIONS = ("2.0.0", "1.0.3")

# Mapping version 2 (#472, AIAAP 0.12.0 agent harness module): two record kinds
# get their own statement shape; every other record maps exactly as in v1.
# Statement ids keep the v1 NAMESPACE: a StatementRef is built from the cause's
# hash alone, so a tool call must keep the id any other record would give it.
MAPPING_VERSION_2 = "2"
PROFILE_VERSION_HARNESS = AI + "/profile/0.12.0"
INVOKED_TOOL = (AI + "/verbs/invoked-tool", "invoked tool")
REPORTED_USAGE = (AI + "/verbs/reported-usage", "reported usage")
HARNESS_TAGS = {"hermes": {"name": "hermes", "plugin": "ltms_recorder"}}

GLOSSARY = "https://id.i2idl.org/concepts/"
SKOS_CONCEPT = "http://www.w3.org/2004/02/skos/core#Concept"
# record type -> glossary concept; every statement also gets learning-record.
# Perceptions are not listed: a perception tagged telemetry is *sensed* (data
# from one of the member's sensors) and gets SENSED_CONCEPT; a plain
# perception is an interpretation and gets no record concept (issue #179).
RECORD_CONCEPTS = {
    "thought": "metacognition",
    "communication": "social-learning",
    "event": "learning-event",
    "lesson": "experiential-learning",
}
SENSED = (AI + "/verbs/sensed", "sensed")
SENSED_CONCEPT = "sensor"

# record type -> (verb IRI, display)
VERBS = {
    "perception": (AI + "/verbs/perceived", "perceived"),
    "thought": (AI + "/verbs/reasoned", "reasoned"),
    "decision": (AI + "/verbs/decided", "decided"),
    "action": (AI + "/verbs/performed", "performed"),
    "communication": (AI + "/verbs/communicated", "communicated"),
    "event": ("http://adlnet.gov/expapi/verbs/experienced", "experienced"),
    "lesson": (AI + "/verbs/learned", "learned"),
    "policy": (AI + "/verbs/set-policy", "set policy"),
    "system": (AI + "/verbs/reported", "reported"),
}

# member kind -> xAPI agent kind; anything not listed -> "ai-agent"
AGENT_KINDS = {"robot": "robot", "agent": "ai-agent", "assistant": "ai-agent", "relay": "relay"}


def _agent_kind(kind: str) -> str:
    return AGENT_KINDS.get(kind, "ai-agent")


def _is_sensed(rec: dict) -> bool:
    """A perception tagged telemetry: data from one of the member's sensors.

    Any sensor's data translates (issue #179); the record type stays
    ``perception`` — the tag selects the "sensed" verb.
    """
    return rec.get("type") == "perception" and "telemetry" in (rec.get("tags") or [])


def harness_kind(rec: dict, payload) -> str | None:
    """``"tool-call"``, ``"usage"`` or None (mapping v1).

    Mapping v2 applies only when the payload has what the shape needs, so a
    purged payload falls back to the v1 statement instead of failing.
    """
    tags = rec.get("tags") or []
    if not isinstance(payload, dict):
        return None
    if rec.get("type") == "action" and "tool-call" in tags and isinstance(payload.get("tool_name"), str) \
            and payload["tool_name"]:
        return "tool-call"
    if rec.get("type") == "perception" and "agent-rollup" in tags and isinstance(payload.get("date"), str):
        return "usage"
    return None


def _harness(rec: dict, payload: dict) -> dict | None:
    """The harness that recorded it: from a tag (tool calls), or from the rollup's machine facts."""
    machine = payload.get("machine") if isinstance(payload.get("machine"), dict) else {}
    names = [t for t in rec.get("tags") or [] if t in HARNESS_TAGS]
    if not names and "hermes_version" in machine:
        names = ["hermes"]  # a Hermes rollup (#470) is tagged telemetry,internal,agent-rollup only
    if not names:
        return None
    harness = dict(HARNESS_TAGS[names[0]])
    version = machine.get("plugin_version")
    if isinstance(version, str) and version:
        harness["version"] = version
    return harness


def iso_duration_ms(ms) -> str | None:
    """Milliseconds -> ISO 8601 duration (``1250`` -> ``PT1.25S``)."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms < 0:
        return None
    seconds = round(ms / 1000, 3)
    text = f"{seconds:.3f}".rstrip("0").rstrip(".")
    return f"PT{text}S"


def usage_counters(payload: dict) -> dict:
    """The rollup payload (#470) as the AIAAP 0.12.0 ``usage`` extension: generic field names, no date."""
    usage = {k: v for k, v in payload.items() if k not in ("date", "rollup_schema")}
    usage = {"usage_schema": payload.get("rollup_schema", 1), **usage}
    machine = usage.get("machine")
    if isinstance(machine, dict) and "hermes_version" in machine:
        machine = dict(machine)
        machine["harness_version"] = machine.pop("hermes_version")
        usage["machine"] = machine
    return usage


def _apply_v2(statement: dict, rec: dict, payload: dict, kind: str, base: str) -> None:
    """Reshape a v1 statement for mapping v2. Provenance extensions are kept as they are."""
    record_activity = statement["object"]
    context = statement["context"]
    ext = context["extensions"]
    ext[EXT + "holocron/mapping-version"] = MAPPING_VERSION_2
    context["contextActivities"]["category"][0]["id"] = PROFILE_VERSION_HARNESS
    context["contextActivities"].setdefault("other", []).append(record_activity)
    harness = _harness(rec, payload)
    if harness:
        ext[EXT + "harness"] = harness
    result = statement.setdefault("result", {})
    result_ext = result.setdefault("extensions", {})

    if kind == "tool-call":
        name = payload["tool_name"]
        statement["verb"] = {"id": INVOKED_TOOL[0], "display": {"en": INVOKED_TOOL[1]}}
        statement["object"] = _activity(base, f"/tools/{quote(name, safe='')}", AI + "/activity-type/tool", name)
        ext[EXT + "tool-name"] = name
        if "args" in payload:
            ext[EXT + "tool-parameters"] = payload["args"]
            if payload.get("args_truncated"):
                ext[EXT + "tool-parameters-truncated"] = True
        for key, field in (("session_id", "session-id"), ("model", "model-id")):
            if isinstance(payload.get(key), str) and payload[key]:
                ext[EXT + field] = payload[key]
        duration = iso_duration_ms(payload.get("duration_ms"))
        if duration:
            result["duration"] = duration
            result_ext[EXT + "latency-ms"] = payload["duration_ms"]
        if isinstance(payload.get("result_raw"), str):
            result["response"] = payload["result_raw"]
            result_ext[EXT + "tool-result-truncated"] = bool(payload.get("result_truncated"))
    else:
        date = payload["date"]
        statement["verb"] = {"id": REPORTED_USAGE[0], "display": {"en": REPORTED_USAGE[1]}}
        statement["object"] = _activity(base, f"/usage/{rec['agent']}/{quote(date, safe='')}",
                                        AI + "/activity-type/usage-report", rec.get("summary"))
        ext[EXT + "usage-period"] = date
        result_ext.pop(EXT + "telemetry", None)  # a usage report is not sensor data
        result_ext[EXT + "usage"] = usage_counters(payload)
        # The signed payload stays available, so the payload hash still verifies.
        ext[EXT + "holocron/payload"] = payload
        categories = context["contextActivities"]["category"]
        context["contextActivities"]["category"] = [
            c for c in categories if c["id"] != GLOSSARY + SENSED_CONCEPT]

    if not result_ext:
        result.pop("extensions", None)
    if not result:
        statement.pop("result", None)


def _account_agent(base: str, name: str) -> dict:
    return {"objectType": "Agent", "account": {"homePage": base, "name": name}}


def _activity(base: str, path: str, type_iri: str, name: str | None = None) -> dict:
    activity = {
        "objectType": "Activity",
        "id": f"{base}{path}",
        "definition": {"type": type_iri},
    }
    if name:
        activity["definition"]["name"] = {"en": name}
    return activity


def because_key(refs) -> str | None:
    """The single ``"agent:seq"`` cause in ``refs["because"]``, or None.

    A cause is linked with a StatementRef only when there is exactly one
    (a string or a one-item list); several causes stay in ``holocron/refs``.
    """
    if not isinstance(refs, dict):
        return None
    because = refs.get("because")
    if isinstance(because, list) and len(because) == 1:
        because = because[0]
    if isinstance(because, str) and ":" in because:
        return because
    return None


def record_to_statement(
    rec: dict, *, base: str, xapi_version: str = "2.0.0", payload: dict | None = None,
    ref_hashes: dict[str, str] | None = None,
) -> dict:
    if xapi_version not in VALID_XAPI_VERSIONS:
        raise ValueError(f"unsupported xAPI version: {xapi_version!r}")
    rtype = rec["type"]
    if rtype not in VERBS:
        raise ValueError(f"unknown record type: {rtype!r}")

    base = base.rstrip("/")
    kind = harness_kind(rec, payload)
    verb_iri, verb_display = SENSED if _is_sensed(rec) else VERBS[rtype]

    statement = {
        "id": str(uuid.uuid5(NAMESPACE, rec["hash"])),
        "actor": _account_agent(base, rec["agent"]),
        "verb": {"id": verb_iri, "display": {"en": verb_display}},
        "object": {
            "objectType": "Activity",
            "id": f"{base}/records/{rec['agent']}/{rec['seq']}",
            "definition": {"type": AI + "/activity-type/record"},
        },
        "timestamp": rec["ts"],
        "version": xapi_version,
    }

    if rec.get("summary"):
        statement["object"]["definition"]["name"] = {"en": rec["summary"]}

    category = [
        {
            "objectType": "Activity",
            "id": PROFILE_VERSION,
            "definition": {"type": "http://adlnet.gov/expapi/activities/profile"},
        }
    ]
    concept = SENSED_CONCEPT if _is_sensed(rec) else RECORD_CONCEPTS.get(rtype)
    for c in ("learning-record", concept):
        if c:
            category.append(
                {"objectType": "Activity", "id": GLOSSARY + c, "definition": {"type": SKOS_CONCEPT}}
            )
    grouping: list[dict] = []
    swarm = rec.get("swarm")
    if swarm:
        grouping.append(
            _activity(base, f"/swarms/{swarm}", AI + "/activity-type/swarm", swarm)
        )
    place = rec.get("place")
    if place:
        grouping.append(
            _activity(base, f"/places/{quote(place, safe='')}", AI + "/activity-type/place", place)  # place may contain spaces
        )

    context: dict = {"contextActivities": {"category": category}}
    if grouping:
        context["contextActivities"]["grouping"] = grouping

    # Cause and effect (issue #180): a single "because" becomes the standard
    # xAPI link to the cause's statement, whose id derives from its hash.
    key = because_key(rec.get("refs"))
    if key is not None and ref_hashes and key in ref_hashes:
        context["statement"] = {
            "objectType": "StatementRef",
            "id": str(uuid.uuid5(NAMESPACE, ref_hashes[key])),
        }

    context_agents: list[dict] = []
    if xapi_version == "2.0.0":
        for peer in rec.get("peers") or []:
            context_agents.append(
                {
                    "objectType": "contextAgent",
                    "agent": _account_agent(base, peer),
                    "relevantTypes": [AI + "/activity-type/peer-role"],
                }
            )
        relay = rec.get("relay")
        if relay:
            context_agents.append(
                {
                    "objectType": "contextAgent",
                    "agent": _account_agent(base, relay),
                    "relevantTypes": [AI + "/activity-type/relay-role"],
                }
            )
    if context_agents:
        context["contextAgents"] = context_agents
    if xapi_version == "2.0.0" and swarm:
        context["contextGroups"] = [{
            "objectType": "contextGroup",
            "group": {"objectType": "Group", "name": swarm,
                      "account": {"homePage": base, "name": swarm}},
            "relevantTypes": [AI + "/activity-type/swarm"],
        }]

    extensions = {
        EXT + "holocron/seq": rec["seq"],
        EXT + "holocron/hash": rec["hash"],
        EXT + "holocron/prev-hash": rec["prev_hash"],
        EXT + "holocron/payload-hash": rec["payload_hash"],
        EXT + "holocron/signature": rec["sig"],
        EXT + "holocron/record-type": rtype,
        EXT + "holocron/received-at": rec["received_at"],
        EXT + "holocron/mapping-version": MAPPING_VERSION,
        EXT + "agent-kind": _agent_kind(rec.get("agent_kind", "")),
    }
    tags = rec.get("tags")
    if tags:
        extensions[EXT + "holocron/tags"] = tags
    refs = rec.get("refs")
    if refs:
        extensions[EXT + "holocron/refs"] = refs
    sw = rec.get("sw")
    if sw:
        extensions[EXT + "software"] = sw
    context["extensions"] = extensions
    statement["context"] = context

    result: dict = {}
    result_ext: dict = {}
    outcome = rec.get("outcome")
    if outcome:
        if rtype == "action":
            result["success"] = outcome == "success"
        result_ext[EXT + "outcome"] = outcome

    # Everything LTMS receives, the LRS receives (Sean, 9 Nov). Sensed records
    # (a perception tagged telemetry) carry their whole payload in the
    # telemetry extension, whatever its kind (or none) — any sensor's data
    # translates (issue #179). Every other payload goes to context.extensions
    # as a JSON object; no payload means it was purged.
    if payload is None:
        extensions[EXT + "holocron/payload-purged"] = True
    elif _is_sensed(rec) and isinstance(payload, dict):
        result_ext[EXT + "telemetry"] = payload
    else:
        extensions[EXT + "holocron/payload"] = payload
    if rec.get("header_raw") is not None:
        # The exact signed bytes as a string, so hash and sig can be re-checked.
        extensions[EXT + "holocron/header"] = rec["header_raw"]

    if result_ext:
        result["extensions"] = result_ext
    if result:
        statement["result"] = result

    if kind is not None:
        _apply_v2(statement, rec, payload, kind, base)
    return statement


def read_to_statement(read: dict, dataset: dict, *, base: str, xapi_version: str = "2.0.0") -> dict:
    """One dataset read (#238) -> one xAPI statement (AIAAP 0.11.0 ``dataset-read``).

    ``read``: ``id``, ``agent`` (member name), ``version``, ``item_key``,
    ``cycle`` (UUID string or None), ``source``, ``ts`` (ISO), ``sha256`` (or None).
    ``dataset``: ``id`` and ``name``.
    """
    if xapi_version not in VALID_XAPI_VERSIONS:
        raise ValueError(f"unsupported xAPI version: {xapi_version!r}")
    if read["source"] not in READ_SOURCES:
        raise ValueError(f"unknown read source: {read['source']!r}")
    base = base.rstrip("/")
    version_path = f"/datasets/{dataset['id']}/v/{read['version']}"
    item = _activity(base, f"{version_path}/items/{quote(read['item_key'], safe='')}",
                     AI + "/activity-type/dataset-item", read["item_key"])
    context: dict = {
        "contextActivities": {
            "parent": [_activity(base, version_path, AI + "/activity-type/dataset", dataset["name"])],
            "grouping": [{"objectType": "Activity", "id": base,
                          "definition": {"type": AI + "/activity-type/fleet"}}],
            "category": [{
                "objectType": "Activity",
                "id": PROFILE_VERSION_READS,
                "definition": {"type": "http://adlnet.gov/expapi/activities/profile"},
            }],
        },
        "extensions": {
            EXT + "resource-version": str(read["version"]),
            EXT + "read-source": read["source"],
        },
    }
    if read.get("sha256"):
        context["extensions"][EXT + "resource-hash"] = read["sha256"]
    if read.get("cycle"):
        context["registration"] = str(read["cycle"])
    verb_iri, verb_display = READ_RESOURCE
    return {
        "id": str(uuid.uuid5(NAMESPACE, f"read:{read['id']}")),
        "actor": _account_agent(base, read["agent"]),
        "verb": {"id": verb_iri, "display": {"en": verb_display}},
        "object": item,
        "context": context,
        "timestamp": read["ts"],
        "version": xapi_version,
    }


SUMMARY_TYPE = AI + "/activity-type/message-summary"


def message_summary_statement(
    agent: str, records: list[dict], *, window_start: str, window_end: str, base: str,
    xapi_version: str = "2.0.0", agent_kind: str = "", swarm: str | None = None,
) -> dict:
    """One statement for a member's swarm messages in one window (#301).

    Deterministic aggregation only: a count, the distinct peers and the first and
    last record refs. No text is summarised. ``records`` are wire dicts (agent,
    seq, hash, ts, peers), oldest first. The id derives from the first and last
    hashes, so a re-send of the same window keeps its id.
    """
    if xapi_version not in VALID_XAPI_VERSIONS:
        raise ValueError(f"unsupported xAPI version: {xapi_version!r}")
    if not records:
        raise ValueError("a message summary needs at least one record")
    base = base.rstrip("/")
    first, last = records[0], records[-1]
    peers = sorted({p for r in records for p in (r.get("peers") or [])})
    statement = {
        "id": str(uuid.uuid5(NAMESPACE, f"summary:{first['hash']}:{last['hash']}")),
        "actor": _account_agent(base, agent),
        "verb": {"id": VERBS["communication"][0], "display": {"en": VERBS["communication"][1]}},
        "object": {
            "objectType": "Activity",
            "id": f"{base}/records/{agent}/{first['seq']}-{last['seq']}",
            "definition": {"type": SUMMARY_TYPE,
                           "name": {"en": f"{len(records)} swarm messages, {window_start} to {window_end}"}},
        },
        "timestamp": last["ts"],
        "version": xapi_version,
        "result": {"extensions": {
            EXT + "message-summary/count": len(records),
            EXT + "message-summary/peers": peers,
            EXT + "message-summary/first": f"{agent}:{first['seq']}",
            EXT + "message-summary/last": f"{agent}:{last['seq']}",
            EXT + "message-summary/window-start": window_start,
            EXT + "message-summary/window-end": window_end,
        }},
        "context": {
            "contextActivities": {"category": [
                {"objectType": "Activity", "id": PROFILE_VERSION,
                 "definition": {"type": "http://adlnet.gov/expapi/activities/profile"}},
                {"objectType": "Activity", "id": GLOSSARY + "social-learning", "definition": {"type": SKOS_CONCEPT}},
            ]},
            "extensions": {
                EXT + "holocron/record-type": "communication",
                EXT + "holocron/mapping-version": MAPPING_VERSION,
                EXT + "agent-kind": _agent_kind(agent_kind),
            },
        },
    }
    if swarm:
        statement["context"]["contextActivities"]["grouping"] = [
            _activity(base, f"/swarms/{swarm}", AI + "/activity-type/swarm", swarm)]
    return statement
