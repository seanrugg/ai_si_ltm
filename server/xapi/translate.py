"""Holocron record -> xAPI statement translator (AIAAP 0.10.0, mapping v1).

A pure function: no I/O, no database, no network. The LRS forwarder (#61)
calls it to turn one LTMS record into one xAPI statement.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from ltms_contract.canonical import canonicalize

MAPPING_VERSION = "1"
AI = "https://w3id.org/xapi-ai"
EXT = AI + "/extensions/"
PROFILE_VERSION = AI + "/profile/0.10.0"
# = uuid5(NAMESPACE_URL, PROFILE_VERSION + "/holocron-mapping/1")
NAMESPACE = uuid.UUID("19413c7a-d33c-5129-b99b-07d709cef285")
TELEMETRY_MAX_BYTES = 16 * 1024

VALID_XAPI_VERSIONS = ("2.0.0", "1.0.3")

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

_TELEMETRY_KINDS = {"pose", "navigation", "sensor"}


def _agent_kind(kind: str) -> str:
    return AGENT_KINDS.get(kind, "ai-agent")


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


def record_to_statement(
    rec: dict, *, base: str, xapi_version: str = "2.0.0", payload: dict | None = None
) -> dict:
    if xapi_version not in VALID_XAPI_VERSIONS:
        raise ValueError(f"unsupported xAPI version: {xapi_version!r}")
    rtype = rec["type"]
    if rtype not in VERBS:
        raise ValueError(f"unknown record type: {rtype!r}")

    base = base.rstrip("/")
    verb_iri, verb_display = VERBS[rtype]

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
    if rtype == "action":
        outcome = rec.get("outcome")
        if outcome:
            result["success"] = outcome == "success"
            result["extensions"] = {EXT + "outcome": outcome}
    elif rtype == "perception" and "telemetry" in (rec.get("tags") or []):
        if isinstance(payload, dict) and payload.get("kind") in _TELEMETRY_KINDS:
            if len(canonicalize(payload)) <= TELEMETRY_MAX_BYTES:
                telemetry = dict(payload)
            else:
                telemetry = {"kind": payload["kind"]}
                for key in ("frame", "sensor", "unit"):
                    if key in payload:
                        telemetry[key] = payload[key]
                telemetry["samples_count"] = len(payload.get("samples") or [])
                telemetry["truncated"] = True
            result["extensions"] = {EXT + "telemetry": telemetry}
    if result:
        statement["result"] = result

    return statement
