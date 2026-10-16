"""Fill a DEVELOPMENT server with realistic demo data for dashboard work.

    python -m scripts.seed_demo --server http://localhost:8080 --admin-password dev-password-123

Creates z3-r0 (robot), vector (robot), an operator member allowed to write
policy, a scoped cloud agent, and one member left pending. Keys are written to
keys/demo/ (git-ignored). Never point this at production.
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from cli_agent.agent import scenario
from cli_agent.client import LTMSClient
from ltms_contract import generate_key, load_key, save_key

KEYS = Path("keys/demo")


def client(server: str, name: str) -> LTMSClient:
    path = KEYS / f"{name}.json"
    if not path.exists():
        save_key(path, name, generate_key())
    return LTMSClient(server, key_path=str(path))


def ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8080")
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-password", required=True)
    args = ap.parse_args()

    admin = httpx.Client(base_url=args.server, headers={"x-ltms-admin": "1"})
    admin.post("/api/auth/admin/login", json={"username": args.admin_user, "password": args.admin_password}).raise_for_status()

    plan = {
        "z3-r0": ("robot", ["contribute", "recall"], None, "Sean"),
        "vector": ("robot", ["contribute", "recall"], None, "Sean"),
        "operator": ("assistant", ["contribute", "recall", "policy"], None, "Sean"),
        "cloud-tutor": ("agent", ["recall:scoped"], {"types": ["lesson", "policy"]}, "Claude"),
        "jetson-hub": ("relay", ["contribute", "recall"], None, "Sean"),
    }
    members = {}
    for name, (kind, perms, scope, operator) in plan.items():
        c = client(args.server, name)
        r = c.http.post("/api/auth/enroll", json={"name": name, "kind": kind, "public_key": bytes(c.key.verify_key).hex(),
                                                 "permissions": perms, "operator": operator})
        if r.status_code == 202 and name != "jetson-hub":  # leave one pending
            mid = r.json()["id"]
            admin.post(f"/api/admin/members/{mid}/approve", json={"scope": scope}).raise_for_status()
        members[name] = c

    rng = random.Random(42)
    z3, vec, op = members["z3-r0"], members["vector"], members["operator"]
    for c in (z3, vec, op):
        c.sync_head()

    op.append("policy", ts=ts(600), summary="No driving after 9 pm", payload={"rule": "quiet hours 21:00-07:00"})
    op.append("policy", ts=ts(590), summary="Keep 30 cm from the stairs", place="hallway", payload={"min_distance_m": 0.3})
    op.flush()

    for i in range(6):
        when = 300 - i * 45
        v = vec.append("communication", ts=ts(when), summary="Hello Z3", peers=["z3-r0"],
                       payload={"channel": "audio", "text": "beep boop"})
        vec.flush()
        z3.append("communication", ts=ts(when - 1), summary="Heard Vector", peers=["vector"],
                  refs={"input": f"vector:{v['seq']}"}, place="living-room", payload={"confidence": 0.91})
        scenario(z3, 4, rng)
        z3.flush()
    z3.append("lesson", ts=ts(5), summary="Approach Vector at 0.1 m/s on the rug", place="living-room",
              peers=["vector"], payload={"basis": "3 reflex stops in 6 approaches"})
    z3.flush()
    print("seeded:", {n: c.seq for n, c in members.items() if c.seq})


if __name__ == "__main__":
    main()
