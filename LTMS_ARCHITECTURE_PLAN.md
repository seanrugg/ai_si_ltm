# LTMS Architecture Plan

**Long-Term Memory Server — repo architecture and implementation plan**
Version 1.1 · 15 Oct 2026 · Owner: Sean
Reference: `Robot-Mesh-and-Long-Term-Memory-Plan.md` (primary design doc) · `CONTRACT.md` (record contract, frozen)

**Changes from 1.0:** header/payload split so retention never breaks the chain;
RFC 8785 canonical JSON with stored canonical bytes; domain-separated signatures;
challenge endpoint; admin sessions in their own table; valid-prefix batch policy
with idempotent retries; row lock on ingest; `outcome` promoted to a header
field; `policy` records gated by a permission; vector column deferred to P8;
`pyproject.toml` moved to the repo root.

---

## Scope

This repo is the LTMS: an append-only, tamper-evident record store for every AI/robot
member of the mesh, plus the admin dashboard. Z3-R0 is the first enrolled member and
serves as the reference example. Other members (Jetson Orin, Anki Vector, future
UNO Q robots, cloud LLM agents) use the same API.

Deploying to: Plesk VPS (`[ltms-host].cucorn.com`), PostgreSQL on localhost,
gunicorn + UvicornWorker (**one worker**) as a systemd service; React dashboard as static files.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Record contract | `ltms_contract/` — RFC 8785 JCS + SHA-256 + Ed25519 (`pynacl`); no other deps |
| Backend API | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, asyncpg |
| Auth (members) | Ed25519 challenge → opaque bearer token (5 min), stored as SHA-256 |
| Auth (admin) | bcrypt password; 30-min httpOnly, SameSite=Strict cookie (stored as SHA-256); `X-LTMS-Admin: 1` header required on writes (CSRF) |
| Database | PostgreSQL 15+ (prod), SQLite (tests only). `pgvector` arrives in P8 |
| Dashboard | React 18, Vite 5, React Router, Tailwind CSS 3 |
| Scheduling | APScheduler 3 (in-process) |
| Deployment | gunicorn + systemd (backend); Plesk static files (dashboard) |

---

## Repo Structure

```
ai_si_ltm/
├── README.md
├── AGENTS.md                        ← rules + workflow for coding agents
├── CONTRACT.md                      ← byte-level record contract (frozen, v1)
├── LTMS_ARCHITECTURE_PLAN.md        ← this file
├── PLESK_DEPLOYMENT_PLAN.md         ← written before deployment (P6)
├── pyproject.toml                   # one package: ltms_contract, server, cli_agent
│
├── ltms_contract/                   # shared by server, CLI agent, robots
│   ├── canonical.py                 # RFC 8785 JCS
│   ├── record.py                    # field rules, hash, sign, verify
│   ├── keys.py                      # key files (0600)
│   └── vectors.json                 # cross-language test vectors
│
├── config/
│   ├── schema.sql                   # GENERATED from models (scripts/dump_schema.py)
│   └── default.yaml                 # defaults; LTMS_* env vars override
│
├── scripts/
│   ├── dump_schema.py               # regenerate config/schema.sql
│   └── make_vectors.py              # regenerate vectors.json (version bumps only)
│
├── server/
│   ├── main.py                      # create_app(config) factory, lifespan, middleware
│   ├── config.py                    # ServerConfig (YAML < env < overrides)
│   ├── db.py                        # engine / sessionmaker (kept on app.state)
│   ├── tasks.py                     # APScheduler jobs
│   ├── manage.py                    # admin CLI: set-admin-password, list-members, approve, integrity
│   ├── models/                      # member, record (+payload, triggers), auth, audit
│   ├── security/                    # tokens, passwords, rate limiter
│   ├── services/                    # ingest, recall, enrollment, auth, integrity, retention
│   ├── api/                         # public.py, member.py, admin.py, deps.py, errors.py, schemas.py
│   └── tests/                       # 80 tests; SQLite by default, Postgres via env
│
├── cli_agent/                       # reference member client + CLI test agent
│   ├── client.py                    # LTMSClient: enroll, login, append (offline), flush, recall
│   └── agent.py                     # genkey | enroll | token | send | verify | recall | head
│
└── dashboard/                       # P3 — React admin UI
```

---

## Record contract

See `CONTRACT.md`. Summary:

```
payload_hash = SHA-256(JCS(payload))                       # in the header
hash         = SHA-256(JCS(header))                        # header = all but payload/hash/sig
sig          = Ed25519(sk, "ltms-record-v1\n" || hash)     # 32 raw bytes after the prefix
login sig    = Ed25519(sk, "ltms-auth-v1\n"   || nonce)
```

