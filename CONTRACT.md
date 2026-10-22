# LTMS Record Contract — v1

This is the byte-level agreement every mesh member shares with the server. It is
**frozen**: changing anything here means a new `v` and a migration plan, because
records already signed under v1 must verify forever.

Reference implementation: `ltms_contract/` (Python, depends only on `pynacl`).
Test vectors: `ltms_contract/vectors.json` — any other implementation (C++ on the
UNO Q, a JS cloud agent) must reproduce every byte in it.

## 1. Record on the wire

A record is a JSON object in three parts:

| Part | Fields | Lifetime |
|---|---|---|
| **Header** | every field except `payload`, `hash`, `sig` | permanent, append-only |
| **Payload** | `payload` (a JSON object; `{}` if none) | may be purged by retention |
| **Seal** | `hash`, `sig` | permanent |

### Header fields

| Field | Req. | Rule |
|---|---|---|
| `v` | yes | integer `1` |
| `agent` | yes | member name, `^[a-z0-9][a-z0-9._-]{0,63}$`; must equal the enrolled name |
| `seq` | yes | integer ≥ 1; exactly previous + 1 |
| `ts` | yes | RFC 3339 string **with offset** (`Z` or `±hh:mm`), fractional seconds optional. Hashed as the exact string sent |
| `type` | yes | `communication` `thought` `decision` `action` `event` `perception` `lesson` `policy` `system` (see 1.1) |
| `prev_hash` | yes | previous record's `hash`; seq 1 uses 64 zeros |
| `payload_hash` | yes | `SHA-256(JCS(payload))`, lowercase hex |
| `summary` | no | string ≤ 500 |
| `place` | no | string ≤ 128 |
| `peers` | no | list (≤ 32) of non-empty strings ≤ 64 |
| `tags` | no | list (≤ 32) of non-empty strings ≤ 64 |
| `refs` | no | object (≤ 16 keys); values are `"agent:seq"` strings or lists of them |
| `outcome` | no | `success` `partial` `reflex_stop` `stuck` `abandoned` `failed` |
| `sw` | no | software/version string ≤ 128 |

No other header fields are allowed — put anything else in `payload`. An absent
optional field and a field set to `null` hash differently; omit fields you don't use.

Payload: any JSON object, canonical size ≤ 64 KiB.

### 1.1 Record types

The **author** chooses `type` when it signs the record. Relays and the server
never assign or change it (any change breaks the signature); the server only
checks that the type is listed here and that the member may write it.

| Type | Use it for | Example summary |
|---|---|---|
| `perception` | something sensed, outside or inside the member | "Obstacle 12 cm ahead", "Battery 71%" |
| `thought` | reasoning that commits to nothing: plans, predictions, questions, interpretations | "Vector may be asleep; check the dock first" |
| `decision` | a commitment to act, or not to act | "Slow to 0.1 m/s near Vector" |
| `action` | something the member did; set `outcome` when known | "Drove forward 0.5 m" |
| `communication` | a message sent or received between members or people | "Hello Z3" |
| `event` | something that happened to the member, not sensed or chosen | "Docked", "Rebooted" |
| `lesson` | a conclusion drawn from experience, meant to change future behavior | "Approach Vector slowly on the rug" |
| `policy` | a rule from an operator (needs the `policy` permission) | "No driving after 9 pm" |
| `system` | reserved for the server | — |

Conventions (tags, not types): sensing inside the member's own body (battery,
motor heat, mood) is a `perception` tagged `internal`. Periodic sensor windows
are `perception` records tagged `telemetry`, with a short range summary in
`summary` and the samples in `payload`. Link cause and effect with `refs`, e.g.
a decision's `refs: {"because": "z3-r0:41"}`.

`thought` was added to v1 on 2026-10-21, before any production record was signed.

## 2. Canonical JSON (RFC 8785 / JCS)

`JCS(x)` is RFC 8785 with two extra restrictions:

* object keys sorted by **UTF-16 code units**; no whitespace;
* strings: escape only `"` `\` and U+0000–U+001F (`\b \t \n \f \r` short forms,
  else `\u00xx` lowercase); everything else raw UTF-8; lone surrogates rejected;
* numbers: printed like ECMAScript `Number.prototype.toString` — so `1.0` → `1`,
  `1e21` → `1e+21`, `1e-7` → `1e-7`; NaN/Infinity rejected;
* **LTMS:** integers must satisfy `|n| ≤ 2^53 − 1`.

Floats are fine in payloads (robots need them). Parse JSON into IEEE doubles and
the canonical form is stable across languages.

## 3. Hashes and signatures

```
payload_hash = hex( SHA-256( JCS(payload) ) )            # stored in the header
hash         = hex( SHA-256( JCS(header) ) )
sig          = hex( Ed25519_sign( sk, "ltms-record-v1\n" || bytes.fromhex(hash) ) )
```

`hash` is 64 lowercase hex chars; `sig` is 128.

Login uses a different prefix so the two kinds of signature can never be swapped:

```
POST /api/auth/challenge {"name"}                 → {"nonce": <64 hex>, "expires_at"}
POST /api/auth/token     {"name","nonce","sig"}   → {"token", "expires_at", "permissions",
                                                     "last_ack_seq", "last_hash"}
sig = hex( Ed25519_sign( sk, "ltms-auth-v1\n" || bytes.fromhex(nonce) ) )
```

Nonces are single-use and expire after 60 s. Tokens last 5 min; send them as
`Authorization: Bearer <token>`.

## 4. What the server checks on ingest

In order, for each record in a batch:

1. `agent` equals the token's member
2. structure (§1), then `payload_hash`, then `hash`, then `sig` with the member's key
3. `type` is allowed: `system` never; `policy` only with the `policy` permission
4. `ts` is not more than 5 min in the future (old timestamps are fine — robots buffer offline)
5. `seq` ≤ head: same `hash` already stored → `duplicate` (OK, idempotent retry);
   different hash → `conflict`
6. `seq` = head + 1, and `prev_hash` = head hash

**Batch policy:** the valid prefix is committed. At the first bad record the
server stops; the response lists it as `rejected` with a `code`, later records as
`not_processed`, and returns `last_ack_seq` / `last_hash` so the member can resume.
Max 100 records per batch.

Rejection codes: `wrong_agent` `bad_record` `bad_header` `bad_version` `bad_type`
`bad_ts` `genesis` `bad_payload` `payload_too_large` `payload_hash` `hash` `sig`
`type_forbidden` `future_ts` `conflict` `gap` `chain`.

## 5. What a member must persist

Its key file, and — if it signs while offline — its outbox of signed, unacked
records. After a restart it calls `GET /api/records/head` (or reads the head from
the token response) and continues from there. Never re-sign a seq the server
may already hold with different content: that is a `conflict`, and it shows on
the Integrity page.

## 6. Storage and verification guarantees

The server stores the exact canonical header bytes (`header_raw`) and payload
bytes, so `GET /api/records` returns each record exactly as signed — anyone with
the member's public key can re-verify it. Integrity checks recompute everything
from those bytes and also confirm the queryable columns agree with them.
