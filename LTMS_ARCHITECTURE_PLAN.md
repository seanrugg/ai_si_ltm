# LTMS Architecture Plan

**Long-Term Memory Server — repo architecture and implementation plan**
Version 1.0 · 12 Oct 2026 · Owner: Sean
Reference: `Robot-Mesh-and-Long-Term-Memory-Plan.md` (primary design doc)

---

## Scope

This repo is the LTMS: an append-only, tamper-evident record store for every AI/robot
member of the mesh, plus the admin dashboard. Z3-R0 is the first enrolled member and
serves as the reference example. Other members (Jetson Orin, Anki Vector, future
UNO Q robots, cloud LLM agents) use the same API.

Deploying to: Plesk VPS (`[ltms-host].cucorn.com`), PostgreSQL on localhost,
gunicorn + UvicornWorker as a systemd service; React dashboard as static files.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, asyncpg |
| Crypto | `pynacl` (Ed25519), `hashlib` (SHA-256) |
| Auth (members) | Ed25519 signature + opaque 32-byte bearer token, stored as SHA-256 hash |
| Auth (admin) | `ADMIN_USERNAME`/`ADMIN_PASSWORD` env vars, bcrypt, 30-min httpOnly session cookie |
| Database | PostgreSQL 15+, `pgvector` extension (`pgvext` enabled at deploy; vector column reserved for Phase 8) |
| Dashboard | React 18, Vite 5, React Router, Tailwind CSS 3 |
| Scheduling | APScheduler (retention rollups, token GC) |
| Deployment | gunicorn + systemd (backend); Plesk static files (dashboard) |

---

## Repo Structure

```
ai_si_ltm/
├── README.md
├── LICENSE
├── .gitignore
├── LTMS_ARCHITECTURE_PLAN.md        ← this file
├── PLESK_DEPLOYMENT_PLAN.md         ← written before deployment
│
├── config/
│   ├── schema.sql                   # Full DDL: tables, triggers, indexes, pgvector
│   └── default.yaml                 # Port, token TTL, rate limits, retention defaults
│
├── server/                          # FastAPI backend
│   ├── __init__.py
│   ├── pyproject.toml
│   ├── main.py                      # create_app() factory, lifecycle hooks
│   ├── config.py                    # ServerConfig (pydantic-settings, env-driven)
│   ├── db.py                        # Async engine, session factory, get_db
│   ├── tasks.py                     # APScheduler jobs: retention, token GC
│   │
│   ├── security/
│   │   ├── __init__.py
│   │   ├── ed25519.py               # verify_signature(pubkey, message, sig)
│   │   ├── hash.py                  # compute_record_hash(dict) → 32-byte digest
│   │   ├── tokens.py                # issue / verify / revoke opaque tokens
│   │   └── deps.py                  # FastAPI Depends: current_member, require_admin
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py                  # DeclarativeBase
│   │   ├── member.py                # Member row
│   │   ├── record.py                # Record CHAIN-HEADER row (append-only; per-item immutable)
│   │   ├── recordpayload.py         # Record PAYLOAD row (mutable; purgeable)
│   │   ├── keyframe.py              # Content-addressed image blob store (P8)
│   │   ├── token.py                 # ActiveToken row
│   │   ├── adminsession.py          # Admin session cookie store
│   │   └── audit.py                 # AuditLog row
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── ingest.py                # verify_chain → validate → insert → ack
│   │   ├── recall.py                # Filtered query + context pack composition
│   │   ├── enrollment.py            # approve / reject / revoke / set permissions
│   │   ├── integrity.py             # Full chain re-verification for a member
│   │   └── retention.py             # Rollup + purge (async, scheduled)
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py                # All FastAPI route definitions
│   │   ├── schemas.py               # Pydantic request/response models
│   │   └── errors.py                # Exception → JSON mapping
│   │
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py              # Test DB fixtures, signed-record factory
│       ├── test_chain.py            # Hash chain: valid, broken, gap
│       ├── test_auth.py             # Token lifecycle
│       ├── test_ingest.py           # Valid batch, wrong agent, bad hash, bad sig
│       ├── test_recall.py           # Filtered query + context pack
│       ├── test_enrollment.py       # Admin approve/reject flow
│       └── test_admin.py            # Admin endpoints, 403 cases
│
├── dashboard/                       # React admin UI (10 routes total)
│   ├── package.json
│   ├── vite.config.js               # base: './'
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── index.html
│   └── src/
│       ├── main.jsx
│       ├── App.jsx                  # Router + shell layout
│       ├── index.css
│       ├── api/client.js            # fetch wrapper, cookie auth, 401 redirect
│       ├── context/AdminContext.jsx
│       ├── components/
│       │   ├── Layout/
│       │   │   ├── Shell.jsx
│       │   │   └── Sidebar.jsx
│       │   ├── Shared/
│       │   │   ├── StatusBadge.jsx
│       │   │   ├── EmptyState.jsx
│       │   │   └── LoadingSpinner.jsx
│       │   ├── RecordCard.jsx
│       │   └── MemberCard.jsx
│       └── pages/
│           ├── Login.jsx
│           ├── Timeline.jsx
│           ├── Conversation.jsx
│           ├── DecisionTrace.jsx
│           ├── Outcomes.jsx
│           ├── Lessons.jsx
│           ├── Members.jsx
│           ├── Integrity.jsx
│           ├── RecallLog.jsx
│           └── Settings.jsx
│
└── cli_agent/                       # Synthesised test member — full pipeline E2E
    ├── __init__.py
    ├── __main__.py                  # python -m cli_agent
    ├── agent.py                     # commands: genkey | token | enroll | send | verify | recall
</parameter"
    └── signing.py                   # pynacl Ed25519 key gen + sign
```

