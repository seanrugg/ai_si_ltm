"""Admin command line for the server host.

    python -m server.manage set-admin-password [--username admin]
    python -m server.manage list-members [--status pending]
    python -m server.manage approve <name> [--permissions contribute,recall]
    python -m server.manage integrity [<name>]
    python -m server.manage show-config

Swarms (issue #15; the root key lives offline, see scripts/swarm_key.py):
    python -m server.manage swarm-create echo-base --public-key <hex> [--home] [--description TEXT]
    python -m server.manage swarm-list
    python -m server.manage swarm-add <member> --swarm echo-base [--role member|observer]
    python -m server.manage swarm-appoint-hub --swarm echo-base <appointment.json | ->
    python -m server.manage swarm-remove <member>
    python -m server.manage swarm-revoke echo-base --yes

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
from server.services import enrollment, integrity, swarms
from server.services.errors import ServiceError

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
            elif args.cmd.startswith("swarm-"):
                try:
                    code = await _swarm_cmd(session, args)
                except ServiceError as exc:
                    await session.rollback()
                    print(f"error ({exc.code}): {exc.message}", file=sys.stderr)
                    return 1
                if code == 0:
                    await session.commit()
                return code
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


async def _member_named(session, name: str) -> Member:
    m = await session.scalar(select(Member).where(Member.name == name))
    if m is None:
        raise ServiceError(404, "no_member", f"no member named {name!r}")
    return m


async def _swarm_cmd(session, args) -> int:
    if args.cmd == "swarm-create":
        swarm = await swarms.create_swarm(session, name=args.name, public_key_hex=args.public_key,
                                          description=args.description, is_home=args.home)
        print(f"created swarm {swarm.name!r}  id={swarm.id}{'  (home)' if swarm.is_home else ''}")
    elif args.cmd == "swarm-list":
        print(json.dumps(await swarms.describe(session), indent=2))
    elif args.cmd == "swarm-add":
        swarm = await swarms.get_swarm(session, args.swarm)
        row = await swarms.set_member(session, await _member_named(session, args.member), swarm, args.role)
        print(f"{args.member} is now {row.role} of {swarm.name}")
    elif args.cmd == "swarm-appoint-hub":
        swarm = await swarms.get_swarm(session, args.swarm)
        raw = sys.stdin.read() if args.appointment == "-" else open(args.appointment).read()
        try:
            appointment = json.loads(raw)
        except ValueError:
            raise ServiceError(422, "bad_appointment", "the appointment isn't valid JSON") from None
        await swarms.appoint_hub(session, swarm, appointment)
        print(f"{appointment['statement']['hub']} is now the hub of {swarm.name} (relay permission granted)")
    elif args.cmd == "swarm-remove":
        left = await swarms.remove_member(session, await _member_named(session, args.member))
        print(f"{args.member} is now a free agent" if left else f"{args.member} wasn't in a swarm")
    elif args.cmd == "swarm-revoke":
        if not args.yes:
            print("revoking a swarm releases every member and stops its hub relaying; add --yes", file=sys.stderr)
            return 1
        n = await swarms.revoke_swarm(session, await swarms.get_swarm(session, args.name))
        print(f"revoked {args.name}; {n} member(s) are now free agents")
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
    sc = sub.add_parser("swarm-create", help="register a swarm by its root public key")
    sc.add_argument("name")
    sc.add_argument("--public-key", required=True, help="64 hex chars, printed by scripts/swarm_key.py create")
    sc.add_argument("--description")
    sc.add_argument("--home", action="store_true", help="this server's home swarm")
    sub.add_parser("swarm-list")
    sa = sub.add_parser("swarm-add", help="put a member in a swarm (moves it if it's elsewhere)")
    sa.add_argument("member")
    sa.add_argument("--swarm", required=True)
    sa.add_argument("--role", choices=list(swarms.MEMBER_ROLES), default="member")
    sh = sub.add_parser("swarm-appoint-hub", help="apply a hub appointment signed with the root key")
    sh.add_argument("--swarm", required=True)
    sh.add_argument("appointment", help="file from scripts/swarm_key.py appoint-hub, or - for stdin")
    sr = sub.add_parser("swarm-remove", help="make a member a free agent")
    sr.add_argument("member")
    sv = sub.add_parser("swarm-revoke")
    sv.add_argument("name")
    sv.add_argument("--yes", action="store_true")
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
