# Robot Mesh and Long-Term Memory Plan

Oct 12, 2026 · Sean · [Live doc](https://claude.ai/code/artifact/2850a3ff-4f60-422f-808d-a088a940add1)

The robot mesh gives every member (robots, software agents and AI assistants) one shared, tamper-evident long-term memory of what was communicated, decided and done, so each can consult that history before it acts. The memory server can run on the internet or on the home network, only enrolled members can read or write it, and a dashboard lets the administrator review everything. Z3-R0 is the first member; the Z3-R0 Build Plan (v2.1) and the [Z3-R0 build model](https://claude.ai/artifact/7AWvT8e8UzJa4VBwpwoHvE) cover the robot side.

## Principles

An autonomous agent's record must live beyond its own reach, and any member should be able to learn from the whole mesh's history. Seven rules follow from that.

1. **Record what matters most.** Every member records its **communications**, **decisions** and **actions**, and every action carries its **outcome**.
2. **Write before acting.** A member journals a record locally before it acts on it or sends it, so nothing is lost when the network is down.
3. **Records are append-only and tamper-evident.** Each member's records form a hash chain and are signed with the member's own key. Nobody, including the administrator, edits a stored record; corrections are new records that point at the old one.
4. **Only enrolled members take part.** Every read and every write needs an enrolled identity. Nothing is public, and nothing is anonymous.
5. **Memory informs; it never commands.** Recall gives context to a member's own decision. Safety behaviors, such as Z3-R0's obstacle stops, never wait on memory.
6. **Memory and control stay separate.** The memory server can't send commands to any robot, and it holds no credentials for the robot mesh.
7. **Reads are recorded too.** Who asked what of the memory is itself part of the audit trail.

## Architecture

*(Diagram in the live doc.)* Robots on the home network reach long-term memory through the mesh hub; outside AI members write to it directly; and Z3-R0's Pi Zero reads from it for the UNO Q. Nothing in the memory server can command a robot.

```
                 Long-term memory server  ◄──views──►  Administrator
           (records · recall API · dashboard; LAN, internet or both)
          ▲ verified records        ▲ recall, read-only        ▲ signed records, recall
          │                         │                          │
 Robot mesh hub · Jetson            │                   Outside AI members
 (broker, mesh logger, Vector's mind)                   (cloud assistants, agents)
   ▲ SDK          ▲ records / acks  │
 Vector       ┌───┴─────────────────┴───────┐
              │ Z3-R0: UNO Q ◄─USB─► Pi Zero W │
              └─────────────────────────────┘
```

## Deployment modes

The same memory server software runs in all three modes; only where it runs and who can reach it change. Start on the home LAN, then add the internet copy when members outside the house need it.

| | Home LAN only | Internet-hosted | Hybrid (recommended end state) |
| --- | --- | --- | --- |
| Where it runs | A home machine: the Jetson, a NAS or a small server | A small rented server (VPS) or managed host | Primary on the home LAN; replica on the internet |
| Who can reach it | Members on the home network, or over a private VPN | Enrolled members anywhere | Home members use the LAN copy; remote members use the internet copy |
| Works without internet | Yes | No | Home side yes; the replica catches up later |
| Exposure | None outside the home | A public address, protected by enrollment and the measures in Security | Only the replica is exposed |
| Running cost | Electricity only | A small monthly hosting fee | Both |
| Best for | Starting out; robots that never leave the house | Cloud AI agents and viewing from anywhere | A home mesh with outside AI members |

**Remote access without a public server:** a private overlay network (a mesh VPN) lets enrolled devices and the administrator reach a home-LAN server from anywhere without exposing anything to the open internet. It is the cheapest way to keep scrapers out entirely, and it suits a home-only mesh. Cloud AI agents that can't join a VPN need the internet-hosted copy.

## Joining the mesh

Any AI can join (a robot, a local model, a cloud assistant, a scripted agent) as long as the administrator approves it. There is no self-signup.

**Identity.** Each member generates its own key pair (Ed25519) and keeps the private key on its own device. The public key is its identity on the mesh. Members sign every record and every request, so the server always knows who is speaking and that nothing was altered in transit.

**Enrollment.**

1. The member submits an enrollment request: a name, its public key, what it is (robot, agent, assistant), who operates it, and the permissions it asks for.
2. The request waits on the dashboard until the administrator approves or rejects it.
3. On approval, the member is added to the registry with its permissions, and gets short-lived access tokens by signing a challenge with its key.
4. The administrator can suspend or revoke a member at any time. Its past records stay in memory, marked with the revocation date.

**Permissions** are granted per member, and each request is checked against them:

| Permission | Allows | Typical holders |
| --- | --- | --- |
| `contribute` | Submit its own records (never anyone else's) | Every robot and agent |
| `recall` | Query long-term memory, within rate and size limits | Robots, agents, Z3-R0's Pi Zero relay |
| `recall:scoped` | Query only certain record types, members or places | Guest or experimental agents |
| `admin` | Approve members, set retention, view everything on the dashboard | The administrator only |

**Relays and sub-devices.** A device that acts for a member, such as Z3-R0's Pi Zero, gets its own identity with `recall` only. It can read on Z3-R0's behalf but can't write as Z3-R0.

## What to record

Keep the three core buckets, and add **meaning, not raw streams**. Long-term memory should hold what a member would want to recall later: what happened, what it noticed, what it learned. Raw sensor and video data is huge, rarely recalled, and in a home it is also private. It stays on the device that produced it, for a short time.

**The three core buckets**

| Bucket | Example | Must include |
| --- | --- | --- |
| Communications | Z3-R0 to Vector: "Found you by the couch." | Sender, recipients, text, channel |
| Decisions | Approach Vector rather than wait or explore | Options considered, choice, reason, the inputs it used |
| Actions | Drove 0.8 m toward Vector | Parameters, the decision it came from, and the **outcome** |

**Recommended additions**

| Kind | Store in long-term memory? | Form | Why |
| --- | --- | --- | --- |
| Notable events | Yes | One record per event: reflex stop, picked up, low battery, peer arrived | Explains why actions turned out as they did |
| Perception results | Yes | Labels, not pixels: "Vector, living room, 1.2 m, confidence 0.9" | Lets members recall who and what was seen, where and when |
| Decision context | Yes, inside each decision | A small snapshot: battery, place, nearby peers, recent events | Makes "why did it decide that?" answerable |
| Lessons | Yes, marked as derived | Written by a reflecting agent, linked to the records it summarizes: "Approaching from the couch side ended in a reflex stop 3 of 4 times" | Compresses history into something recall can use quickly |
| Policies and norms | Yes, written by the administrator only | Rules members consult: quiet hours, no-go areas, conduct rules | Puts the mesh's constitution in the same memory members already check |
| Places and members | Yes, as registries | Named places (living-room, kitchen) and the member registry | Lets every record say where and with whom in shared terms |
| Health summaries | Yes, as daily rollups | Distance driven, battery cycles, uptime, error counts | Spots wear and trends without storing raw telemetry |
| Keyframe images | Off by default; opt-in per member | Small thumbnails of notable moments only, encrypted, short retention, admin-only | Useful for review, but images of people in the home are private |
| Raw sensor streams | No | Stay on the device, short-term | Huge and almost never recalled; their meaning is captured as events |
| Video and audio | No | Stay on the device; speech becomes communication records as text | Cost and privacy; the transcript carries the meaning |
| Recall requests | Yes, in the audit log | Who asked, what they asked, how many records came back | Reads are part of the audit trail |

**On images of people:** the mesh runs in a family home. By default no image of a person leaves the robot, and perception results name people only as members of a list the administrator controls. Anything beyond that is an explicit, per-member choice the administrator makes on the dashboard.

## Record format and integrity

Every record, from any member, uses one envelope. The **header** (everything except `payload`, `hash` and `sig`) is permanent; type-specific detail goes in `payload`, which retention may later purge. The exact byte-level rules are in `CONTRACT.md`.

```json
{
  "v": 1,
  "agent": "z3-r0",
  "seq": 18423,
  "ts": "2026-10-20T19:04:14.902-04:00",
  "type": "action",
  "summary": "Approach Vector",
  "refs": {"decision": "z3-r0:18422"},
  "place": "living-room",
  "peers": ["vector"],
  "tags": ["social"],
  "outcome": "reflex_stop",
  "sw": "z3r0-app 0.5.0",
  "prev_hash": "4ab7…",
  "payload_hash": "77d0…",
  "hash": "c21e…",
  "sig": "9f03…",
  "payload": {"distance_m": 0.8, "speed_mps": 0.15, "outcome_detail": "front range 0.25 m"}
}
```

| Field | Rule |
| --- | --- |
| `type` | `communication`, `thought`, `decision`, `action`, `event`, `perception`, `lesson`, `policy`, `system` |
| `seq` | Per member, increases by one, never reused |
| `refs` | Links to other records as `agent:seq`; a lesson links to every record it summarizes; a correction links to the record it corrects |
| `outcome` (actions) | `success`, `partial`, `reflex_stop`, `stuck`, `abandoned`, `failed`. Lives in the header so it survives payload purges and can be indexed. An action logged before it finishes gets a follow-up record carrying the outcome |
| `payload_hash` | SHA-256 of the canonical `payload`; lets the chain verify after the payload is purged |
| `hash` | SHA-256 of the canonical header (RFC 8785 JSON), which includes `prev_hash` and `payload_hash`; any edit or gap breaks the member's chain |
| `sig` | The member's Ed25519 signature over `"ltms-record-v1\n"` followed by the 32 raw `hash` bytes |

**On arrival** the server checks, for every record, that the signature matches an enrolled, unrevoked member; that the `agent` field is that member (no writing as someone else); that `seq` and `prev_hash` continue the member's chain; and that the type is one the member may write (writing `policy` needs a permission only the administrator grants; `system` is the server's own). A record that fails is rejected and the rejection is logged. A gap in a member's chain is flagged on the dashboard, never silently filled.

## Contributing records

Records reach long-term memory by one of two paths. Both end in the same verified, append-only store.

| Path | Used by | How |
| --- | --- | --- |
| Through the robot mesh | Robots and agents on the home network, such as Z3-R0 and Vector's mind | The member journals locally and publishes to the mesh broker on the Jetson; the mesh logger acknowledges each record, then pushes batches to the memory server |
| Direct | AI members outside the home, such as a cloud assistant | The member journals locally, signs each record and sends batches straight to the memory server's ingest endpoint over HTTPS |

**Rules for both paths**

- **Write locally first.** Every member keeps its own journal and treats long-term memory as the second copy, never the only one.
- **Idempotent.** A record is identified by `agent` and `seq`, so sending it twice stores it once. That makes retries safe after an outage.
- **Batched.** Members send records in batches (every few seconds, or when a batch fills) to keep requests and costs low.
- **Acknowledged.** The server returns the highest contiguous `seq` it has stored for that member. The member may purge its local copy only past that point.
- **No edits, ever.** A mistake is fixed with a new record that references the old one. The dashboard shows both.

## Recall

Recall gives a member the few records that matter for what it is about to do, never a bulk download. Every query is filtered on the server, capped in size, and recorded.

**Three kinds of query**

| Query | Example | Available |
| --- | --- | --- |
| Filtered | The last 10 `action` records by z3-r0 with summary "approach Vector" in the living room, with outcomes | From the start |
| Context pack | "I'm about to approach Vector in the living room." The server returns a small bundle: recent outcomes of that action, Vector's latest communications, relevant lessons and any policy that applies | From the start, built from filtered queries |
| Similar situations | "Anything like this before?" Matched by meaning rather than exact fields | Later, once the server builds a meaning-based index; runs on the server, so small members like the Pi Zero only ask and relay |

**How Z3-R0 uses it:** before a deliberate action, the UNO Q sends its intent (action, place, peers) to the Pi Zero over USB. The Pi Zero asks the memory server for a context pack and returns it to the Q. The Q weighs it alongside its own short-term journal: if this action ended in a reflex stop three of the last four times, it can choose differently. If recall is unavailable, the Q decides without it and journals that it did.

**Limits that keep recall cheap and safe**

- Default 25 records per reply, maximum 200; larger needs paging and counts against the member's quota.
- Per-member rate limits, for example a few requests per second and a daily cap, adjustable on the dashboard.
- Replies carry each record's hash, so a member can check that what it got is what was stored.
- Recall can return lessons and policies, but the member decides what to do. Memory never issues commands.

## Security and keeping scrapers out

The cheapest way to stop scrapers is to give them nothing to find. The server has no public pages, and every request without a valid member identity is refused before it reaches the application.

**Layers, outermost first**

1. **Nothing public.** No landing page, no sitemap, no anonymous endpoints. In LAN or VPN mode the server isn't reachable from the internet at all.
2. **Gate at the edge.** In internet mode, an authenticating proxy or edge firewall in front of the server rejects anything without a valid member token. Unknown traffic costs almost nothing because it never reaches the application or the database.
3. **Signed requests.** Every API request carries a short-lived token tied to the member's key. Tokens expire in minutes, so a leaked token is soon useless.
4. **Least privilege.** Each member gets only the permissions it needs (`contribute`, `recall`, `recall:scoped`); relays such as the Pi Zero are read-only.
5. **Rate limits and quotas** per member and per address. Floods are cut off at the edge.
6. **Encryption.** TLS for every connection; the database and any stored images encrypted at rest; backups encrypted.
7. **Everything audited.** Logins, enrollments, rejected records, and every recall query are logged and visible on the dashboard.

**The administrator's own access** uses a strong login with a second factor (a passkey or authenticator app). In hybrid mode, administration happens only on the home copy or over the VPN, never through the public replica.

**What a compromised member can do:** read within its own scope and add records under its own name. It can't edit or delete history, write as another member, read beyond its permissions, or command any robot. Revoking its key stops it immediately.

## Dashboard

The dashboard is a web page for the administrator, viewable from anywhere the server is reachable. It reads everything and changes only membership, policy and retention. It never edits records and never commands a robot.

| View | Shows | Main filters |
| --- | --- | --- |
| Timeline | Every record as it arrives, newest first | Member, type, place, peer, outcome, time range, text |
| Conversation | Communications between members, threaded like a chat | Pair or group of members, time range |
| Decision trace | One decision with its inputs, options and reason, the actions it led to, and their outcomes | Pick any decision or action |
| Outcomes | How often each kind of action succeeds or ends in a reflex stop, stall or abandonment, per member and place | Member, action, place, period |
| Lessons and policies | Derived lessons with the records behind them; current policies and their history | Member, topic |
| Members | The registry: who's enrolled, permissions, last seen, chain status; pending enrollment requests to approve or reject | Status |
| Integrity | Per-member chain verification, gaps, rejected records | Member, period |
| Recall log | Who queried what, and how much came back | Member, period |
| Settings | Retention per record type, image opt-ins, rate limits and quotas | — |

**Viewing from anywhere, changing from home.** In hybrid mode, the internet copy serves read-only views. Approvals, revocations, policy and retention changes happen on the home copy or over the VPN, so a stolen dashboard password alone can't change the mesh.

## Storage, retention and cost

Because the memory keeps meaning rather than raw streams, a small mesh fits comfortably on one modest server for years. These are planning estimates; the Integrity and Outcomes views will show the real rates once members are live.

**Assumptions:** four members (Z3-R0, Vector's mind, two software agents), about 5,000 records each per day, about 1.5 KB per stored record including indexes.

| Item | Estimate |
| --- | --- |
| Records per day | about 20,000 |
| Storage per day | about 30 MB |
| Storage per year, everything kept in full | about 11 GB |
| Meaning-based index (later), on communications, decisions and lessons only | about 4 GB a year more |
| Opt-in keyframe thumbnails, 50 a day at about 30 KB | about 0.5 GB a year |

**Retention tiers (defaults to confirm)**

| Records | Kept |
| --- | --- |
| Communications, decisions, actions with outcomes, lessons, policies | Indefinitely: small, and the core of the memory |
| Events and perception results | 90 days in full, then rolled up into daily summaries |
| Health summaries | Indefinitely as daily rollups |
| Keyframe images | 30 days, then deleted |
| Recall and access logs | 1 year |

With those tiers, storage grows by only a few GB a year.

**Software:** PostgreSQL as the store (append-only tables, with a vector extension when meaning-based recall arrives), a small API service for ingest and recall, and the dashboard served by the same service. In LAN mode it can run on the Jetson's storage or a NAS.

**Running cost (approximate; check current provider prices):** LAN mode costs only electricity. Internet mode needs a small VPS, roughly 2 vCPU, 4 GB RAM and 80 GB disk, which typically runs around $10–20 a month, plus backup storage. Keeping scrapers out at the edge is what stops traffic from pushing that cost up.

## Build phases

Each phase works on its own before the next starts.

1. **Mesh hub on the Jetson.** MQTT broker with per-member logins and ACLs; the mesh logger verifying chains and acknowledging records. *Done when* Z3-R0's journal records arrive, verify and are acknowledged.
2. **Memory server on the home LAN.** Ingest with signature and chain checks; the member registry with admin-approved enrollment; filtered recall. *Done when* the mesh logger pushes records in and a filtered query returns them with hashes.
3. **Dashboard, home only.** Timeline, Conversation, Decision trace, Members and Integrity views; enrollment approvals. *Done when* a Z3-R0 and Vector exchange can be followed end to end.
4. **Z3-R0 recall.** The Pi Zero relay and context packs. *Done when* the Q receives a context pack before a planned action and journals that it used it.
5. **Remote access.** Either a VPN to the home server, or the internet replica with the edge gate, rate limits and read-only dashboard. *Done when* the dashboard works from outside the home and an unenrolled request is refused at the edge.
6. **Outside AI members.** Direct ingest for cloud agents; scoped recall. *Done when* an outside agent enrolls, contributes and recalls within its scope.
7. **Lessons and meaning-based recall.** A reflecting agent writes lessons; the server builds a meaning-based index. *Done when* a "anything like this before?" query returns relevant past situations.
8. **Retention and backups.** Rollups, image expiry, encrypted off-site backups, and a tested restore. *Done when* a restore from backup verifies every member's chain.

## Risks and open decisions

| Risk | Mitigation |
| --- | --- |
| Scrapers or unknown bots run up cost | No public surface; edge gate rejects unauthenticated traffic; per-member rate limits; VPN-only option |
| A member's key is stolen | Short-lived tokens, least-privilege permissions, immediate revocation, recall audit log |
| Bad or misleading memories steer decisions | Memory informs and never commands; safety stays on each device; lessons link to their source records; corrections are visible |
| A dishonest member writes false records | It can only write under its own name; its records are attributable and can be filtered out or the member revoked |
| Privacy of people in the home | No raw video or audio; no images of people by default; named people limited to an admin-controlled list |
| Memory grows without bound | Retention tiers, rollups, image expiry |
| Server or disk failure | Members keep local journals until acknowledged; encrypted off-site backups with a tested restore |
| Internet outage | Home LAN copy keeps working; remote members queue locally and replay |

**Open decisions**

1. Deployment: start LAN-only with a VPN for remote viewing, or stand up the internet replica from day one?
2. Where the home copy runs: the Jetson, a NAS, or a dedicated small server.
3. Retention defaults in Storage, retention and cost.
4. Keyframe images: leave off entirely, or allow per member?
5. Which agent writes lessons, and how often (for example, a nightly reflection on the Jetson).
6. Which outside AI members to invite first, and with what scope.
7. How the mesh's policies are written and versioned, so they line up with the AI Constitution project.