---

## Record Hash and Signature (contract shared by all members)

> **This contract is frozen at P1.** Once members sign records, `body_hash`,
> `raw`, the canonical encoding, the hash/payload split, and the signature
> domain prefixes cannot change — every stored `sig` is a signature over the
> exact bytes that `prev_hash` chains into the next record. Keep hashing cheap
> (canonicalise the payload, hash once, store the bytes). The verification
> logic lives in `server/security/`.
>
> `raw` is stored as `BYTEA`, not as JSONB — text columns round-trip and
> normalise differently; `raw` is the single source of truth so
> `integrity.py` can always recompute `body_hash` and it stays verifiable by
> every member, in every language.

**Split into a permanent header + a mutable payload.**

A record has two tables:

| Header (`records`) | Payload (`recordpayload`) |
|---|---|
| Permanent, never mutated (`records_no_mutation` trigger) | Purged on a schedule |
| Header-only rows only, chain runs through | |
| one row per header, linked by `id` | |

- **Header** holds the chain: `id, member_id, seq, ts, type, summary, refs, tags, sw, prev_hash, body_hash, sig, hash, created_at`.
- **Payload** holds the mutable body: `id, header_id, member_id, seq, ts, status, place, peers, body`.

Retention touches only `recordpayload` (plus content-addressed keyframes). The chain never sees body mutations or purges — `records_no_mutation` only ever fires on the header table.

**Canonical form.** Adopt **RFC 8785 (JCS)**. Keys sorted alphabetically everywhere, minimal UTF-8, no insignificant whitespace:
nested objects sorted (`{ "a": { "a1": 1, "a2": 2 } }`), all numbers written as **decimal** (`1.0`, `0`, `-0.5`), dates/time in **ISO 8601 UTC** (`2026-10-12T00:00:00Z`). No floats in hashed fields. `raw` is the exact bytes; verify against those, not against JSONB or TIMESTAMPTZ.

**Hash.** `body_hash = SHA-256(canonical_bytes(payload, include place + peers + tags))`. Store and chain `body_hash` (not `body`); `hash`/`raw` stay opaque (stored `raw BYTEA`, served as-is, compared by hash), keeping chain verification independent of message framing.

