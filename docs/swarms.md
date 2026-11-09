# Swarms, hubs and relays

A **swarm** is a group of members with one **hub**. At home that's
**echo-base**: the hub host is the hub, Z3-R0 and Vector are members, and the Pi
Zero is an observer. In the reference deployment (`examples/echo-base`) the hub
host is a Jetson Orin Nano. A member is in at most one swarm at a time; a member
in no swarm is a **free agent** (cloud agents, the Mac's coding agents) and talks
to LTMS directly.

Nothing here changes the record format. Swarm membership is something the
**server** records because you set it; no record carries a swarm ID, and no
member can claim one.

## Keys

| Key | Lives | Used for |
|---|---|---|
| Each member's key | on that member (UNO Q, the hub host, Pi Zero, …) | signing its own records, logging in |
| `hub-host`'s key | on the hub host | the hub's own records, and logging in to relay |
| **Swarm root key** | **offline, with you** (password manager or USB stick, plus a backup). Made in the dashboard or with `scripts/swarm_key.py` | appointing the hub. Its fingerprint is the swarm ID |

The root key never goes on the hub host or the server. Replacing the hub host
doesn't change the swarm: appoint the new hub, and the old one loses relay
rights at once.

## Roles

| Role | Example | Notes |
|---|---|---|
| `hub` | the hub host | One per swarm. Only through a root-key-signed appointment. Gets `relay` |
| `member` | Z3-R0, Vector | Set with `swarm-add` |
| `observer` | Pi Zero, a display | Set with `swarm-add --role observer`; give it `recall:scoped` |

Roles don't grant anything except the hub's `relay`. Permissions are still set
per member when you approve it, and only permissions are enforced.

## Setting up echo-base

### In the dashboard (any machine)

1. **Swarms → Create a swarm:** enter the name, tick "home swarm" if it is one,
   then **Generate root key**. The browser makes the key and sends only the
   public key. The key is shown **once**, as
   `ltms-swarm-key-v1:<base64url seed>:<8-hex checksum>`: copy it into your
   password manager or download the `.txt`, then tick "I've saved the key".
   LTMS never stores it and can't show it again.
2. **Add members** on the swarm's card.
3. **Appoint hub:** choose the hub from the swarm's members, paste the root key
   (the dashboard string or the CLI's JSON key file both work) and **Sign and
   appoint**. The browser checks the key belongs to this swarm, signs the
   appointment, and sends only the statement and signature. The key field is
   cleared straight away.

The dashboard is served with a strict Content-Security-Policy (only its own
bundled scripts run) because root keys pass through it. Keys made in the
browser and with the CLI are interchangeable; `server/tests/test_swarm_key_vectors.py`
and `dashboard/src/lib/swarmKey.test.js` pin the exact bytes both produce.

### With the command line

On the **Mac** (keep the key file on the USB stick):

```bash
python -m scripts.swarm_key create echo-base --out /Volumes/KEY/echo-base.swarm.json
# prints the public key, the swarm ID, and the command for the server
```

On the **server**, as root (`manage.sh` = `bash <app-dir>/deploy/manage.sh`):

```bash
manage.sh swarm-create echo-base --public-key <hex from above> --home
manage.sh swarm-add z3-r0  --swarm echo-base
manage.sh swarm-add vector --swarm echo-base
manage.sh swarm-add pi-zero --swarm echo-base --role observer
manage.sh swarm-list
```

Appoint the hub. On the Mac, sign; the hub's key is on the Members page
(fingerprint) or in `manage.sh list-members`:

```bash
python -m scripts.swarm_key appoint-hub --key /Volumes/KEY/echo-base.swarm.json \
    --hub hub-host --hub-key <hub-host public key> > appoint.json
```

Copy `appoint.json` to the server and apply it (it isn't secret; it's a signed
statement, valid for 30 days):

```bash
cat appoint.json | manage.sh swarm-appoint-hub --swarm echo-base -
```

Other commands: `swarm-remove <member>` (free agent), `swarm-add` again to move
a member, `swarm-revoke <swarm> --yes` (every member becomes a free agent, the
hub stops relaying). Every change is in the audit log.

## Relaying (for the hub host's replicator)

The hub logs in with its own key and posts other members' records **exactly as
they were signed** to `POST /api/records`, the same endpoint and batch rules as
a member's own records. One batch may mix authors, including the hub's own
records. In Python, `LTMSClient.relay(records)`.

- Each record is verified against its **author's** key and chain. A changed
  byte fails `hash` or `sig`; a record re-signed by the hub fails `sig`.
- Results list `agent` per record, and `heads` gives each author's
  `last_ack_seq` / `last_hash`. The batch stops at the first rejection, as usual;
  resending is idempotent (`duplicate`).
- `GET /api/records/head?agent=z3-r0` (`LTMSClient.head_of`) returns a member's
  head, for the hub of that member's swarm only, so the replicator can resync.
- A member can still post directly (for example, while the hub host is off).
  Its chain is the same either way.

## Provenance

Every stored record gets a `record_provenance` row: the author's swarm at that
moment (null for free agents) and the relay that delivered it (null when sent
directly, including the hub's own records). Moving a member later doesn't
change the provenance of what it already sent.
