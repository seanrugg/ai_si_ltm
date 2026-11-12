"""Run the LTMS harness conformance cases against a live test instance (#298).

    python conformance/run.py --server https://ltms-test.example.org --key member.json --state-dir /tmp/conf
    python conformance/run.py ... --adapter mypackage.myadapter:MyHarness

The member in --key must be enrolled and approved with `contribute` and `recall`
on a **test** instance: the cases write records. Exit status 1 if any case fails.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cases import run  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--server", required=True)
    p.add_argument("--key", required=True, help="the member's key file (the reference adapter reads it)")
    p.add_argument("--state-dir", required=True, help="where the harness under test keeps its files")
    p.add_argument("--adapter", default="adapter:CliAgentAdapter", help="module:Class implementing adapter.py")
    a = p.parse_args(argv)
    mod, _, cls = a.adapter.partition(":")
    harness = getattr(importlib.import_module(mod), cls)(a.server, a.key, a.state_dir)
    results = run(harness)
    for name, ok, reason in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  ' + reason if reason else ''}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
