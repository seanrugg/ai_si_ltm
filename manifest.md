# LTMS Project Manifest

Updated: 2026-10-15 · P1–P3 merged to `main`; deployment plan in PR

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

Done and merged: guardrails (CI, contract guard, `AGENTS.md`, `server.manage`), P3 dashboard, `main` protected (squash only, 5 required checks).

1. **Deploy (P6)**: follow `PLESK_DEPLOYMENT_PLAN.md`. Plesk Git pulls `main`; `deploy/plesk-deploy.sh` builds and restarts; one-time `deploy/root-setup.sh` as root.
2. **P4 dashboard pages**: done (#4 by Ornith, #5–#9 in the P4 PR). Settings editing from the dashboard still needs a decision.
3. **Database migrations** before the first schema change on a deployed server.
4. **P5**: Z3-R0 relay on the Pi Zero using `cli_agent/client.py` with a persistent outbox.

## Rules for agents working here

See `AGENTS.md` for the full rules. In short:

* `CONTRACT.md` is frozen. Don't change hashing, canonicalization, prefixes or header fields.
* Models are the schema source: change `server/models`, then `python -m scripts.dump_schema`.
* Run `pytest` before committing; run it against Postgres for anything touching queries or locking.
* No secrets, key files or `.db` files in git.