**Signature & domain separation.** Prefix a message before signing so a record signature and a token/nonce signature can never cross signatures:
- record: `ltms-record-v1 : 64-hex(body_hash)`
- token/nonce: `ltms-auth-v1 : <32 hex>`

`sig  = Ed25519_sign(private_key, prefix || nonce)`, `hash = SHA-256(sig_bytes)`. This means `prev_hash` bytes are hex-encoded (not raw), so they serialise correctly and stay unambiguous.

`prev_hash` for `seq=1` is a 32-byte zero vector (genesis marker).

**Server verify order for each ingested record:**
1. Member is enrolled and `status='active'`
2. `agent` field matches member's `name` (no writing as another member)
3. `type` is permitted for member's permission set
41. `hash`/`prev_hash` match header; chain links `prev_hash` → `hash`
42. `ts` in header matches payload; `ts` ≤ `clock_skew_sec` from `now()`; `type` ∈ `enum`; `seq == last_acknowledged + 1` (no gaps)
43. `body_hash` matches payload (canonicalised)
44. `sig` over `hash` verifies with member's stored public key
45. All header + payload columns match
46. `ts` not older than `max_replay_sec` (default 3600; per-member)
47. Insert header + payload, advance `last_acknowledged`

---

## Database Schema (config/schema.sql — summary)

### members

```sql
CREATE TABLE members (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  public_key      BYTEA UNIQUE NOT NULL,          -- Ed25519 32-byte public key
  name            TEXT UNIQUE NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('robot','agent','assistant','relay')),
  operator        TEXT,
  permissions     JSONB NOT NULL DEFAULT '[]',    -- ['contribute','recall','recall:scoped']
  status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','active','revoked')),
  enrolled_at     TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ,
  last_seen_at    TIMESTAMPTZ,
  last_ack_seq    BIGINT NOT NULL DEFAULT 0,      -- highest seq confirmed by server
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### records — CHAIN-HEADER TABLE (permanent, append-only)

Holds only the immutable chain headers for one member. retention + payload data live in
`recordpayload`; the trigger only affects this table. The schema is fixed — layout or
schema changes bump `schema_version` and re-hash the affected member (see the security
design section at the bottom). Chain links `prev_hash` (genesis marker for `seq = 1`) to
each `hash`/`body_hash` byte string:

```sql
CREATE TABLE records (
  -- CHAIN-HEADER (append-only, immutable). payload lives in recordpayload. No embeddings
  -- column until P8; the embedding index is built then -- not here. Idempotent schema.
  id          BIGSERIAL PRIMARY KEY,
  member_id   UUID NOT NULL REFERENCES members(id),
  agent       TEXT NOT NULL,             -- denormalised from member.name for queries
  seq         BIGINT NOT NULL,             -- member-relative, 1..N
  ts          TIMESTAMPTZ NOT NULL,
  type        TEXT NOT NULL CHECK (type IN (
                  'communication','decision','action','event',
                  'perception','lesson','policy','system')),
  summary     TEXT,
  refs         JSONB DEFAULT '{}',
  place       TEXT,
  peers       JSONB DEFAULT '[]',
  body_hash   BYTEA NOT NULL,              -- SHA-256(canonical_payload_bytes)
  sig         BYTEA NOT NULL,              -- Ed25519 over concat(prefix, hash(bytes))
  hash        BYTEA NOT NULL,              -- SHA-256(sig_bytes); chain links prev_hash -> hash
  prev_hash   BYTEA NOT NULL,              -- genesis = 32 x 0x00 at seq=1
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (member_id, seq)
);

-- Append-only enforcement
CREATE OR REPLACE FUNCTION records_no_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'records table is append-only: UPDATE and DELETE are not permitted';
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_records_no_mutation
  BEFORE UPDATE OR DELETE ON records
  FOR EACH ROW EXECUTE FUNCTION records_no_mutation();

