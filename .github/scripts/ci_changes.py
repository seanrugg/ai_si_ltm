"""Decide which CI jobs a change can affect (issue #389).

Reads changed file paths (one per line) on stdin and prints GitHub Actions
outputs, e.g. ``python=true``. ``--all`` prints every output as true; CI uses it
for pushes to main, the nightly schedule and manual runs.

Docs-only changes (``*.md``, ``docs/**``) match nothing, so the heavy jobs skip.
A few non-code files are listed because a test reads them.
"""
from __future__ import annotations

import sys
from fnmatch import fnmatchcase

WORKFLOWS = [".github/workflows/**", ".github/scripts/**"]  # CI and its helper scripts

RULES: dict[str, list[str]] = {
    "python": [
        "server/**", "cli_agent/**", "scripts/**", "config/**", "examples/**/*.py",
        "pyproject.toml", *WORKFLOWS,
        # Python packages tested by pytest, not named in the issue.
        "ltms_contract/**", "ltms_mcp/**", "conformance/**", "harness/**",
        "examples/*/tests/**",
        "ltms_mind/**", "tests/**", "integrations/**",  # tests/mind, test_hermes_device
        # Non-code files a Python test reads.
        "docs/vocabulary.jsonld",                      # test_vocabulary, test_xapi_translate
        "dashboard/src/lib/swarm-key-vectors.json",    # test_swarm_key_vectors
        "deploy/compose/**",                           # test_setup_env
    ],
    "dashboard": [
        "dashboard/**", *WORKFLOWS,
        "ltms_contract/vectors.json",                  # dashboard/src/lib/swarmKey.test.js
    ],
    "deploy": ["deploy/**", "examples/**/deploy/**", *WORKFLOWS],
}


def _match(path: str, pattern: str) -> bool:
    """Glob match where ``**`` spans directories and ``**/`` may match nothing."""
    if fnmatchcase(path, pattern):  # fnmatch's * already crosses "/"
        return True
    return "**/" in pattern and fnmatchcase(path, pattern.replace("**/", ""))


def classify(paths: list[str]) -> dict[str, bool]:
    paths = [p.strip() for p in paths if p.strip()]
    return {out: any(_match(p, pat) for p in paths for pat in pats) for out, pats in RULES.items()}


def main(argv: list[str]) -> int:
    if "--all" in argv:
        result = {out: True for out in RULES}
    else:
        paths = sys.stdin.read().splitlines()
        result = classify(paths)
        print(f"{len(paths)} changed file(s)", file=sys.stderr)
    for out, value in result.items():
        print(f"{out}={'true' if value else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
