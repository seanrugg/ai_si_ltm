"""The swarm's root key. Run this on YOUR machine (the Mac), never on the server.

The root key is the swarm's identity: its public key's fingerprint is the swarm
ID. It is used for one thing today: appointing the hub. Keep the key file
offline (a USB stick or your password manager), and keep a second copy. If it's
lost, the swarm can't appoint a new hub; you'd create a new swarm.

    # once: create the key, then register the swarm on the server
    python -m scripts.swarm_key create echo-base --out /Volumes/KEY/echo-base.swarm.json

    # whenever the hub changes: sign an appointment for the hub's member key
    python -m scripts.swarm_key appoint-hub --key /Volumes/KEY/echo-base.swarm.json \\
        --hub jetson-hub --hub-key <64 hex from the dashboard or `manage list-members`> \\
        > appoint-jetson-hub.json

Needs only ``ltms_contract`` and ``pynacl``; no server or database access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from ltms_contract import canonicalize, generate_key, load_key, now_ts, public_key_hex, save_key

SWARM_PREFIX = b"ltms-swarm-v1\n"  # must match server/services/swarms.py


def swarm_id(public_key_hex_: str) -> str:
    return hashlib.sha256(bytes.fromhex(public_key_hex_)).hexdigest()


def appointment(key, swarm: str, hub: str, hub_key: str, ts: str | None = None) -> dict:
    statement = {"v": 1, "action": "appoint_hub", "swarm": swarm, "hub": hub,
                 "hub_key": hub_key.lower(), "ts": ts or now_ts()}
    return {"statement": statement, "sig": key.sign(SWARM_PREFIX + canonicalize(statement)).signature.hex()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="make a new swarm root key")
    c.add_argument("name")
    c.add_argument("--out", required=True, help="where to write the key file (mode 600)")
    h = sub.add_parser("appoint-hub", help="sign a hub appointment (prints JSON)")
    h.add_argument("--key", required=True)
    h.add_argument("--hub", required=True, help="the hub's member name, e.g. jetson-hub")
    h.add_argument("--hub-key", required=True, help="the hub's member public key, 64 hex")
    i = sub.add_parser("show", help="print a key file's swarm name, public key and ID")
    i.add_argument("--key", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "create":
        out = Path(args.out)
        if out.exists():
            print(f"{out} already exists; refusing to overwrite a root key", file=sys.stderr)
            return 1
        key = generate_key()
        save_key(out, args.name, key)
        pub = public_key_hex(key)
        print(f"swarm      {args.name}\npublic key {pub}\nswarm id   {swarm_id(pub)}\n")
        print("Keep this file offline and make a backup copy. Then, on the server:")
        print(f"  manage.sh swarm-create {args.name} --public-key {pub} --home")
    elif args.cmd == "appoint-hub":
        name, key = load_key(args.key)
        if len(args.hub_key) != 64:
            print("--hub-key must be 64 hex characters", file=sys.stderr)
            return 1
        print(json.dumps(appointment(key, swarm_id(public_key_hex(key)), args.hub, args.hub_key), indent=2))
        print(f"signed for swarm {name!r}; apply on the server with: "
              f"manage.sh swarm-appoint-hub --swarm {name} <this file>", file=sys.stderr)
    elif args.cmd == "show":
        name, key = load_key(args.key)
        pub = public_key_hex(key)
        print(f"swarm      {name}\npublic key {pub}\nswarm id   {swarm_id(pub)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
