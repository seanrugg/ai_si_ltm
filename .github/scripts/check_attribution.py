"""Check a PR's attribution block (AGENTS.md rule 9, issue #389).

Passes when:
- the description has Agent-Harness, Agent-Model and Agent-Host with real values
  (lines may be indented);
- the description says ``Agent-Harness: none`` (a human edit) and no commit or
  label says an agent did the work;
- the description has no filled-in block, no commit carries an agent trailer, and the PR
  has no ``agent-task`` label (a human PR that never filled in the template).

Fails an agent PR without a complete block, and prints exactly what to paste.

Inputs (environment): ``BODY`` the PR description, ``LABELS`` comma-separated
label names, ``COMMITS_FILE`` a file of commit messages separated by NUL bytes.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

KEYS = ("Agent-Harness", "Agent-Model", "Agent-Host")
AGENT_LABEL = "agent-task"

# Co-Authored-By trailers that name an AI model or tool.
_AI_NAMES = (
    r"claude|anthropic|copilot|openai|chatgpt|gpt-?\d|codex|gemini|qwen|nemotron|llama|"
    r"mistral|deepseek|cursor|devin|aider|grok"
)
_AI_COAUTHOR = re.compile(rf"^\s*Co-Authored-By:.*\b({_AI_NAMES})", re.IGNORECASE | re.MULTILINE)
_AGENT_TRAILER = re.compile(r"^\s*(Agent-(Harness|Model|Host)|Claude-Session):", re.IGNORECASE | re.MULTILINE)

PASTE_AGENT = """Agent-Harness: <your harness, e.g. claude-code>
Agent-Model: <your model id, e.g. claude-opus-4-1>
Agent-Host: <where it ran, e.g. cloud>"""
PASTE_HUMAN = "Agent-Harness: none"


@dataclass
class Result:
    ok: bool
    message: str


def block_values(body: str) -> dict[str, list[str]]:
    """Values of each Agent-* line in the body, placeholders and blanks dropped."""
    found: dict[str, list[str]] = {}
    for key in KEYS:
        values = [v.strip() for v in re.findall(rf"^[ \t]*{key}:[ \t]*(.*?)[ \t]*$", body, re.MULTILINE)]
        found[key] = [v for v in values if v and not v.startswith("<")]
    return found


def agent_commits(messages: list[str]) -> int:
    """How many commit messages carry an agent trailer."""
    return sum(1 for m in messages if _AI_COAUTHOR.search(m) or _AGENT_TRAILER.search(m))


def check(body: str, labels: list[str], commit_messages: list[str]) -> Result:
    values = block_values(body or "")
    n_agent = agent_commits(commit_messages)
    labelled = AGENT_LABEL in {label.strip() for label in labels}
    agent_evidence = []
    if n_agent:
        agent_evidence.append(f"{n_agent} commit(s) carry an agent trailer")
    if labelled:
        agent_evidence.append(f"the PR has the '{AGENT_LABEL}' label")

    harness = values["Agent-Harness"]
    real_harness = [v for v in harness if v.lower() != "none"]
    if harness and not real_harness:  # only "Agent-Harness: none"
        if agent_evidence:
            return Result(False, _failure(
                f"The description says 'Agent-Harness: none', but {' and '.join(agent_evidence)}. "
                "Replace 'none' with the agent's details:", human=False))
        return Result(True, "Attribution OK: human PR (Agent-Harness: none).")

    missing = [k for k in KEYS if not values[k]]
    if not missing:
        return Result(True, "Attribution OK: " + ", ".join(f"{k}={values[k][0]}" for k in KEYS))

    if not agent_evidence and not any(values.values()):
        return Result(True, "Attribution OK: no filled-in attribution block and no agent trailers or "
                            f"'{AGENT_LABEL}' label, so this is treated as a human PR.")

    why = f"Missing or placeholder: {', '.join(missing)}."
    if agent_evidence:
        why += f" ({' and '.join(agent_evidence)}.)"
    return Result(False, _failure(why + " Paste this at the end of the PR description:", human=True))


def _failure(lead: str, human: bool) -> str:
    text = (f"{lead}\n\n{PASTE_AGENT}\n\n"
            "Replace each <...> with real values (AGENTS.md rule 9). Lines may be indented.")
    if human:
        text += f"\n\nHuman edit with no AI agent? Paste this line instead:\n\n{PASTE_HUMAN}"
    return text


def main() -> int:
    body = os.environ.get("BODY") or ""
    labels = [label for label in (os.environ.get("LABELS") or "").split(",") if label]
    messages: list[str] = []
    path = os.environ.get("COMMITS_FILE")
    if path:
        with open(path, encoding="utf-8", errors="replace") as fh:
            messages = [m for m in fh.read().split("\0") if m.strip()]
    result = check(body, labels, messages)
    if result.ok:
        print(result.message)
        return 0
    print(f"::error title=Attribution::{result.message.splitlines()[0]}")
    print(result.message)
    return 1


if __name__ == "__main__":
    sys.exit(main())
