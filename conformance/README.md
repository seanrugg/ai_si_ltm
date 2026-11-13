# Harness conformance

Checks that a harness (the client that connects a device or agent to LTMS) behaves
correctly against a live LTMS, so anyone can build one for their own board or
language and prove it works (#298).

## Run it

Against a **test** instance (the cases write records), with a member enrolled and
approved with `contribute` and `recall`:

```bash
python conformance/run.py --server https://ltms-test.example.org --key member.json --state-dir /tmp/conf
```

One line per case, `PASS` or `FAIL` with the reason; exit status 1 on any failure.

## Testing your own harness

Write a small Python adapter that drives your harness (over a serial port, a
subprocess, HTTP to your device, ...) with the methods described in
[`adapter.py`](adapter.py), then:

```bash
python conformance/run.py --server ... --key ... --state-dir ... --adapter my_adapter:MyHarness
```

`CliAgentAdapter` in `adapter.py` is the reference: it drives this repository's own
`cli_agent` harness.

## Cases

| Case | Checks |
|---|---|
| `signed_records_verify` | every record verifies against the frozen contract (`ltms_contract`) |
| `chain_continues_across_restart` | `seq` and `prev_hash` continue after a restart |
| `flush_reaches_ltms_and_heads_match` | LTMS's head equals the harness's last record |
| `resend_is_idempotent` | flushing again, or after a restart, changes nothing |
| `refuses_system_records` | `system` records (server-only) are never produced |
| `stored_chain_is_unbroken` | LTMS holds one unbroken, verifying chain from genesis |

The contract's own test vectors (`ltms_contract/vectors.json`) are read-only here.
MQTT topic checks join once topics move to the swarm topic ID (#293).
