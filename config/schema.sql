-- LTMS schema (PostgreSQL 15+, pgvector optional until Phase 8).
-- Idempotent: safe to run repeatedly. The signed-chain contract
-- (schema/v1/schema.json) is frozen; this DDL mirrors it.
--
-- The record store is a permanent CHAIN-HEADER table (`records`) plus a
-- mutable, purgable PAYLOAD table (`recordpayload`). Chain invariants only
-- ever touch `records`; retention touches payloads + keyframes.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS members (
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

-- CHAIN-HEADER: permanent, append-only. `records_no_mutation` fires here only.
CREATE TABLE IF NOT EXISTS records (
  id          BIGSERIAL PRIMARY KEY,
  member_id   UUID NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  agent       TEXT NOT NULL,             -- denormalised from member.name for queries
  seq         BIGINT NOT NULL,           -- member-relative, 1..N
  ts          TIMESTAMPTZ NOT NULL,
  type        TEXT NOT NULL CHECK (type IN (
                 'communication','decision','action','event',
                 'perception','lesson','policy','system')),
  summary     TEXT,
  refs        JSONB DEFAULT '{}',
  place       TEXT,
  peers       JSONB DEFAULT '[]',
  body_hash   BYTEA NOT NULL,             -- SHA-256(canonical_payload_bytes)
  sig         BYTEA NOT NULL,             -- Ed25519 over domain-separated hash
  hash        BYTEA NOT NULL,             -- SHA-256(sig_bytes); chain links prev_hash -> hash
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (member_id, seq)
);

-- Append-only enforcement on the chain header.
CREATE OR REPLACE FUNCTION records_no_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'records table is append-only: UPDATE and DELETE are not permitted';
END
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_records_no_mutation
  BEFORE UPDATE OR DELETE ON records
  FOR EACH ROW EXECUTE FUNCTION records_no_mutation();

-- Core indexes. No embedding index until Phase 8 (column absent until then).
CREATE INDEX IF NOT EXISTS idx_records_ts      ON records (ts DESC);
CREATE INDEX IF NOT EXISTS idx_records_member_seq ON records (member_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_records_type    ON records (type);

-- Mutable, purgable payload for one chain header row. Retention + rollups work here.
CREATE TABLE IF NOT EXISTS recordpayload (
  id           BIGSERIAL PRIMARY KEY,
  header_id    BIGINT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  member_id    UUID NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  seq          BIGINT NOT NULL,
  status       TEXT,                     -- for outcomes: e.g. 'success','failure'
  place        TEXT,                     -- where/when the record was made
  body         JSONB NOT NULL DEFAULT '{}',
  media_type   JSONB DEFAULT '[]',
  ts           TIMESTAMPTZ NOT NULL,
  UNIQUE (member_id, seq),
  FOREIGN KEY (member_id, seq) REFERENCES records(member_id, seq) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_recordpayload_seq      ON recordpayload (seq DESC);
CREATE INDEX IF NOT EXISTS idx_recordpayload_media    ON recordpayload ((media_type::text));

-- Short-lived opaque member tokens. `member_id` is nullable so an admin-owned
-- token can exist; admin login sessions live in `admin_sessions`, not here.
CREATE TABLE IF NOT EXISTS tokens (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  member_id    UUID REFERENCES members(id),            -- nullable for admin
  token_hash   BYTEA NOT NULL UNIQUE,                  -- SHA-256(raw 32-byte token)
  permissions  JSONB NOT NULL,
  remote_ip    INET,
  issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at   TIMESTAMPTZ NOT NULL,
  revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tokens_member ON tokens (member_id) WHERE revoked_at IS NULL;

-- Admin login sessions. Dedicated store so admin cookie lifecycle never touches
-- the member token store.
CREATE TABLE IF NOT EXISTS admin_sessions (
  session_id  BYTEA PRIMARY KEY,             -- 32-byte opaque session cookie
  username    TEXT NOT NULL REFERENCES admin_user(username),
  issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  revoked_at  TIMESTAMPTZ
);

-- Auditor: recalls, logins, enroll, ingest rejects.
CREATE TABLE IF NOT EXISTS audit_log (
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

-- Admin user (bcrypt password hash).
CREATE TABLE IF NOT EXISTS admin_user (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username       TEXT UNIQUE NOT NULL,
  password_hash  TEXT NOT NULL,            -- bcrypt
  last_login_at  TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Signed thumbnails are content-addressed so they can be purged independently
-- of the chain. Embedded in payload JSONB in v1; side-table added in P8.
CREATE TABLE IF NOT EXISTS keyframe (
  id           BIGSERIAL PRIMARY KEY,
  entry_id     UUID NOT NULL REFERENCES recordpayload(id) ON DELETE CASCADE,
  content_hash BYTEA UNIQUE NOT NULL,
  size_at      INTEGER NOT NULL,
  media_type   TEXT NOT NULL DEFAULT 'image/png'
);
