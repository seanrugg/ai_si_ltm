# LTMS Project Manifest

Updated: 2026-10-15 · Branch: `claude/p1-core-rebuild`

## Status

**P1 and P2 complete.** 80 tests pass on SQLite and on PostgreSQL 16. The CLI
agent enrolls, logs in, sends 20 records and verifies its chain against a real
uvicorn + Postgres server.

The earlier scaffold (commit `5bee1a5`) was replaced rather than patched: its
write path never ran (un-awaited calls), its batches weren't atomic, its
`body_hash` covered the payload (so purging would have broken verification),
it rejected any record older than 45 s, and `schema.sql` didn't load. The design
decisions it recorded (header/payload split, JCS, domain separation, challenge
endpoint, separate admin sessions) are kept and now implemented.

## Next

1. **P3 dashboard** — Login, Timeline, Members (approve/reject/revoke), Integrity. API is ready.
2. Admin password change command (`python -m server.manage set-admin-password`).
3. `PLESK_DEPLOYMENT_PLAN.md` — systemd unit (1 worker, `--proxy-headers`), nginx, Postgres role, backups.
4. P5 relay on the Pi Zero using `cli_agent/client.py` with a persistent outbox.

## Rules for agents working here

* `CONTRACT.md` is frozen. Don't change hashing, canonicalization, prefixes or header fields.
* Models are the schema source: change `server/models`, then `python -m scripts.dump_schema`.
* Run `pytest` before committing; run it against Postgres for anything touching queries or locking.
* No secrets, key files or `.db` files in git.
