"""Prompt templates for model brains (#340, #341), as plain text an admin can read.

``common.txt`` wraps every task: who the member is, its charter duties, the gate's
rules and the output format. The task kind's own file (``answer.txt``,
``directive.txt``, ...) supplies the instructions. ``decide.txt`` is for deciding
whether to follow a directive. Placeholders are ``{{name}}``, so the JSON examples
in the text need no escaping.
"""
from __future__ import annotations

import json
import os

from ltms_mind.brains.base import ALLOWED_KINDS

HERE = os.path.dirname(__file__)
KINDS = ("directive", "answer", "review", "check", "reflect", "study")
MAX_INPUTS = 24000  # characters of task data passed to a model
EXAMPLE_KEY = "v3-c7r:120"  # rule 2's example when the task's own key isn't a record key


def load(name: str) -> str:
    with open(os.path.join(HERE, f"{name}.txt"), encoding="utf-8") as f:
        return f.read()


def fill(template: str, **values) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def data(inputs) -> str:
    text = json.dumps(inputs, default=str, ensure_ascii=False, indent=1)
    # The data sits between <data> tags: don't let it close them early.
    text = text.replace("</data>", "<\\/data>")
    return text if len(text) <= MAX_INPUTS else text[:MAX_INPUTS] + "\n... (truncated)"


def kinds_text(task_kind: str) -> str:
    """'lesson, question or prediction': the kinds this task allows, for rule 1 (#540)."""
    allowed = [f'"{k}"' for k in ALLOWED_KINDS[task_kind]]
    return allowed[0] if len(allowed) == 1 else ", ".join(allowed[:-1]) + " or " + allowed[-1]


def example_key(task) -> str:
    """A real record key for rule 2's example: the task's own when it is one (a small
    model copies a placeholder like "<agent>:<seq>" literally, #540)."""
    from ltms_mind.gate import (
        REF_RE,  # the gate's own pattern; imported late (gate imports brains)
    )
    return task.key if isinstance(task.key, str) and REF_RE.match(task.key) else EXAMPLE_KEY


def task_prompt(task, context) -> str:
    if task.kind not in KINDS:
        raise ValueError(f"no prompt template for task kind {task.kind!r}")
    return fill(load("common"), member=context.member, swarm=context.swarm or "none", family=context.family or "unknown",
                now=context.now, mode=context.mode, task=task.text, instructions=load(task.kind).strip(),
                kinds=kinds_text(task.kind), example_key=example_key(task), inputs=data(task.inputs))


def decide_prompt(directive: dict, context) -> str:
    return fill(load("decide"), member=context.member, swarm=context.swarm or "none", now=context.now,
                inputs=data(directive))
