# Hub machine health (#349)

A hub can report on the machine it runs on: temperatures, CPU and GPU load,
memory, disk, power mode, over-current throttle counters, and services you
name. It's read-only, needs no root, and uses only the standard library.

## Records

| When | Record | Tags |
|---|---|---|
| every `HUB_HEALTH_SECONDS` (300) | `perception`, payload `{"sensor": "jetson-health", ...}` with min/max/last over the window | `telemetry, internal, health` |
| an `oc*` counter rises, or a zone reaches 85 °C | `event` `throttle: oc3 +2`, at most one per 5 minutes | `internal, health, throttle` |

Both are self-reports (see [sensing.md](sensing.md)): they describe the hub,
not the world, so profile counts keep them apart from sensing. The member
profile's Senses tab shows the newest reading under **Machine health**.

Payload fields (each only when the machine exposes it): `temp_c` per thermal
zone (named by the zone's `type`), `cpu_pct` (from `/proc/stat`), `gpu_load_pct`,
`ram_used_mib`, `ram_total_mib`, `disk_used_pct`, `power_mode` (`nvpmodel -q`),
`oc_events` (`/sys/class/hwmon/*/oc*_event_cnt`), `services`, `throttled`.
A counter that drops (a reboot) is a reset, not an event.

## Settings (hub.env)

| Setting | Default | |
|---|---|---|
| `HUB_HEALTH` | `auto` | on for a Jetson (`/etc/nv_tegra_release`), `1` always, `0` never |
| `HUB_HEALTH_SECONDS` | `300` | one health record per window |
| `HUB_HEALTH_SAMPLE_SECONDS` | `30` | how often the machine is read |
| `HUB_HEALTH_SERVICES` | empty | `name=http(s)://url` (online if it answers) or `name=systemd:unit` |
| `HUB_HEALTH_STATE` | `hub-health.json` beside the outbox | newest reading, world-readable |

## From other local programs

```bash
python3 cli_agent/hub_health.py latest --state <HUB_HEALTH_STATE>           # JSON
python3 cli_agent/hub_health.py latest --state <HUB_HEALTH_STATE> --speak   # one sentence
python3 cli_agent/hub_health.py probe                                       # what this machine exposes
```

A voice assistant can speak `--speak`'s sentence, so what it says matches what
LTMS recorded. `latest` fails (exit 1) when the reading is missing or older
than 10 minutes, so a wrapper can fall back to reading the machine itself.
`examples/echo-base/deploy/vector-status.sh` does that for a wire-pod intent.
