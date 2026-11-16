# Design: the Fleet Commander, an agentic AI member that helps run the fleet

Status: **proposed, revised** (16 Nov 2026, after Sean's answers on #432).

The product is now **FLTMS, the Fleet Management System** for physical and
agentic AI. This design adds the **Fleet Commander**: an AI fleet member,
running on whichever AI provider the admin chooses, that can observe and
command every swarm. The admin decides how much of the fleet's management to
hand to it, from read-only advice up to running most operations on its own.

## 1. Decisions

1. **The Fleet Commander is a fleet member.** It has its own Ed25519 key,
   writes signed, hash-chained records under the frozen contract, reads the
   charter like every member, and is observed like every member. Its records
   are labelled with its role, so researchers can always separate "what the
   swarms did" from "what the commander told them to do".
2. **One commander per fleet**, appointed by the admin and signed with the
   fleet key (the same pattern as hub and leader appointments). It belongs to
   no swarm; it sits above them. Federation (#235) may later add commanders
   that talk to each other.
3. **Provider-agnostic.** Two adapters cover every provider Sean has today,
   and most others:

   | Provider | Adapter | Notes |
   |---|---|---|
   | Anthropic API | `anthropic` | Messages API, native tool use |
   | Google AI Studio (Gemini API) | `openai_compatible` | Google's OpenAI-compatible endpoint; a native `gemini` adapter only if this falls short |
   | Ollama (web-deployed or local) | `openai_compatible` | Ollama's `/v1` endpoint |
   | Mac model server over a private tunnel | `openai_compatible` | Ollama, LM Studio or llama.cpp behind WireGuard/Tailscale; the tunnel is set up outside FLTMS |

   Several providers can be configured at once, with one active, plus an
   optional fallback. Tool calling is tested for each provider and model in
   Configure › Commander › *Test* before it can be made active, because small
   local models often call tools badly.
4. **Permissions are configurable, enforced by the server.** The admin grants
   permissions from a fixed catalog (section 3). The server checks every
   commander call against the grants, so a confused or manipulated model can
   never do more than it was granted, whatever its prompt says.
5. **Member content is untrusted input.** Records, swarm messages, dataset
   text and transcripts can carry instructions aimed at the commander. The
   commander's prompt wraps them as data; actions taken because of untrusted
   content carry a `derived_from` reference; a red-team fixture set runs in CI;
   and the admin can require confirmation for any action derived from
   untrusted content (section 3.4).
6. **A data-sharing agreement per provider** decides what leaves FLTMS
   (section 4). Self-hosting needs no restrictions; a cloud provider gets
   whatever the admin agrees to share.
7. **AIAAP covers the commander, and its activity goes to the LRS.** Its
   records already reach the LRS like any member's; this design adds the AIAAP
   statements for decisions, proposals, approvals and actions (section 6). This
   needs an AIAAP profile update in `xapi-ai` first.

## 2. How it runs

```
            ┌─────────────── FLTMS server ────────────────┐
 admin ──── │ dashboard · Commander API (/api/commander/*) │
            │   permission check → existing services       │
            │   proposals · ledger · data-sharing filter   │
            └───────────────▲───────────────┬──────────────┘
          signed records,   │               │ events (attention, enrollments,
          commander calls   │               │ acks, health), admin messages
            ┌───────────────┴───────────────▼──────────────┐
            │ fleet commander runtime (own container)      │
            │  key (own volume) · outbox · wake loop       │
            │  provider adapter ──► AI provider            │
            └──────────────────────────────────────────────┘
```

- **Its own process.** `fltms_commander` runs as its own container in the
  Compose stack (or on another machine). Its key lives in its own volume and
  never touches the FLTMS server's database, like any member's key. It talks
  to FLTMS through the member API plus a **Commander API** that wraps the
  admin services behind the permission check.
- **Wake loop.** It wakes on events (new attention item, enrollment request,
  directive acknowledgement, health change, a message from the admin) and on a
  schedule, within its token budget.
- **Every turn is on its chain.** It writes `perception` (what it noticed),
  `thought` (its reasoning, summarized), `decision`, and `action` records, with
  `refs` to the member records that led to it. The admin can read the
  commander's reasoning on its profile like any member's.
- **Directives come from the commander's key.** It signs directives with its
  own key, the same way a swarm leader does (#310), scoped to one swarm, a list
  or the whole fleet. Members accept them because the commander's appointment
  is signed by the fleet key. The harness must learn to verify a commander
  appointment (an issue under #289).
- **The admin talks to it.** The Ask panel on every page is a conversation
  with the commander, carrying the page's context. Answers about fleet activity
  cite record `agent:seq`, member and swarm names, and render as links.

## 3. The permission catalog

### 3.1 Modes

Each permission is set to one mode:

| Mode | Meaning |
|---|---|
| `off` | The commander can't do it or see it |
| `propose` | It drafts the action as a proposal card; the admin confirms or dismisses |
| `act` | It does it on its own, within the permission's limits, and it shows up in the activity feed |

Read permissions are `off` or `on`. A permission can't be set above
`propose` unless the commander's runtime has passed the red-team suite for the
active provider and model (S6).

### 3.2 Catalog

**Observe**

| ID | Lets the commander | Requires |
|---|---|---|
| `fleet.read` | See members, swarms, roles, health, attention, specs, system status | none (base of everything) |
| `records.read` | Search records, episodes, conversations and swarm messages | `fleet.read` |
| `datasets.read` | Read the catalog and approved dataset items | `fleet.read` |
| `governance.read` | Read the charter, policies and who has read what | `fleet.read` |
| `audit.read` | Read the audit log | `fleet.read` |
| `location.read` | See declared locations, GPS, network estimates and IPs | `fleet.read` **and** the data-sharing agreement allows location for the active provider |

**Operate**

| ID | Lets the commander | Requires | Limits |
|---|---|---|---|
| `attention.manage` | Snooze or clear Needs attention items | `fleet.read` | max snooze length |
| `members.ping` | Ping members | `fleet.read` | per-member rate |
| `directives.issue` | Issue directives | `fleet.read`, `records.read` (to read acks) | scope: listed swarms or all; per-hour rate; expiry cap |
| `directives.cancel` | Cancel its own directives | `directives.issue` | own directives only, unless `all` |
| `swarms.leader.direct` | Send a task to a swarm leader to plan and run | `directives.issue` | same scope as directives |

**Membership**

| ID | Lets the commander | Requires | Limits |
|---|---|---|---|
| `enrollment.decide` | Approve or reject enrollment requests | `fleet.read` | roles it may grant: `member`, `observer`; **never `hub`** |
| `members.profile.edit` | Edit specs, cognition, proxied flag and permissions | `fleet.read` | |
| `locations.declare` | Declare member and swarm locations | `location.read` | |
| `swarms.membership` | Move members between swarms and to and from free agent | `fleet.read` | |
| `swarms.manage` | Add and rename swarms | `swarms.membership` | |
| `swarms.leader.appoint` | Appoint or remove a swarm leader | `swarms.membership` | |

**Govern**

| ID | Lets the commander | Requires | Limits |
|---|---|---|---|
| `datasets.intake` | Add datasets and versions, snapshot URLs, import reference sources | `datasets.read` | size cap |
| `datasets.approve` | Approve or retire versions | `datasets.intake` | two-person rule option: can't approve its own intake |
| `charter.draft` | Draft charter versions | `governance.read` | |
| `charter.publish` | Publish a charter version | `charter.draft` | two-person rule option; mode capped at `propose` by default |

**Restricted** (each is a separate, explicit grant; default `off`; `propose` recommended)

| ID | Lets the commander | Requires |
|---|---|---|
| `members.revoke` | Revoke a member | `enrollment.decide` |
| `members.wipe` | Wipe a member's memory (recall cutoff) | `records.read`, `members.revoke` |
| `records.delete` | Delete specific records | `records.read`, `audit.read` |
| `swarms.remove` | Remove a swarm (members to free agents or revoked; keep or purge records) | `swarms.manage`, `members.revoke` |
| `swarms.hub.appoint` | Appoint a swarm's hub | `swarms.manage` |

**Never grantable** (not in the catalog; human only)

- The fleet key, swarm root keys, MCP tokens and admin accounts or sessions.
- LRS endpoints, retention, enrollment policy and GeoIP configuration.
- **Anything about the commander itself**: its appointment, permissions,
  provider, data-sharing agreement and budget. It also can't approve its own
  enrollment, edit or delete its own records, or remove the audit trail of what
  it did.

### 3.3 Dependency rules

- Granting a permission grants nothing else. The dashboard won't save a grant
  whose requirements aren't granted; turning a requirement off turns its
  dependents off too, and says which.
- A permission's mode can't be higher than the mode of an operate or govern
  permission it depends on (for example `directives.cancel: act` needs
  `directives.issue: act`).
- `location.read` also switches off when the active provider's agreement stops
  allowing location.

### 3.4 Presets and global controls

| Preset | Grants |
|---|---|
| **Advisor** | All observe permissions except `location.read`; everything else `propose` except restricted (`off`) |
| **Operator** | Advisor, plus Operate permissions at `act` |
| **Delegate** | Operator, plus Membership and Govern at `act` (charter publish stays `propose`), restricted at `propose` |
| **Custom** | Anything the catalog allows |

Controls that apply over every grant:

- **Stand down**: one switch that drops every permission to `propose` at
  once, keeps proposals, and records the stand-down. **Kill switch**
  (`FLTMS_COMMANDER_DISABLED=true` or the toggle) stops the runtime.
- **Confirm when derived from untrusted content** (default on): an `act` whose
  reasoning cites member-authored text becomes a proposal.
- **Rate limits**: per permission, plus a fleet-wide cap on actions per hour.
- **Two-person rule** for `datasets.approve` and `charter.publish` (on by
  default).
- Every grant change is audited and versioned, with the admin who made it.

## 4. Data-sharing agreements

Each configured provider has an agreement the admin accepts, versioned and
audited like the charter.

| Field | What it holds |
|---|---|
| Hosting | `self-hosted` (on infrastructure the operator controls, including a private tunnel) or `provider-hosted` |
| Provider attestation | Free text the admin fills in: the provider's terms, data retention, certifications or authorization level. FLTMS records it; it doesn't verify it |
| Categories shared | On or off per category (below) |
| Accepted by, version, date | Set by FLTMS |

| Category | Default, self-hosted | Default, provider-hosted |
|---|---|---|
| Fleet structure (names, roles, swarms, health, specs) | on | on |
| Record headers and summaries | on | on |
| Record payloads and telemetry | on | off |
| Swarm messages and conversation transcripts | on | off |
| Dataset text | on | on |
| Charter and policies | on | on |
| Location and IP addresses | on | off |
| Audit log | on | off |
| **Secrets: keys, tokens, passwords, sessions** | **never** | **never** |

When a category is off, FLTMS sends a stub (type, size, record number), so
the commander knows the data exists and can ask the admin, but the content
stays home. Turning on location for a provider-hosted model is a change to the
rule that IPs and locations never leave FLTMS, so the dashboard asks for it
explicitly. The LRS rule doesn't change: IPs and location still never go to
the LRS.

## 5. Where it appears

| Surface | Nav group | Advice | Agentic (with permission) |
|---|---|---|---|
| **Ask panel** | Everywhere | Help using FLTMS; answers with citations | Anything granted, from the page in context |
| **Commander profile** | Operate | Its chain: perceptions, thoughts, decisions, actions | Stand down, kill switch |
| **Proposals** | Operate | Open proposals with the exact parameters and the records behind them | Confirm or dismiss, singly or in bulk |
| **Needs attention** | Operate | Triage and likely cause | Snooze, ping, issue directives |
| **Fleet Health / System status** | Analyze / Configure | Diagnosis | Ping; directive to a member to report |
| **Members** | Operate | Enrollment summaries | Approve or reject, place in swarms |
| **Swarms** | Operate | Swarm summaries | Appoint leaders, task leaders |
| **Datasets** | Govern | Intake review: licence, contents, injected instructions | Intake, approve |
| **Charter** | Govern | Diff summary, who hasn't read it | Draft, publish (two-person rule) |
| **Profiles** (Geography, Topology, Chronology, Choreography) | Analyze | Narration of what happened, with record links | none |
| **Configure › Commander** | Configure | | Human only: provider, agreements, permissions, budget |

## 6. Records, audit and the LRS

- **FLTMS records**: the commander's own chain, plus a `refs` link from each
  action record to the member records it was based on.
- **Audit log**: actor `commander:<name>`, with `mode` (`act`, or `propose`
  plus the admin who confirmed) and the permission used.
- **Ledger**: each provider call (provider, model, tokens, latency, tools,
  outcome); transcripts kept `FLTMS_COMMANDER_TRANSCRIPT_DAYS` (default 30).
- **LRS (AIAAP)**: statements for each perception, decision, proposal,
  confirmation or dismissal, action and stand-down, with the commander as
  actor, the admin as authority on confirmed proposals, the model and provider
  in the context, and the permission in a context extension. Live-only, like
  other statements. The verbs and extensions are defined in `xapi-ai` first,
  then FLTMS emits them.

## 7. Budget

Per-call and daily token caps per provider, and a monthly cap. Near the cap,
the commander drops to reading and `propose` only; at the cap, it stops and
raises a Needs attention item. FLTMS never depends on the commander running.

## 8. Accessibility

The Ask panel is a keyboard-operable drawer with an ARIA live region for
streamed answers. Proposal cards are forms with labelled controls. The
permission page is a table with real checkboxes and selects, and dependency
messages are text. Nothing relies on color or animation, and it all works in
the 508 theme and Calm view.

## 9. Implementation issues (to file once accepted)

One issue per agent session, in this order.

| # | Issue | Needs |
|---|---|---|
| C0 | Rename to FLTMS, the Fleet Management System: product name in the dashboard, docs, README and tagline; env-var prefix change with `LTMS_*` still accepted | none |
| C1 | Provider layer: `anthropic` and `openai_compatible` adapters, multiple providers, fallback, *Test* including a tool-calling check | none |
| C2 | Commander appointment: fleet-key-signed appointment, `commander` role, one per fleet, appointment history | none |
| C3 | Permission catalog and grants: tables and migration, dependency rules, modes, presets, stand down, audited versioned changes | C2 |
| C4 | Commander API: `/api/commander/*` wrapping existing services behind the permission check; proposals table; audit `commander:<name>` | C3 |
| C5 | Data-sharing agreements and the outbound filter (categories, stubs, secrets never) | C1 |
| C6 | Red-team fixtures (injected records, datasets, swarm messages, transcripts) and the gate that caps modes at `propose` until they pass | C4, C5 |
| C7 | Commander runtime `fltms_commander`: own container and key, wake loop, records its own chain, ledger and budget | C1, C4, C5 |
| C8 | Commander directives signed with its key; harness verifies commander appointments (under #289) | C2, C7 |
| C9 | Configure › Commander page: providers, agreements, permissions table with dependencies, presets, budget, kill switch | C3, C5 |
| C10 | Ask panel and Proposals page | C7 |
| C11 | Surfaces: Needs attention, Health and System status | C10 |
| C12 | Surfaces: Members and Swarms | C10 |
| C13 | Surfaces: Datasets and Charter (two-person rule) | C10 |
| C14 | Surfaces: profile narration with record links | C10 |
| C15 | AIAAP: commander verbs and extensions in `xapi-ai` | none |
| C16 | FLTMS sends commander AIAAP statements to the LRS | C7, C15 |

## 10. Open questions (Sean)

1. **FLTMS rename scope.** Product name and UI only (C0 as written), or also
   the repo, Python packages and Docker image names? The latter is a bigger,
   riskier change for existing deployments and members.
2. **Default preset** for a newly appointed commander: Advisor (proposed).
3. **Two-person rule** on dataset approval and charter publish: on by default
   (proposed)?
