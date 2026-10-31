# Platforms

LTMS runs on any Linux/macOS host with Python 3.11+; members run anywhere Python and MQTT do.

| Role | Platform | Status | Notes |
|---|---|---|---|
| LTMS server | Ubuntu 24.04 VPS (Plesk) | tested | see `PLESK_DEPLOYMENT_PLAN.md` |
| LTMS server | Docker Compose | planned | |
| Hub host (Mosquitto + hub) | NVIDIA Jetson Orin Nano, JetPack 6.x | planned | |
| Member | Arduino UNO Q (Debian side) | planned | |
| Member | Raspberry Pi Zero W (ARMv6) | planned | `pynacl` may need a piwheels wheel |
| Member (via adapter) | Anki Vector with wire-pod | planned | adapter not yet written |
| Agent member | MCP clients via `ltms_mcp` | tested | |