The server stores the exact canonical header and payload bytes, so integrity never
depends on how Postgres round-trips JSONB or timestamps, and retention can delete
payloads without breaking a single link.

**Server verify order:** agent → structure → payload_hash → hash → sig → type allowed
→ ts not > 5 min in the future → duplicate/conflict → seq = head + 1 → prev_hash = head hash.
Batches keep the valid prefix; retries of already-stored records return `duplicate`.
The member row is locked (`SELECT … FOR UPDATE`) for the whole batch.

---

## Database Schema (summary — `config/schema.sql` is generated from `server/models`)

| Table | Purpose | Notes |
|---|---|---|
| `members` | enrolled/pending members | `permissions` (granted) vs `requested_permissions`; `scope` for `recall:scoped`; chain head `last_ack_seq` + `last_hash`; status `pending/active/revoked/rejected` |
| `records` | chain headers | **append-only** (row triggers on UPDATE/DELETE, statement trigger on TRUNCATE); `header_raw` = signed bytes; parsed columns for queries incl. `outcome`; unique `(member_id, seq)` and `hash` |
| `record_payloads` | one per record | `payload_raw` (signed bytes) + `body` JSONB; purgeable |
| `auth_challenges` | login nonces | single use, 60 s; in DB so multiple workers would work |
| `tokens` | member bearer tokens | SHA-256 only; permission snapshot; revoked on permission change/revoke |
| `admin_users`, `admin_sessions` | admin login | session stored as SHA-256 |
| `audit_log` | recall, logins, enrollment decisions, ingest rejects, purges | |

Permissions: `contribute`, `recall`, `recall:scoped` (scope `{types, agents, places}`;
types must be listed explicitly), `policy` (may write `policy` records).

---

## API Surface

### Public (rate-limited per IP)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/enroll` | Request enrollment (202; admin decides) — 5/h per IP, ≤ 20 pending |
| `POST` | `/api/auth/challenge` | Get a login nonce |
| `POST` | `/api/auth/token` | Signed nonce → bearer token |
| `GET` | `/api/health` | Liveness (no data) |

No OpenAPI/docs pages are served.

### Member (`Authorization: Bearer <token>`)

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `POST` | `/api/records` | `contribute` | Ingest a batch (≤ 100) |
| `GET` | `/api/records/head` | any | Own chain head (resume after restart) |
| `GET` | `/api/records` | `recall` / `recall:scoped` | Filters: `type agent place peer tag outcome from to summary include_payload limit offset` |
| `GET` | `/api/recall/context` | `recall` / `recall:scoped` | `intent target place` → outcomes (10), comms 24 h (5), lessons (5), policies (5); cap 25 |

### Admin (cookie `ltms_admin`; writes need `X-LTMS-Admin: 1`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/admin/login` · `/logout` | Session |
| `GET` | `/api/admin/me` | Who am I |
| `GET` | `/api/admin/members[?status=]` · `/members/pending` | Members |
| `POST` | `/api/admin/members/{id}/approve` | Body `{permissions?, scope?}`; defaults to what was requested |
| `POST` | `/api/admin/members/{id}/reject` · `/revoke` | Body `{reason?}`; revoke keeps records, kills tokens |
| `PATCH` | `/api/admin/members/{id}/permissions` | Body `{permissions, scope?}`; kills outstanding tokens |
| `GET` | `/api/admin/timeline` | All records, newest first, same filters as recall + `q` |
| `GET` | `/api/admin/records/{agent}/{seq}` | One record as signed |
| `GET` | `/api/admin/conversation?member_a=&member_b=` | Communications between two members |
| `GET` | `/api/admin/decision-trace?agent=&seq=` | Inputs, children (refs), grandchildren |
| `GET` | `/api/admin/outcomes?period_days=&agent=&place=` | Outcome counts + success rate per member/action/place |
| `GET` | `/api/admin/lessons` | Lessons and policies |
| `GET` | `/api/admin/integrity[?member=]` | Full chain re-verification |
| `GET` | `/api/admin/recall-log?action=&member=` | Audit log (recall by default; `action=all`) |
| `GET` | `/api/admin/settings` | Effective config without secrets (`PUT` in P4) |

Errors are always `{"error": {"code", "message"}}`.

---

## Security Design