-- Core indexes
CREATE INDEX idx_records_ts       ON records (ts DESC);
CREATE INDEX idx_records_member_seq ON records (member_id, seq DESC);
CREATE INDEX idx_records_type     ON records (type);
CREATE INDEX idx_records_summary_fts
  ON records USING GIN (to_tsvector('english', coalesce(summary,'')));


### tokens  (short-lived opaque member tokens)

`member_id` is nullable so a non-member value is permitted for admin sessions.
`token_hash` is the SHA-256 of the raw 32-byte token; the raw token stays in memory only.

```sql
CREATE TABLE tokens (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  member_id    UUID REFERENCES members(id),            -- nullable for admin
  token_hash   BYTEA NOT NULL UNIQUE,                  -- SHA-256(raw 32-byte token)
  permissions  JSONB NOT NULL,
  remote_ip    INET,
  issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at   TIMESTAMPTZ NOT NULL,
  revoked_at   TIMESTAMPTZ NULL
);
CREATE INDEX idx_tokens_member ON tokens (member_id) WHERE revoked_at IS NULL;


### audit_log  (recall, access, enrollment, rejects)

```sql
CREATE TABLE audit_log (
  id              BIGSERIAL PRIMARY KEY,
  ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
  member_id       UUID REFERENCES members(id),
  action          TEXT NOT NULL,
                  -- 'recall','login','enroll_request','enroll_approve',
                  -- 'enroll_reject','ingest_reject','token_issued','token_revoked'
  detail          JSONB NOT NULL DEFAULT '{}',
  request_id      TEXT,
  response_count  INT
);
CREATE INDEX idx_audit_ts ON audit_log (ts DESC);
CREATE INDEX idx_audit_member ON audit_log (member_id, ts DESC);
```

### recordpayload — mutable, purgable body for one records row

The append-only trigger never fires here; retention and rollups modify this
table. Layout is fixed — changes bump `schema_version` and re-hash the member.

```sql
CREATE TABLE recordpayload (
  -- payload body for one records row. schema fixed — changes bump schema_version.
  id           INTEGER PRIMARY KEY,
  keyholder_id UUID NOT NULL,            -- references members(id)
  seq          BIGINT NOT NULL,
  status       TEXT,                     -- for outcomes: e.g. 'success','failure'
  place        TEXT,                     -- where/when the record was made
  body         JSONB NOT NULL DEFAULT '{}',
  media_type   JSONB DEFAULT '[]',
   header_id    BIGINT NOT NULL REFERENCES records(id) ON DELETE CASCADE
);
CREATE INDEX idx_recordpayload_seq ON recordpayload (seq DESC);
CREATE INDEX idx_recordpayload_media ON recordpayload (media_type);


### admin_user

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector

