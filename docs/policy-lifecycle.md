# Policy lifecycle

A `policy` record's lifecycle is expressed with existing header fields only
(`tags` and `refs`): there is no contract change and no new record type.

## Tags

- `proposal`: the record is a **proposal** -- listed for comment, never served
  as a rule. Context packs return proposals in their own `proposals` section,
  never under `policies`.
- absent: the record is **enacted** -- a rule in force, unless retired (below).

## Refs

Refs use `"agent:seq"` string match and are resolved against records the caller
can see (refs that point at missing records are ignored).

- `refs: {"enacts": "<agent>:<seq>"}` -- an enacted policy cites what it enacts
  (informational; no behavior change).
- `refs: {"supersedes": "<agent>:<seq>"}` -- the referenced enacted policy is
  retired, replaced by this one.
- `refs: {"revokes": "<agent>:<seq>"}` -- the referenced enacted policy is
  retired with no replacement.

Only an enacted policy can retire another; a proposal's `supersedes`/`revokes`
refs do nothing. Only a member with the `policy` permission can write these
(enforced at ingest).

## Acceptance and objection

A member's acceptance or objection to a policy is its own record (for example a
`decision`) with `refs: {"policy": "<agent>:<seq>"}`. This issue documents the
convention only; it does not change decision handling.

## Context pack

A member's context pack serves:

- `policies`: the newest 5 **in-force** policies -- enacted, not tagged
  `proposal`, and not the target of any in-scope `supersedes`/`revokes` ref
  from an enacted policy.
- `proposals`: the newest 3 proposals, each carrying `status: "proposed"`.

Both sections respect the caller's scope and the overall 25-record cap, in
priority order outcomes → comms → lessons → policies → proposals (proposals
last).

## Admin endpoint

`GET /api/admin/policies?status=in_force|proposed|retired|all`

Returns every policy with `status` added; retired items also carry
`retired_by`, the `"agent:seq"` of the enacted policy that superseded or
revoked them.

## Status

| Status | Meaning |
|--------|---------|
| `proposed` | Tagged `proposal`; listed for comment, not a rule. |
| `in_force` | Enacted and not retired. |
| `retired` | Enacted, but targeted by an enacted policy's `supersedes` or `revokes`. |
