# Member spec sheet (#332)

Each member can report what it is: hardware, language models and software. An
admin sees it on the member's profile and can correct any field. It's member
metadata, not a record: it isn't signed, chained, or sent to an LRS.

## Reporting

```python
LTMSClient(server, key_path=...).report_specs({
    "hardware": {"board": "", "cpu": "", "cores": 6, "ram_mib": 7619, "storage_gib": 64,
                 "accelerators": ["GPU"], "sensors": ["camera"], "actuators": [], "power": "25W"},
    "models": [{"name": "qwen2.5:1.5b", "params": "1.5B", "quant": "q4_K_M", "runtime": "ollama",
                "where": "onboard", "role": "chat"}],
    "software": {"os": "Ubuntu 22.04", "harness": "ltms-harness", "harness_version": "0.1"},
    "notes": "free text, up to 500 characters",
})
```

`PUT /api/members/me/specs` with the same body. Every field is optional;
unknown keys are refused (422). Text is at most 64 characters (`ping_reason`: 200), lists at most 16
items, and the whole sheet at most 8 KiB (413).

A report replaces **the sections it sends**. A section it leaves out keeps what
was reported before, and a section sent as `null` (e.g. `{"models": null}`) is
cleared. So a robot that reports its hardware on connect doesn't erase the
models its cognition report wrote at start. An identical re-report changes
nothing, so a harness can report on every start without filling the audit log.

### How it answers a ping (#384)

`software.ping` says how the member can answer an admin ping (see
[swarms.md](swarms.md#heartbeats-and-ping-356)):

- `polling`: it runs all the time and checks for pings itself or through its hub.
- `on_use`: it runs only when someone uses it, such as a chat assistant. It
  answers with its next record, which may be hours away.
- `none`: it can't answer. Give the reason in `software.ping_reason` (up to 200
  characters); the dashboard shows it and turns Ping off. Use this, for example,
  for a member whose key the fleet holds and for which an admin submits.

An admin can set or correct either field like any other. When neither the
member nor an admin has set it, LTMS infers it: a member with regular heartbeats
is `polling`, an `assistant` without them is `on_use`, anything else is unknown.

`PUT /api/members/me/cognition` (where the AI runs, #185) also writes its models
into the sheet; cognition still owns the on/off-board location.

## Admin

- `GET /api/admin/members/{id}/specs`: each section with `value` (merged),
  `source` (`declared`, `admin`, or `mixed`), `admin_fields`, `declared` (what
  the device said) and `reported_at`.
- `PATCH /api/admin/members/{id}/specs`: set override fields, or clear them with
  `null` (`{"hardware": {"ram_mib": null}}`, or `{"models": null}` for a whole
  section). What the device reported is never edited.
- `GET /api/admin/members/{id}/specs/history`: `specs_change` audit entries,
  newest first, with what changed and who changed it.