1. Only enroll/challenge/token/health are reachable without credentials; all are rate-limited per IP.
2. Member tokens: random 32 bytes, stored as SHA-256, 5-min TTL, permission snapshot ∩ current permissions; revoked members fail on the next request.
3. Admin: bcrypt, constant-time miss path, 10 logins/min/IP, httpOnly + Secure + SameSite=Strict cookie scoped to `/api`, CSRF header on writes.
4. Per-member rate limits: 10 ingest req/s, 5 recall req/s (in-process → **single worker** until moved to Postgres/Redis).
5. TLS by nginx/Plesk; app runs with `--proxy-headers --forwarded-allow-ips=127.0.0.1` so client IPs are real.
6. SQLAlchemy parameterised queries only.
7. Records hash-chained and signed; DB triggers block edits; Integrity view catches edits made by someone who bypasses the triggers.

**A compromised member** can read within its scope and append under its own name. It cannot edit or delete history, write as another member, write `policy`/`system`, or command any robot. Revocation is immediate.

---

## Retention (APScheduler, UTC)

| Job | Schedule | Action |
|---|---|---|
| `purge_expired_auth` | every 5 min | Delete expired challenges; tokens/sessions a day after expiry |
| `purge_event_payloads` | daily 07:00 UTC (03:00 ET) | Delete **payloads** of `event`/`perception` records older than 90 days; headers stay; audit entry written |
| `prune_audit_log` | Mondays 08:00 UTC | Delete audit rows older than 365 days |

Rollups into signed `system` summary records need a server signing identity — planned with P7.

---

## Build Phases

| Phase | Deliverables | "Done when" | Status |
|---|---|---|---|
| **P1** — Repo scaffold | contract, schema, server package, tests | `pytest` green against SQLite **and** PostgreSQL | ✅ 15 Oct |
| **P2** — Ingest + auth live | `main.py` runs, token + ingest pipeline, CLI agent sends 20 records | `cli_agent send` → records in PostgreSQL, `cli_agent verify` OK | ✅ 15 Oct (uvicorn + Postgres 16 + CLI over HTTP) |
| **P3** — Recall + dashboard | Dashboard: Login, Timeline, Members (approve), Integrity | Admin approves a test agent; Timeline shows its records with hashes | API ready; UI next |
| **P4** — Full dashboard | Conversation, Decision Trace, Outcomes, Lessons, Recall Log, Settings (+ `PUT`) | All pages render real data | API ready except settings `PUT` |
| **P5** — Z3-R0 recall | Pi Zero relay service using `cli_agent/client.py`; UNO Q context pack calls | Q receives context before a deliberate action | |
| **P6** — Remote access | VPS, Let's Encrypt, `PLESK_DEPLOYMENT_PLAN.md` | Dashboard loads over HTTPS; unenrolled request → 401/403 | |
| **P7** — Outside AI members + server identity | Cloud agents enroll with `recall:scoped`; server key signs `system` rollup records | Outside agent enrolls, contributes, recalls within scope | |
| **P8** — Lessons + vector | Reflecting agent writes lessons; pgvector column + embedding pipeline | "Similar situation" query returns relevant records | |

---

## Verification

```bash
pip install -e '.[dev]'
pytest                                                         # SQLite, ~30 s
LTMS_TEST_DATABASE_URL=postgresql+asyncpg://user@localhost/postgres pytest   # real Postgres
python -m scripts.dump_schema   # after any model change (a test fails if you forget)
```

Coverage highlights: JCS edge cases and published vectors; every tamper path
(header, payload, sig, wrong key, wrong agent, gap, chain, fork, future ts);
domain separation; valid-prefix batches and idempotent retries; offline-buffered
old timestamps accepted; append-only triggers; concurrent batches serialize (Postgres);
nonce single-use/expiry; token expiry/revocation; permission change kills tokens;
scoped recall; CSRF; integrity catches edits made behind the trigger; payload purge
keeps the chain valid; CLI end-to-end.

---

## Decisions (resolved)

1. **First admin** — `LTMS_ADMIN_PASSWORD` on first start creates the account; change it with `python -m server.manage set-admin-password`.
2. **Token TTL** — 300 s. The client re-logs in automatically 20 s before expiry.
3. **Max batch** — global 100. Per-member override not needed yet.
4. **`recall:scoped`** — `{types, agents, places}` set by the admin at approval; types must be listed.
5. **Hybrid deployment** — same code and `schema.sql` on VPS and home LAN; only `LTMS_DATABASE_URL` differs.
6. **Keyframes** — not in v1. When added: content-addressed side table referenced from the payload, purged on its own schedule.
7. **Batch failure** — keep the valid prefix (robots never resend accepted work).
8. **Policy records** — need the `policy` permission (design doc: administrator-only).

## Not in this plan

Z3-R0 hardware (`Z3-R0-Build-Plan.md`), Jetson mesh hub, Vector SDK adapter (a thin
client like `cli_agent/client.py`), cloud agent SDKs, meaning-based recall (P8).
