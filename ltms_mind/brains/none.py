"""No brain: for devices without a model (a hub, a microcontroller's Linux side).

It never dreams. It only does the rule-based tasks a device can do without a
model: checking predictions whose measure is a number the loop could count
(``inputs["observed"]``). Duties (charter, health on change, the one-time
introduction) are done by the loop itself.
"""
from __future__ import annotations

import operator

from ltms_mind.brains.base import Brain, Context, Proposal, Task

OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


def check_numeric(measure: dict, observed: float) -> bool:
    op = OPS[measure["op"]]
    return bool(op(observed, float(measure["value"])))


class NoBrain(Brain):
    name = "none"
    can_dream = False

    def propose(self, task: Task, context: Context) -> list[Proposal]:
        self.last_tokens = 0
        if task.kind != "check" or "observed" not in task.inputs:
            return []
        rec = task.inputs["prediction"]
        measure = (rec.get("payload") or {}).get("measure") or {}
        observed = task.inputs["observed"]
        held = check_numeric(measure, observed)
        claim = (rec.get("payload") or {}).get("claim") or rec.get("summary") or "prediction"
        return [Proposal(
            kind="prediction-check",
            summary=f"Prediction {'held' if held else 'failed'}: {claim}"[:500],
            refs={"checks": task.key},
            payload={"outcome": "true" if held else "false",
                     "evidence": {"observed": observed, "measure": measure, "method": "count"}},
            rationale="numeric measure compared by rule")]
