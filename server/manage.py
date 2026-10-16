"""Admin command line for the server host.

    python -m server.manage set-admin-password [--username admin]
    python -m server.manage list-members [--status pending]
    python -m server.manage approve <name> [--permissions contribute,recall]
    python -m server.manage integrity [<name>]
    python -m server.manage show-config

Uses the same LTMS_* environment as the server.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from sqlalchemy import select

from server.config import load_config
from server.db import create_all, make_engine, make_sessionmaker
from server.models import AdminSession, AdminUser, Member
from server.security.passwords import hash_password
from server.services import enrollment, integrity

MIN_PASSWORD = 12


def _read_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New admin password: ")
    if first != getpass.getpass("Repeat: "):
        raise SystemExit("passwords do not match")
    return first


async def _run(args, cfg) -> int:
    engine = make_engine(cfg.resolved_database_url)
    try:
        await create_all(engine)
        sm = make_sessionmaker(engine)
        async with sm() as session:
            if args.cmd == "set-admin-password":
                password = _read_password(args)
                if len(password) < MIN_PASSWORD:
                    print(f"password must be at least {MIN_PASSWORD} characters", file=sys.stderr)
                    return 1
                admin = await session.scalar(select(AdminUser).where(AdminUser.username == args.username))
                if admin is None:
                    admin = AdminUser(username=args.username, password_hash=hash_password(password))
                    session.add(admin)
                    msg = "created"
                else:
                    admin.password_hash = hash_password(password)
                    # Log out every existing session for this admin.
                    for s in await session.scalars(select(AdminSession).where(AdminSession.admin_id == admin.id)):
                        await session.delete(s)
                    msg = "updated (existing sessions logged out)"
                await session.commit()
                print(f"admin {args.username!r} {msg}")
            elif args.cmd == "list-members":
                stmt = select(Member).order_by(Member.created_at)
                if args.status:
                    stmt = stmt.where(Member.status == args.status)
                for m in await session.scalars(stmt):
                    print(f"{m.name:24} {m.kind:9} {m.status:8} seq={m.last_ack_seq:<6} "
                          f"perms={','.join(m.permissions) or '-'}  id={m.id}")
            elif args.cmd == "approve":
                m = await session.scalar(select(Member).where(Member.name == args.name))
                if m is None:
                    print(f"no member named {args.name!r}", file=sys.stderr)
                    return 1
                perms = args.permissions.split(",") if args.permissions else None
                await enrollment.approve(session, m.id, admin="cli", permissions=perms)
                await session.commit()
                print(f"approved {m.name} with {m.permissions}")
            elif args.cmd == "integrity":
                if args.name:
                    m = await session.scalar(select(Member).where(Member.name == args.name))
                    if m is None:
                        print(f"no member named {args.name!r}", file=sys.stderr)
                        return 1
                    reports = [await integrity.verify_member(session, m)]
                else:
                    reports = await integrity.verify_all(session)
                print(json.dumps(reports, indent=2))
                return 0 if all(r["status"] in ("ok", "empty") for r in reports) else 2
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None, *, config=None) -> int:
    p = argparse.ArgumentParser(prog="server.manage")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set-admin-password")
    s.add_argument("--username", default=None)
    s.add_argument("--password-stdin", action="store_true", help="read the password from stdin (for scripts)")
    lm = sub.add_parser("list-members")
    lm.add_argument("--status", choices=["pending", "active", "revoked", "rejected"])
    ap = sub.add_parser("approve")
    ap.add_argument("name")
    ap.add_argument("--permissions", help="comma list; default = what the member requested")
    ig = sub.add_parser("integrity")
    ig.add_argument("name", nargs="?")
    sub.add_parser("show-config")
    args = p.parse_args(argv)

    cfg = config or load_config()
    if args.cmd == "show-config":
        hidden = {"db_password", "admin_password", "database_url"}
        print(json.dumps({k: v for k, v in json.loads(cfg.model_dump_json()).items() if k not in hidden}, indent=2))
        return 0
    if args.cmd == "set-admin-password" and args.username is None:
        args.username = cfg.admin_username
    return asyncio.run(_run(args, cfg))


if __name__ == "__main__":
    raise SystemExit(main())
