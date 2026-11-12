# ltms-harness

The client side of LTMS, packaged on its own (#294): what a hub, a member or a
test agent needs to talk to an LTMS server, **without** the server's dependencies
(FastAPI, SQLAlchemy, asyncpg, alembic, …).

## What's in it

| Package | What |
|---|---|
| `ltms_contract` | the frozen record contract: canonical JSON, Ed25519 keys, building and verifying records |
| `cli_agent` | `LTMSClient` (HTTPS), the outbox, the MQTT hub (`hub_main`) and member (`member_main`), and the `cli_agent` CLI |

They are the repository's own directories, not a copy: building this package
takes them from `../ltms_contract` and `../cli_agent`.

Dependencies: `pynacl`, `httpx`. The `mqtt` extra adds `paho-mqtt` (needed for a hub or an MQTT member).

## Install

From a checkout of the repository:

```bash
pip install ./harness            # client and CLI
pip install "./harness[mqtt]"    # plus MQTT, for a hub or member
```

Or build a wheel to copy to a device that can't reach GitHub:

```bash
pip wheel ./harness -w dist/     # dist/ltms_harness-0.1.0-py3-none-any.whl
```

## Use

```bash
python -m cli_agent genkey --name <member> --out <key file>
python -m cli_agent pubkey --key <key file>          # safe to show: name and public key only
python -m cli_agent enroll --key <key file> --server https://ltms.example.org --kind robot --permissions contribute
python -m cli_agent.member_main                      # or: ltms-member   (configured by environment, see examples/)
python -m cli_agent.hub_main                         # or: ltms-hub
```

Configuration for the hub and member is in `examples/echo-base/deploy/*.env.example`.

## Rules

- Nothing in `ltms_contract` or `cli_agent` may import the server or a server-only
  package. `server/tests/test_harness_package.py` enforces it.
- `ltms_contract` is frozen (see `CONTRACT.md`); packaging doesn't change it.
