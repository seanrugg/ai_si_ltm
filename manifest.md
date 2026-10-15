# LTMS Project Manifest

Date: 2026-10-13
Status: **Work in progress — NOT commit/push-ready.** The scaffold is incomplete
and does not currently import. See "Blockers" below.

This is a development environment. Nothing here is going to production.

---

## What this repo is

The Long-Term Memory Server (LTMS): an append-only, tamper-evident record store
for AI/robot mesh members. Design source of truth:
`LTMS_ARCHITECTURE_PLAN.md` (version 1.0, 12 Oct 2026).

Contract details (frozen at P1):
- Record hash: `body_hash = SHA-256(canonical_bytes(payload))`, RFC 8785 / JCS
  canonicalization (keys sorted alphabetically, minimal whitespace, no floats in
  hashed fields).
- Separation into a permanent CHAIN-HEADER `records` table + a purgable
  `recordpayload` table.
- Signature domain separation:
  - record: `ltms-record-v1\n+ 64-hex(body_hash)`
  - auth nonce: `ltms-auth-v1\n+ 32-hex(nonce)`
- Chain link: `hash = SHA-256(sig_raw_bytes)`, chain `prev_hash -> hash`.
- Genesis `prev_hash` is a 32-byte zero vector.

---

## What has been done

### Present on disk (not yet committed — working tree is dirty)
- `config/default.yaml`, `config/schema.sql` — schema + defaults.
- `server/security/__init__.py`, `server/security/key.py`, `server/security/token.py`,
  `server/security/hash/__init__.py` — crypto layer (Ed25519 + JCS hashing).
- `server/models/*.py` — 9 ORM model files (`Base` declared in `server/models/base.py`).
- `server/config.py` — pydantic-settings, env-driven, YAML base.
- `server/db.py` — async SQLAlchemy engine/session + session_scope helper.
- `server/services/__init__.py` — docstring only; **no service logic yet**.

### Verified working
- **Crypto layer (key.py / hash.py / token.py)** — the record/nonce signing and
  verification paths have been exercised end-to-end in `/tmp` against an in-memory
  engine: signatures verify, domain separation between record/nonce signatures
  holds, and chain links compute correctly. This layer is the reliable part of the
  scaffold (once the blocker below is fixed, see "Blockers").

## What needs to be done

### Blockers — doing before anything else
1. **`server.models` package does not import.** Every model file references
   `Base` (`class Session(Base)`, etc.) but none of the model files import it
   (`from server.models.base import Base`). This is a real `NameError`; it is the
   only thing keeping the model tree from importing at all. Fix: add the `Base`
   import to each of `member.py`, `record.py`, `recordpayload.py`, `keyframe.py`,
   `adminsession.py`, `adminuser.py`, `audit.py`, `token.py`, in the import block.
2. **No tested ingest chain.** The plan's hard requirement is: *unit-test the
   ingest chain + crypto against an in-memory DB before touching git/push.* That
   chain logic (`services/`) does not exist yet — only `services/__init__.py`.
   The verification pass in this session must exercise chain verification
   (`body_hash` + record signature + `prev_hash -> hash`).

### To build before commit/push (P1 scaffold)
3. `server/services/` — the ingest chain: recompute `body_hash`, verify the Ed25519
   signature, enforce `prev_hash -> hash` and monotonic `seq`, reject tampered
   records. Plus token/nonce auth helpers using `hashed tokens`.
4. `server/api/` (or whatever the plan specifies) — HTTP routes, request/response
   schemas, and the FastAPI `create_app` factory in `server/main.py`.
5. `.gitignore` already exists and is untracked — confirm it covers the `.venv`
   and `.pytest_cache`.
6. `manifest.md` (this file).

### Verification gating the commit (must all pass first)
7. `server.models` imports cleanly: `import server.models`.
8. `import server.db` and `create_all()` builds every table in an in-memory
   SQLite engine.
9. Crypto + ingest-chain unit tests pass green in the ephemeral `/tmp` venv.
10. **Git hygiene before push:** `git rev-parse --short HEAD` shows no new commit,
    `git status --porcelain` only contains the intended new files
    (`config/`, `server/db.py`, `server/services/*`, `server/api/*`,
    `server/main.py`, tests, `manifest.md`), and **no `.venv` inside the repo tree**.

---

## Key facts / environment notes
- Python 3.14.
- Deps installed in ephemeral `/tmp` venv (`/private/tmp/vm`). This venv lives
  **outside** the repo so it stays clean of `.venv`.
- Deps pulled so far include `pynacl` (verified working). Confirm `asyncpg`,
  `pydantic`, `pydantic-settings`, `SQLAlchemy` (async variant), `PyYAML`, and any
  other plan imports are present in the venv before building `services/` and
  `api/`.

---

## Honest end-of-session note
I was **not** at commit/push-ready when this session ends. The blocker list above
is accurate and non-negotiable: the models don't currently import, and the ingest
chain (the core of the scaffold) doesn't exist yet. Do not push at this state.
