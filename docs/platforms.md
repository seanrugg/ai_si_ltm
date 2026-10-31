# Platforms

LTMS runs on any Linux/macOS host with Python 3.11+; members run anywhere Python and MQTT do.

Status: **CI** = covered by the test suite; **planned** = not yet run on that platform. Nothing here is a production claim yet.

| Role | Platform | Status | Notes |
|---|---|---|---|
| LTMS server | Ubuntu 24.04 VPS (Plesk) | planned | see `PLESK_DEPLOYMENT_PLAN.md`; not deployed yet |
| LTMS server | Linux / macOS, Python 3.11–3.14, SQLite | CI | the test suite's setup |
| LTMS server | Docker Compose | planned | |
| Hub host (Mosquitto + hub) | NVIDIA Jetson Orin Nano, JetPack 6.x | planned | |
| Member | Arduino UNO Q (Debian side) | planned | |
| Member | Raspberry Pi Zero W (ARMv6) | planned | `pynacl` may need a piwheels wheel |
| Member (via adapter) | Anki Vector with wire-pod | planned | adapter not yet written |
| Agent member | MCP clients via `ltms_mcp` | CI | |
