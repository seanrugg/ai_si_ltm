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
statement). It must be **applied within 30 days of signing**; once applied it
stays in force until another hub is appointed or the swarm is revoked:

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

## MQTT topics

### Swarm topic ID (#292)

A device addresses its swarm on MQTT by the swarm's **topic ID**, never by its
name, so renaming a swarm changes nothing on any device. A member or hub gets
it from LTMS:

```
GET /api/swarm   →  {"swarm": {"id": "<64 hex>", "topic_id": "sw-1a2b3c4d", "name": "<current name>", "role": "member"}}
```

`{"swarm": null}` for a free agent. In Python: `LTMSClient.swarm()`.

The topic ID is `sw-` plus the first 8 hex characters of the permanent swarm ID.
If an **older** swarm (in any status) already starts with the same 8, the newer
swarm uses 12, then 16, ... characters until no older swarm shares the prefix.
The oldest swarm keeps the short form, so a swarm's topic ID never changes after
it is created.


### Topics

`<swarm>` below is the swarm's **topic ID** (#293). Hubs and members find it from `SWARM_TOPIC_ID`, or from LTMS at start; the old `SWARM=<name>` still works, with a warning. During a migration the hub serves both prefixes (`SWARM_LEGACY_NAME`): see `examples/echo-base/README.md`.

| Topic | Who publishes | What |
|---|---|---|
| `<swarm>/<agent>/records` | the member | one signed record per message |
| `<swarm>/<agent>/acks` | the hub (retained) | LTMS's acknowledged head for that member |
| `<swarm>/<agent>/hello` | the member, on every connect | `{"agent", "network": [{"ip", "interface"}]}`: its LAN addresses (at most 8) |
| `<swarm>/<agent>/say` | the member | a **swarm message**: one signed `communication` record (#300) |

The hub reports each hello's addresses to LTMS on its next tick
(`PUT /api/relay/network/<agent>`), where they show as reported and unverified.
The broker's ACL should let each member publish only under its own
`<swarm>/<agent>/` topics, let the hub subscribe to `<swarm>/+/records`,
`<swarm>/+/hello` and `<swarm>/+/say`, and let every member read `<swarm>/+/say`
(`examples/echo-base/deploy/echo-base.acl`).

### Swarm messages (#300)

Members talk to each other on the bus, and LTMS sees all of it: a swarm message
**is** an LTMS record, so it's signed by its author (the hub can't invent or
change it) and lands in the author's chain like anything else.

- Type `communication`, tag `swarm-message`, the text in `summary` (≤ 500
  characters), recipients in `peers` (omit for the whole swarm), and
  `refs: {"reply_to": "<agent>:<seq>"}` when it answers another message.
  Anything bigger goes in `payload`. No contract change.
- Published on the author's `say` topic. Every member subscribed to
  `<swarm>/+/say` hears it; a member may ignore messages not addressed to it.
- The hub forwards it to LTMS exactly like a record (same outbox, same relay),
  and from there it reaches the LRS as `communicated`. Until LTMS acks it, the
  author keeps it in its outbox and resends it as a record.
- **Hard cap:** the hub forwards at most `HUB_MAX_MSGS_PER_SECOND` (default 10)
  messages per member per second. Extras are dropped, logged once a minute per
  member, and counted in the hub's heartbeat as `dropped_messages`. Records on
  `records` aren't capped by this.

In Python: `MemberLink.send_message(text, to=None, reply_to=None, payload=None)`;
`member_main` hands received messages to a handler (by default it logs them).

### Busy members: summaries to the LRS (#301)

LTMS keeps every swarm message. Only what goes to an LRS changes: a member that
sent more than `LTMS_LRS_MSG_SUMMARY_PER_HOUR` (60) swarm messages in each of
the last `LTMS_LRS_MSG_SUMMARY_SUSTAIN_HOURS` (3) clock hours is in *summary
mode*. Its swarm messages then go to the LRS as one `communicated` statement
per `LTMS_LRS_MSG_SUMMARY_WINDOW_MINUTES` (5) window, with `count`, the distinct
`peers`, the `first` and `last` record refs and the window's start and end
(`result.extensions`, `…/message-summary/*`). It's counting, not summarising
text. One full hour under the limit ends it. Entering and leaving are audited
(`lrs_summary_on`, `lrs_summary_off`). The forwarder waits for a window to close
before sending it, so it can run up to one window behind.

## Heartbeats and ping (#356)

Hubs and members sign a heartbeat (a self-report: `perception` tagged
`telemetry,internal`) on start and then every `HUB_HEARTBEAT_SECONDS` /
`HEARTBEAT_SECONDS`, by default **twice a day** (43200 s), so they don't flood
LTMS or the LRS. A hub's relay traffic already keeps its *last seen* current.

