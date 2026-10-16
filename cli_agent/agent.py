"""CLI test member: exercises the whole pipeline before Z3-R0 exists.

    python -m cli_agent genkey  --name test-agent-1 --out keys/test-agent-1.json
    python -m cli_agent enroll  --key keys/test-agent-1.json --server http://localhost:8080 \
                                --kind agent --permissions contribute,recall
    (approve it in the dashboard, or: curl ... /api/admin/members/<id>/approve)
    python -m cli_agent token   --key keys/test-agent-1.json --server http://localhost:8080
    python -m cli_agent send    --key keys/test-agent-1.json --server http://localhost:8080 --count 20
    python -m cli_agent verify  --key keys/test-agent-1.json --server http://localhost:8080
    python -m cli_agent recall  --key keys/test-agent-1.json --server http://localhost:8080 \
                                --intent approach --target vector --place living-room
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from cli_agent.client import LTMSClient, LTMSError
from ltms_contract import (
    GENESIS_HASH,
    ContractError,
    generate_key,
    load_key,
    public_key_hex,
    save_key,
    verify_record,
)

PLACES = ["living-room", "kitchen", "hallway"]
OUTCOMES = ["success"] * 5 + ["partial", "reflex_stop", "reflex_stop", "stuck", "abandoned"]


def scenario(client: LTMSClient, count: int, rng: random.Random) -> None:
    """Queue ``count`` records shaped like the design doc: a communication, a
    decision that cites it, an action that cites the decision and carries an
    outcome; occasionally a perception, event or lesson."""
    made = 0
    while made < count:
        place = rng.choice(PLACES)
        comm = client.append(
            "communication", summary="Vector chirped hello", place=place, peers=["vector"], tags=["social"],
            payload={"direction": "in", "channel": "audio", "text": "beep boop", "confidence": round(rng.uniform(0.6, 0.99), 2)},
        )
        made += 1
        if made >= count:
            break
        dec = client.append(
            "decision", summary="Approach Vector", place=place, peers=["vector"],
            refs={"input": f"{client.name}:{comm['seq']}"},
            payload={"options": ["approach", "wait", "ignore"], "chosen": "approach",
                     "reason": "Vector greeted; distance under 1.5 m"},
        )
        made += 1
        if made >= count:
            break
        outcome = rng.choice(OUTCOMES)
        detail = {"reflex_stop": "front range 0.25 m", "stuck": "left wheel stalled"}.get(outcome)
        client.append(
            "action", summary="Approach Vector", place=place, peers=["vector"], tags=["social", "drive"],
            refs={"decision": f"{client.name}:{dec['seq']}"}, outcome=outcome,
            payload={"distance_m": round(rng.uniform(0.3, 1.4), 2), "speed_mps": 0.15,
                     **({"outcome_detail": detail} if detail else {})},
        )
        made += 1
        if made < count and rng.random() < 0.3:
            client.append("perception", summary="Floor edge detected", place=place,
                          payload={"sensor": "cliff", "value": rng.randint(200, 900)})
            made += 1
        if made < count and rng.random() < 0.15:
            client.append("lesson", summary="Slow down near Vector on the hallway rug", place="hallway",
                          peers=["vector"], payload={"basis": "repeated reflex stops", "suggested_speed_mps": 0.1})
            made += 1


def verify_chain(records: list[dict], public_key: bytes) -> tuple[bool, str]:
    records = sorted(records, key=lambda r: r["seq"])
    prev, expected = GENESIS_HASH, 1
    for rec in records:
        if rec["seq"] != expected:
            return False, f"gap: expected seq {expected}, got {rec['seq']}"
        try:
            verify_record(rec, public_key, require_payload=False)
        except ContractError as exc:
            return False, f"seq {rec['seq']}: {exc.code}: {exc.message}"
        if rec["prev_hash"] != prev:
            return False, f"seq {rec['seq']}: prev_hash does not link"
        prev, expected = rec["hash"], expected + 1
    return True, f"{len(records)} records, chain intact, head {prev[:16]}…"


def fetch_all(client: LTMSClient) -> list[dict]:
    out, offset = [], 0
    while True:
        page = client.records(agent=client.name, limit=200, offset=offset)
        out.extend(page)
        if len(page) < 200:
            return out
        offset += 200


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cli_agent", description="LTMS CLI test member")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("genkey", help="create an Ed25519 key file")
    g.add_argument("--name", required=True)
    g.add_argument("--out", required=True)

    def common(sp):
        sp.add_argument("--key", required=True)
        sp.add_argument("--server", required=True)

    e = sub.add_parser("enroll", help="request enrollment")
    common(e)
    e.add_argument("--kind", default="agent", choices=["robot", "agent", "assistant", "relay"])
    e.add_argument("--permissions", default="contribute,recall")
    e.add_argument("--operator")

    t = sub.add_parser("token", help="log in and print the token")
    common(t)
    t.add_argument("--out")

    s = sub.add_parser("send", help="send realistic records")
    common(s)
    s.add_argument("--count", type=int, default=20)
    s.add_argument("--seed", type=int)

    v = sub.add_parser("verify", help="fetch own records and verify the chain locally")
    common(v)

    r = sub.add_parser("recall", help="fetch a context pack")
    common(r)
    r.add_argument("--intent")
    r.add_argument("--target")
    r.add_argument("--place")

    h = sub.add_parser("head", help="show the server's view of this member's chain head")
    common(h)
    return p


def main(argv: list[str] | None = None, http=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "genkey":
        if Path(args.out).exists():
            print(f"refusing to overwrite {args.out}", file=sys.stderr)
            return 1
        key = generate_key()
        save_key(args.out, args.name, key)
        print(json.dumps({"name": args.name, "public_key": public_key_hex(key), "key_file": args.out}, indent=2))
        return 0

    client = LTMSClient(args.server, key_path=args.key, http=http)
    try:
        if args.cmd == "enroll":
            res = client.enroll(args.kind, [p for p in args.permissions.split(",") if p], operator=args.operator)
            print(json.dumps(res, indent=2))
            print("Pending: approve this member in the dashboard (Members → Pending).", file=sys.stderr)
        elif args.cmd == "token":
            tok = client.login()
            if args.out:
                Path(args.out).write_text(tok["token"])
            print(json.dumps({k: v for k, v in tok.items() if k != "token" or not args.out}, indent=2))
        elif args.cmd == "send":
            client.sync_head()
            start = client.seq
            scenario(client, args.count, random.Random(args.seed))
            client.flush()
            print(f"sent seq {start + 1}..{client.seq}; head {client.last_hash[:16]}…")
        elif args.cmd == "verify":
            _, key = load_key(args.key)
            ok, msg = verify_chain(fetch_all(client), bytes(key.verify_key))
            print(("OK  " if ok else "FAIL ") + msg)
            return 0 if ok else 2
        elif args.cmd == "recall":
            pack = client.context(args.intent, args.target, args.place)
            summary = {k: [f"{r['agent']}:{r['seq']} {r['type']} {r.get('summary', '')} {r.get('outcome', '')}".strip()
                           for r in pack[k]] for k in ("outcomes", "comms", "lessons", "policies")}
            print(json.dumps({"count": pack["count"], **summary}, indent=2))
        elif args.cmd == "head":
            print(json.dumps(client.sync_head(), indent=2))
    except LTMSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
