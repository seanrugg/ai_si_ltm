# echo-base deployment runbook

Everything needed to run echo-base for real: the Mosquitto broker with per-member
ACL, systemd units for the hub (Jetson) and a member (Pi Zero W / UNO Q), and the
steps to stand them up. Config and docs only — no Python changes.

## 1. What runs where

| Machine | Runs | Notes |
|---|---|---|
| Jetson | Mosquitto broker, echo-base hub, Vector's mind | The broker lives here; members connect to it. |
| Robots / test nodes (Pi Zero W, UNO Q) | echo-base member | Publishes signed heartbeats, reads its own acks. |
| LTMS server | LTMS (long-term memory server) | Wherever `LTMS_SERVER` points; not deployed yet (see below). |

## 2. Prerequisite: a reachable LTMS server

The hub relays records to LTMS over HTTP, so `LTMS_SERVER` must point at a running
LTMS instance before records can be acknowledged. LTMS isn't deployed yet: until it
is, the hub logs connection errors and keeps records queued in its persistent
outbox, and the member's heartbeats resend until the hub picks them up.

## 3. Broker on the Jetson

Mosquitto 2.x denies everything by default, so we configure users, passwords and the
ACL explicitly.

1. Install only the two needed packages (this is a production box, so no `apt upgrade`):

   ```
   sudo apt install mosquitto mosquitto-clients
   ```

2. Copy the broker config and ACL into place:

   ```
   sudo cp deploy/echo-base/mosquitto-echo-base.conf /etc/mosquitto/conf.d/
   sudo cp deploy/echo-base/echo-base.acl /etc/mosquitto/echo-base.acl
   ```

3. Create the password file. The first user (`jetson-hub`) needs `-c`; then add each
   member without `-c`:

   ```
   sudo mosquitto_passwd -c /etc/mosquitto/echo-base.passwd jetson-hub
   sudo mosquitto_passwd /etc/mosquitto/echo-base.passwd pi-zero
   ```

4. Restart the broker:

   ```
   sudo systemctl restart mosquitto
   ```

**MQTT usernames must equal LTMS member names**, or the ACL's `%u` pattern won't
match and the member will be denied its own topics.

## 4. Hub (Jetson)

Runs as `python -m cli_agent.hub_main` under systemd as user `echobase`.

1. Create the service user and directories (`/etc/echo-base` holds the env file at
   mode 0700 so only `echobase` can read it):

   ```
   sudo useradd --system --create-home --shell /usr/bin/nologin echobase
   sudo install -d -o echobase -g echobase -m 0700 /var/lib/echo-base /etc/echo-base
   ```

2. Clone the repo and set up the venv:

   ```
   sudo -u echobase git clone <repo> /opt/ai_si_ltm
   sudo -u echobase python3 -m venv /opt/ai_si_ltm/.venv
   sudo -u echobase /opt/ai_si_ltm/.venv/bin/pip install -e '.[hub]'
   ```

3. Generate and enroll the hub key and appoint it with the offline swarm key — see
   `docs/swarms.md` (not repeated here). The key file is mode 0600 owned by `echobase`.

4. Copy `deploy/echo-base/hub.env.example` to `/etc/echo-base/hub.env`, fill in the
   real values (placeholders are only placeholders), and set mode 0600:

   ```
   sudo cp deploy/echo-base/hub.env.example /etc/echo-base/hub.env
   sudo chown echobase:echobase /etc/echo-base/hub.env
   sudo chmod 0600 /etc/echo-base/hub.env
   ```

5. Install and enable the unit, then watch the log:

   ```
   sudo cp deploy/echo-base/echo-base-hub.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now echo-base-hub
   journalctl -u echo-base-hub -f
   ```

## 5. A member (Pi Zero W or UNO Q)

Same shape as the hub, but the broker is on the Jetson so there is no `mosquitto`
dependency in the unit.

1. Create the user and directories, clone, venv, and install with `'.[member]'` instead of `'.[hub]'`, exactly as in the hub steps.

2. Generate and enroll the member key (its filename is the member name, e.g. `pi-zero`), mode 0600 owned by `echobase`, and appoint it per `docs/swarms.md`.

3. Copy `deploy/echo-base/member.env.example` to `/etc/echo-base/member.env`, set `MQTT_HOST` to the Jetson's address, fill in the real values, mode 0600.

4. Install and enable the member unit:

   ```
   sudo cp deploy/echo-base/echo-base-member.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now echo-base-member
   ```

On a Pi Zero W (ARMv6), `pynacl` may need a prebuilt wheel from piwheels or a long
source build.

## 6. Check it works

Subscribe to all acks on the Jetson:

```
mosquitto_sub -h <jetson> -u jetson-hub -P ... -t 'echo-base/+/acks' -v
```

Acks should appear as members run. The member's heartbeats show up as signed records
in the LTMS dashboard (at whatever `LTMS_SERVER` points to).

## 7. Failure drill

Stop the hub for 2 minutes while the member keeps running, then start it again and
confirm no gap: the Integrity page is clean and the member's outbox is pruned on the
acks. This is the scenario the persistent session and periodic resend exist for.