To check a member now, press **Ping** on its profile. LTMS records the request;
nothing is pushed:

1. The member's hub checks `GET /api/members/me/pings` every
   `PING_POLL_SECONDS` (30). It answers its own ping with a heartbeat, and
   publishes `<topic_id>/<agent>/ping` (empty, not retained) for members on MQTT.
   A member with `LTMS_SERVER` set also checks for itself.
2. The member answers with a heartbeat whose payload has `"ping": true`.
3. The profile shows *waiting* → *delivered* → *answered in N s* (the first
   record LTMS received from it after the request).

The broker's ACL needs `topic write <topic_id>/+/ping` for the hub and
`pattern read <topic_id>/%u/ping` for members (see `examples/echo-base/deploy/echo-base.acl`).
Members keep resending unacked records every `RESEND_SECONDS` (60), separately
from the heartbeat.

## Directives and downlink (#303, #304, #305)

An admin issues a **directive** to one swarm or all swarms. LTMS signs it with the
fleet key (`GET /api/fleet-key` gives the public key) so members can check where
it came from. Each swarm's hub pulls `GET /api/relay/downlink` every
`DOWNLINK_SECONDS` (30) and publishes:

| What | Topic | Retained |
|---|---|---|
| an open directive | `<topic_id>/fleet/<directive id>` | yes, until cancelled or expired |
| a cancel | `<topic_id>/fleet` (and an empty retained message on the directive's topic) | no |
| a message to the swarm from a member outside it (`peers: ["swarm:<topic_id>"]`, needs `speak:swarm`) | `<topic_id>/<sender>/say`, unchanged | no |

The hub saves its cursor (`hub-downlink.json` beside its state) only after
publishing, so a crash re-delivers instead of losing. It doesn't forward
downlinked messages back to LTMS. The ACL needs `topic write <topic_id>/fleet/#`
and `topic write <topic_id>/+/say` for the hub, and `pattern read <topic_id>/fleet/#`
for members.

### Acknowledging directives (#306)

A member subscribes to `<topic_id>/fleet` and `<topic_id>/fleet/+`, checks each
directive's signature with the fleet's public key (fetched from
`GET /api/fleet-key` once and kept in `fleet-key.pub` beside its state, or
`FLEET_PUBLIC_KEY`), and records as itself a `communication` tagged
`directive-ack`: first `{"directive": id, "status": "received"}`, then
`accepted`, or `declined` with a `reason`. **Directives are followed by
default**: behaviour code replaces the default decision, and a decline needs a
reason (safety envelope, permissions, or the charter). An invalid signature is
ignored and recorded once as an `event` tagged `directive-invalid`. Handled ids
are kept in `directives.json`, so a retained directive isn't acknowledged twice.
The admin's directive list shows, per directive, how many members it was meant
for and how many received, accepted and declined it, with each decline's reason.
## Swarm leader (#308)

A swarm can have one **leader**: any active member (a cloud agent may lead), but
not its hub. Like the hub, it's appointed with the swarm's root key:

```bash
python -m scripts.swarm_key appoint-leader --key /Volumes/KEY/<swarm>.swarm.json \
    --leader <member> --leader-key <64 hex> > appoint-leader.json
# then POST it to /api/admin/swarms/<swarm>/leader (or use the dashboard)
```

The appointment must be applied within 30 days of signing. It gives the leader
scoped recall of the swarm's records (every type except `system`), and removing
or replacing the leader takes back exactly what the appointment added. A member
that already has full recall keeps it unchanged. `GET /api/swarm` shows the
leader's name and public key, so members can verify its directives (#310).

### Leader directives (#310)

The current leader issues directives to its own swarm through LTMS, signed with
**its own key** (same statement and `ltms-directive-v1\n` prefix as the fleet's,
`issued_by` = the leader): `LTMSClient.issue_directive(text, payload=None,
expires_in=None)`, i.e. `POST /api/directives` with `{statement, signature}`.
LTMS checks the issuer is the current leader and the scope is its own swarm
(403 otherwise), then the downlink delivers it like any directive. Members
verify it with the leader's key from `GET /api/swarm` (`leader.public_key`).
`GET /api/swarm` also says which swarm a member leads (`leads`).

When the leader changes, the old leader's open directives become `inherited`.
The new leader lists them (`GET /api/directives?inherited=1`) and must
`POST /api/directives/{id}/confirm` or `/cancel` each one; both are audited.
Unconfirmed ones stay open. The fleet outranks the leader: an admin can cancel
any directive, and a leader can't cancel the fleet's.
