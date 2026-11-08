"""Policy lifecycle: proposal / in force / retired.

Lifecycle is expressed with existing header fields only -- no contract change:

* a ``policy`` tagged ``proposal`` is a **proposal**: listed for comment, never
  served as a rule;
* an enacted policy (no ``proposal`` tag) is **in force** unless an enacted
  policy cites it in ``refs`` under ``supersedes`` or ``revokes``;
* refs are resolved by ``"agent:seq"`` string match, refs that point at missing
  records are ignored, and a proposal's refs never retire anything.
"""

from __future__ import annotations

from server.models import Record

PROPOSAL_TAG = "proposal"
RETIRE_KEYS = ("supersedes", "revokes")


def _ref_strings(ref_value: str | list[str] | None) -> set[str]:
    """The ``"agent:seq"`` strings in one ref value (a string, a list, or absent)."""
    if ref_value is None:
        return set()
    if isinstance(ref_value, list):
        return set(ref_value)
    return {ref_value}


def is_proposal(record: Record) -> bool:
    return PROPOSAL_TAG in (record.tags or [])


def policy_key(record: Record) -> str:
    return f"{record.agent}:{record.seq}"


def classify(policies: list[Record]) -> dict[str, dict[str, str]]:
    """Lifecycle status for each policy, keyed by ``agent:seq``.

    Only enacted policies can retire, and only refs pointing at another record
    in ``policies`` count (refs to missing records are ignored). Returns
    ``{"status": "proposed"|"in_force"|"retired"}`` plus ``"retired_by"`` for
    retired policies.
    """
    by_key = {policy_key(p): p for p in policies}
    enacted = [p for p in policies if not is_proposal(p)]
    retired_by: dict[str, str] = {}
    for p in enacted:
        refs = p.refs if isinstance(p.refs, dict) else {}
        for key in RETIRE_KEYS:
            for target in _ref_strings(refs.get(key)):
                if target in by_key and target not in retired_by:
                    retired_by[target] = policy_key(p)
    out: dict[str, dict[str, str]] = {}
    for p in policies:
        key = policy_key(p)
        if is_proposal(p):
            out[key] = {"status": "proposed"}
        elif key in retired_by:
            out[key] = {"status": "retired", "retired_by": retired_by[key]}
        else:
            out[key] = {"status": "in_force"}
    return out
