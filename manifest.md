# LTMS Project Manifest

Updated: 2026-10-15 · P1/P2 merged to `main` (PR #1)

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

1. **Guardrails** (PR #2): CI on every PR (SQLite + Postgres, Python 3.11/3.13/3.14), contract guard, `AGENTS.md`, admin CLI `python -m server.manage`.
2. **P3 dashboard** (PR #3): Login, Timeline, Members (approve/reject/revoke/permissions), Integrity, record page. P4 pages are agent-task issues.
3. `PLESK_DEPLOYMENT_PLAN.md` — systemd unit (1 worker, `--proxy-headers`), nginx, Postgres role, backups.
4. P5 relay on the Pi Zero using `cli_agent/client.py` with a persistent outbox.

## Rules for agents working here

See `AGENTS.md` for the full rules. In short:

* `CONTRACT.md` is frozen. Don't change hashing, canonicalization, prefixes or header fields.
* Models are the schema source: change `server/models`, then `python -m scripts.dump_schema`.
* Run `pytest` before committing; run it against Postgres for anything touching queries or locking.
* No secrets, key files or `.db` files in git.
