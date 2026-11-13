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
unknown keys are refused (422). Text is at most 64 characters, lists at most 16
items, and the whole sheet at most 8 KiB (413). A report replaces the previous
one. An identical re-report changes nothing, so a harness can report on every
start without filling the audit log.

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
