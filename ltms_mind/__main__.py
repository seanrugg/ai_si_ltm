"""``python -m ltms_mind run --once``  or  ``run --every 4h --jitter 30m``.

Settings come from the environment, or ``--env FILE`` (KEY=VALUE lines). See docs/mind.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    from ltms_mind import config

    ap = argparse.ArgumentParser(prog="python -m ltms_mind", description="The member mind (#338).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="wake once, or on a schedule")
    when = run.add_mutually_exclusive_group(required=True)
    when.add_argument("--once", action="store_true", help="one wake, then exit")
    when.add_argument("--every", help="wake on a schedule, e.g. 4h")
    run.add_argument("--jitter", default="0", help="random extra wait per wake, e.g. 30m")
    run.add_argument("--env", help="settings file (KEY=VALUE lines); the environment wins")
    run.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = {**(config.read_env_file(args.env) if args.env else {}), **os.environ}
    try:
        cfg = config.load(env)
        every = config.parse_duration(args.every) if args.every else None
        jitter = config.parse_duration(args.jitter)
    except config.ConfigError as exc:
        print(f"ltms_mind: {exc}", file=sys.stderr)
        return 2

    from cli_agent.client import LTMSClient
    from ltms_mind import brains
    from ltms_mind.gate import make_embedder
    from ltms_mind.loop import Mind, run_forever

    if not os.path.exists(cfg.key_path):
        print(f"ltms_mind: no key file at {cfg.key_path} (LTMS_KEY_PATH); enroll the member first", file=sys.stderr)
        return 2
    try:
        brain = brains.make(cfg.brain, cfg)
    except ValueError as exc:
        print(f"ltms_mind: {exc}", file=sys.stderr)
        return 2
    embedder = make_embedder(cfg.embedder)

    def factory() -> Mind:
        client = LTMSClient(cfg.server, key_path=cfg.key_path, state_path=cfg.state_path + ".generation")
        return Mind(cfg, client, brain, embedder=embedder)

    if args.once:
        print(json.dumps(factory().wake(), default=str))
        return 0
    run_forever(factory, every, jitter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