CREATE TABLE admin_user (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username       TEXT UNIQUE NOT NULL,
  password_hash  TEXT NOT NULL,            -- bcrypt
  last_login_at  TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
### admin_sessions  (admin dashboard cookie store)

Admin login sessions as 30-min httpOnly cookies. Kept separate from member `token`s so the
member-only `token` lifecycle never affects admin auth, and so admin sessions stay out of the
member chain.

```sql
CREATE TABLE admin_sessions (
  session_id  BYTEA PRIMARY KEY,             -- 32-byte opaque session cookie
  username    TEXT NOT NULL REFERENCES admin_user(username),
  issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  revoked_at  TIMESTAMPTZ
);
CREATE INDEX idx_admin_sessions_user ON admin_sessions (username, expires_at);
```

### keyframe  (Content-addressed image store — Phase 8)

Signed thumbnails live here as content-addressed blobs that can be purged independently of
`records`. Bumped by `schema_version` so payload layout changes never force a chain rebuild.

```sql
CREATE TABLE keyframe (
  keyframe_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  schema_version BIGINT NOT NULL DEFAULT 1,
  member_id    UUID NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  seq          INT NOT NULL,
  content_hash BYTEA UNIQUE NOT NULL,
  media_type   TEXT NOT NULL DEFAULT 'image/png',
  size_at      INT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_keyframe_member_seq ON keyframe (member_id, seq);
CREATE INDEX idx_keyframe_content ON keyframe (content_hash);
```

---
## API Surface

### Public (no auth)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/enroll` | Submit enrollment request (capped per remote IP) |
| `GET` | `/api/auth/challenge` | One-time nonce + replay window (45 s, bump to 90 s) per IP |

### Member (Authorization: Bearer \<token\>)

| Method | Path | Permissions | Purpose |
|---|---|---|---|
| `POST` | `/api/auth/token` | Ed25519 sig | Exchange a fresh `/api/auth/challenge` nonce + signature for access token |
| `POST` | `/api/records` | `contribute` | Ingest a batch (max 100 records) |
| `GET` | `/api/records` | `recall` | Filtered recall |
| `GET` | `/api/recall/context` | `recall` | Context pack for a situation |

`GET /api/records` query params:
`type`, `agent`, `place`, `peer`, `outcome`, `from`, `to`, `summary`, `limit` (default 25, max 200), `offset`

```
GET /api/recall/context?intent=approach&target=vector&place=living-room
→ returns: last 10 outcomes of that action, peer comms last 24h,
           applicable lessons, relevant policies  (capped 25 total)
```

### Admin (session cookie `ltms_admin`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/admin/login` | Admin login → sets cookie |
| `POST` | `/api/auth/admin/logout` | Clear cookie |
| `GET` | `/api/admin/timeline` | All records, newest first, paginated |
| `GET` | `/api/admin/conversation?member_a=&member_b=` | Threaded comm view |
| `GET` | `/api/admin/decision-trace?member=&seq=` | Decision + child actions + outcomes |
| `GET` | `/api/admin/outcomes?period=` | Aggregated success rates |
| `GET` | `/api/admin/lessons` | Derived lessons + policies with source refs |
| `GET` | `/api/admin/members` | Active + pending members |
| `GET` | `/api/admin/members/pending` | Enrollment requests to act on |
| `POST` | `/api/admin/members/{id}/approve` | Activate member |
| `POST` | `/api/admin/members/{id}/revoke` | Revoke (mark revoked_at, keep records) |
| `PATCH` | `/api/admin/members/{id}/permissions` | Update permission set |
| `GET` | `/api/admin/integrity` | Per-member chain status |
| `GET` | `/api/admin/recall-log` | Audit log of recall queries |
| `GET` | `/api/admin/settings` | Retention, rate limits, quotas |
| `PUT` | `/api/admin/settings` | Update settings |

---

## Security Design

**Layer order (outermost first):**

1. No public pages or anonymous endpoints. All `/api/` paths require a valid token.
2. Opaque token in `Authorization` header; server looks up SHA-256 hash in `tokens` table.
3. Every request checked against member's permission set before routing.
4. Rate limiter (in-memory, per-member): 10 req/s ingest, 5 req/s recall, max 100 records/batch.
5. TLS enforced by nginx/Plesk (http→https redirect).
6. No SQL injection surface: SQLAlchemy parameterised queries only.
7. Records are hash-chained and signed — an edit leaves a detectable gap.
8. Admin session is a 30-min httpOnly cookie whose 32-byte session id lives in `admin_sessions` (a **dedicated** table — never in `tokens`, which is the member token store).

**Public exceptions** are exactly two, both throttled per remote IP and never open:
- `/api/auth/enroll` — submits an enrollment request; per-IP capacity cap; the next step is a nonce-bound signature check.
- `/api/auth/challenge` — hands out a single-use nonce within a short replay window (45 s, bump to 90 s).
- `/api/auth/token` — verified below with a fresh nonce.

All other `/api/` paths enforce their session cookie or bearer token; a missing/invalid session on a public path (e.g. `/api/admin/*`) is refused with 403.

**What a compromised member can do:** read within its own scope and add records under its own name. Cannot edit/delete history, read other members' data beyond its scope, or command any robot. Immediate revocation via admin dashboard.

---

## Retention and Rollups (APScheduler jobs)

| Job | Schedule | Action |
|---|---|---|
| `purge_expired_tokens` | Every 5 min | Delete tokens past `expires_at` |
| `rollup_events` | Daily 03:00 | Aggregate `event` + `perception` records past 90 days into summary records |
| `purge_keyframes` | Daily 04:00 | Delete keyframe image blobs past 30 days (when image storage is added) |
| `audit_log_prune` | Weekly | Delete `audit_log` rows older than 365 days |

Each rollup/purge writes a `type='system'` record before deleting anything, so the operation itself is in the chain.

---

## CLI Test Agent (cli_agent/)

Purpose: verify the full pipeline before Z3-R0 is built.

```bash
# Generate a test key pair
python -m cli_agent genkey --name "test-agent-1" --out keys/test-agent-1.json

# Enroll (admin approves on dashboard)
python -m cli_agent enroll --key keys/test-agent-1.json \
  --server http://localhost:8080 \
  --kind agent --permissions contribute,recall

# Acquire token
python -m cli_agent token --key keys/test-agent-1.json \
  --server http://localhost:8080 --out keys/tok.txt

# Send 20 realistic records (comms, decisions, actions with outcomes)
python -m cli_agent send --key keys/test-agent-1.json \
  --server http://localhost:8080 --count 20

# Verify the chain is intact from seq 1
python -m cli_agent verify --key keys/test-agent-1.json \
  --server http://localhost:8080

# Fetch a context pack for "approach vector in living-room"
python -m cli_agent recall --key keys/test-agent-1.json \
  --server http://localhost:8080 \
  --intent "approach vector in living-room"
```

The agent generates realistic records: a `communication` followed by a `decision`
referencing it, then an `action` with an `outcome`, matching the plan's examples.

---

## Dashboard Pages (10 total routes)

| Route | Page | Primary data source |
|---|---|---|
| `/login` | Login | Admin cookie |
| `/` | Timeline | `GET /api/admin/timeline` |
| `/conversation` | Conversation | `GET /api/admin/conversation` |
| `/decision-trace/:seq` | Decision Trace | `GET /api/admin/decision-trace` |
| `/outcomes` | Outcomes | `GET /api/admin/outcomes` |
| `/lessons` | Lessons & Policies | `GET /api/admin/lessons` |
| `/members` | Members | `GET /api/admin/members` |
| `/integrity` | Integrity | `GET /api/admin/integrity` |
| `/recall-log` | Recall Log | `GET /api/admin/recall-log` |
| `/settings` | Settings | `GET/PUT /api/admin/settings` |

**Layout:** dark theme, left sidebar with nav + member status indicator,
top bar with current member filter + search, content area with cards.
All list views: virtual scrolling for >200 records (react-window or similar).

---

## Environment Variables (server)

```
# Database
LTMS_DB_HOST=localhost
LTMS_DB_PORT=5432
LTMS_DB_NAME=ltms
LTMS_DB_USER=ltms
LTMS_DB_PASSWORD=              # required, no default

# Auth
LTMS_ADMIN_USERNAME=admin
LTMS_ADMIN_PASSWORD=           # required on first start; stored bcrypt-hashed in DB
LTMS_TOKEN_TTL_SECONDS=300    # 5 min default

# Rate limits
LTMS_INGEST_RATE=10           # batches/s per member
LTMS_RECALL_RATE=5            # queries/s per member
LTMS_MAX_BATCH=100            # records per POST /api/records
LTMS_MAX_RECALL=200           # records per GET /api/records

# Retention
LTMS_RETENTION_KEYFRAMES_DAYS=30
LTMS_RETENTION_EVENTS_DAYS=90
LTMS_RETENTION_AUDIT_DAYS=365
```

---

## Build Phases (from the memory plan, with file ownership)

| Phase | Deliverables | "Done when" |
|---|---|---|
| **P1** — Repo scaffold | `config/schema.sql`, `server/` full Python package, `conftest.py`, all 7 unit test files passing | `pytest server/tests/` green against a real (or test-DB) PostgreSQL instance |
| **P2** — Ingest + auth live | `main.py` runs, full member token + ingest pipeline, CLI agent sends 20 records | `cli_agent send` → records visible in PostgreSQL, chain verified |
| **P3** — Recall + dashboard | Dashboard deployed, admin can see Timeline, Members, Integrity, approve members | Admin approves a test agent; Timeline shows its records with hashes |
| **P4** — Full 8 dashboard views | React all pages, conversation/decision-trace/outcomes/lessons/recall-log/settings all working | All pages render real data from the API |
| **P5** — Z3-R0 recall | Pi Zero relay service, UNO Q context pack calls | Q receives context before a deliberate action |
| **P6** — Remote access | VPS deployed, Let's Encrypt, admin can view from outside home | Dashboard loads from `https://[ltms-host].cucorn.com`; unenrolled request → 403 |
| **P7** — Outside AI members | Direct ingest API for cloud agents, scoped recall | An outside agent enrolls, contributes, recalls within scope |
| **P8** — Lessons + vector | Reflecting agent writes lessons; `pgvector` batch-embedding pipeline | "Similar situation" query returns relevant past records |

---

## Verification Plan

**Unit tests (P1):**
- `test_chain.py`: valid chain accepted; broken prev_hash rejected; gap in seq rejected; sig mismatch rejected
- `test_auth.py`: token issued after valid sig; expired token rejected; revoked token rejected
- `test_ingest.py`: single record OK; batch of 5 OK; wrong agent rejected; type outside permissions rejected
- `test_recall.py`: filtered type query returns only matching; context pack capped at 25
- `test_enrollment.py`: pending → approve → active; active → revoke → cannot ingest
- `test_admin.py`: admin endpoints return 401 without cookie; 403 for non-admin

**Integration test (P2, manual):**
1. Initialise test PostgreSQL with `config/schema.sql`
2. Start `uvicorn server.main:app --reload`
3. `python -m cli_agent genkey --name "e2e-agent"`
4. Enroll via curl → approve via admin login
5. `python -m cli_agent send --count 20`
6. `python -m cli_agent verify` → chain intact
7. Query `GET /api/records?type=action&outcome=reflex_stop` → correct records

**Dashboard smoke test (P3):**
1. `cd dashboard && npm install && npm run build`
2. Open `dist/index.html` locally against the dev server
3. Admin login → approve test agent → Timeline shows all 20 records

---

## Open Decisions (from the memory plan, to resolve before Phase 2)

1. **First admin credentials** — set via env on first start (as in LLM_instruct_models) or interactive setup command?
   → Plan: env vars on first start (consistent with existing pattern)
2. **Token TTL** — 300 s (5 min) as default; acceptable?
3. **Max batch size** — 100 records or should it be configurable per-member?
   → Plan: global config `LTMS_MAX_BATCH=100`, overridable per-member in permissions JSONB
4. **`recall:scoped`** — which `type`, `agent`, or `place` fields to restrict? Defined per enrollment request.
5. **Hybrid deployment** — v1 is internet (VPS) only. Home LAN copy is Phase 6; the same `schema.sql` and code run on both, with `LTMS_DB_HOST` pointing to the local Jetson NAS.
6. **Keyframe images** — leave the `body` field for small base64 thumbnails in the JSONB (no separate blob storage) in v1; move to a side-table in Phase 8 if needed.

---

## What is NOT in this plan

- Z3-R0 hardware build (separate project track; `Z3-R0-Build-Plan.md` is the reference)
- Jetson Orin mesh-hub implementation (MQTT broker config, Jetson-specific drivers)
- Vector robot SDK integration (Anki Vector has its own SDK; a thin adapter would connect to the LTMS ingest API the same as any other member)
- Cloud LLM agent SDKs (each agent writes its own client against the public API)
- Meaning-based recall implementation (Phase 8 — pgvector column is in the schema; the embedding pipeline and query API come later)
