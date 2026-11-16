# Design: the fleet steward, an agentic AI subsystem for fleet management

Status: **proposed** (15 Nov 2026). Decisions marked **(Sean)** need his call
before implementation issues are filed.

LTMS is the fleet management system for physical and agentic AI. This design
adds an **agentic AI subsystem** that helps the admin run the fleet: it reads
the fleet's state, explains it, drafts actions and, with the admin's approval,
carries them out. It connects to an AI provider the admin chooses (a cloud API
or a local model server) and is embedded in the pages where admins already
work, not bolted on as a separate chatbot.

Working name: **the steward**. **(Sean)** to confirm or rename.

## Decisions (proposed)

1. **Off by default, provider-neutral.** LTMS ships with no provider set. An
   admin picks one in Configure › AI. Two adapters cover nearly everything:
   - `anthropic`: the Messages API, native tool use.
   - `openai_compatible`: one adapter for Ollama, vLLM, llama.cpp server,
     LM Studio, OpenAI, Azure OpenAI and any gateway that speaks the
     Chat Completions tool-calling format.

   No vendor SDK is required by the core; adapters use plain HTTPS. A closed
   network runs the steward against a local model server with no internet.

2. **The steward is not a fleet member.** It has no key, writes no records,
   never appears in recall packs and never joins a swarm. LTMS exists to
   observe members; an AI that writes into their memory would contaminate what
   is being observed. The steward works on the **admin side**: it acts *for* a
   signed-in admin, and its words reach members only as **directives**, which
   are signed by the fleet key and approved by a person.

3. **Three tool tiers, and a list that is never exposed.**

   | Tier | What it does | Who triggers it |
   |---|---|---|
   | `read` | Queries fleet state through existing services | The steward, freely |
   | `propose` | Drafts something (a directive, a snooze, an enrollment decision, a charter change) and returns a **proposal card** | The steward |
   | `act` | Carries out an approved proposal through the same service call and audit as the button it replaces | **The admin**, by confirming the card |

   **Never exposed as tools**: memory wipe, record delete, purge, revoke, key
   operations (fleet key, swarm root keys, MCP tokens), charter publish, LRS
   endpoints, retention, enrollment policy, and the AI settings themselves.
   These stay human-only, in the UI.

   v1 autonomy ceiling: **every `act` needs a confirm click.** Standing
   permissions (for example "the steward may snooze low-severity attention
   items") are a later issue, behind a per-tool setting that defaults off.
   **(Sean)** to confirm the v1 ceiling.

4. **Member content is untrusted data.** Record summaries, payloads, swarm
   messages, dataset text and wire-pod transcripts are written by members or
   outsiders and can carry prompt injection aimed at the steward. Therefore:
   - Tool results mark member-authored text as data (`{"untrusted": true, ...}`
     plus a delimiting wrapper in the prompt).
   - A proposal that was built from untrusted text says so on its card
     ("Drafted from <member>'s record #812"), and shows the exact parameters
     that will run.
   - No `act` runs from the model's output alone; the confirm click is the
     control. A red-team fixture set (records that try to make the steward
     issue directives, approve members or reveal secrets) runs in CI.

5. **Data boundary.** Before anything leaves LTMS for a provider, a redaction
   layer removes what never leaves: IP addresses, location estimates and
   coordinates, enrollment and MCP tokens, key material, admin session data
   and the admin's email. This follows the standing rule that IPs and location
   are admin-only. Each provider has a **boundary** setting:
   - `cloud` (default for any non-local base URL): redacted, and record
     payloads are summarized by type and size unless the admin allows payloads.
   - `local` (the admin asserts the endpoint is on their own network): payloads
     allowed; IPs, location and secrets still redacted.

6. **Secrets by name, never by value.** The provider credential is the **name**
   of an environment variable on the LTMS server (`ANTHROPIC_API_KEY`,
   `LTMS_AI_KEY`, ...), the same pattern as dataset integrations. LTMS never
   stores, logs, returns or sends the key anywhere but the provider.
   Base URLs are checked against `LTMS_AI_BASE_URL_ALLOW` when it is set.

7. **Every call is on the record, for humans.** A new `ai_calls` ledger keeps:
   time, admin, surface (which page), provider, model, input and output tokens,
   latency, tools called, proposals made and their outcome (confirmed,
   dismissed, expired). Transcripts are kept for `LTMS_AI_TRANSCRIPT_DAYS`
   (default 30) and then dropped; the ledger row stays. Confirmed actions also
   go in the audit log as `actor: "admin:<user> via steward"`.

   If an LRS is connected, steward activity can be sent as xAPI using the
   **AI Agent Actions Profile** (AIAAP), with the admin as
   authority and the model as the agent. Off by default, live-only like other
   statements.

8. **Budget and kill switch.** Per-request token cap, per-day fleet cap and a
   per-admin daily cap, all in Configure › AI. Over the cap, the steward says
   so and stops; the dashboard works exactly as before. A kill switch
   (`LTMS_AI_DISABLED=true`, or the toggle) turns the subsystem off at once.
   Nothing in LTMS depends on the steward.

9. **Accessible first.** The Ask panel is a keyboard-operable drawer with an
   ARIA live region for streamed answers, proposal cards are real forms with
   labelled controls, and nothing is conveyed only by animation or color. It
   works in the 508 theme and Calm view.

## Where it is embedded

The steward is one service with one tool registry; each surface is a preset
(page context plus a starting task) over it.

| Surface | Nav group | What it does |
|---|---|---|
| **Ask panel** | Everywhere | A drawer on every page. Sends the current page and selection as context ("this member", "this swarm", "this time range"). |
| **Needs attention** (Overview) | Operate | *Triage*: groups items, explains likely cause, proposes snoozes or pings. |
| **Fleet Health / System status** | Analyze / Configure | *Diagnose*: reads heartbeats, hub health, background jobs and migrations; explains what is wrong and what to check on the device. |
| **Members: enrollment review** | Operate | Summarizes a pending request (declared kind, specs, cognition, swarm invite) and proposes approve or reject with reasons. The admin decides. |
| **Datasets: intake review** | Govern | Summarizes a pending version, flags licence terms, and scans the text for instructions aimed at members before an admin approves it for the whole fleet. |
| **Charter** | Govern | Summarizes a draft's diff, drafts amendments, reports who hasn't read the current version and when their writes pause. Publishing stays human. |
| **Directives** | Operate | Drafts a directive to one swarm or all swarms, routes it through the swarm leader when one is appointed, summarizes acknowledgements (received, accepted, declined). |
| **Profiles** (Topology, Chronology, Choreography) | Analyze | *Narrate*: "what did this member do in the last 24 h, and with whom", citing record numbers so every claim links back to the record. |
| **Fleet digest** | Operate | A scheduled background run (default daily, off by default) that reads, never acts, and posts a digest the admin sees on Overview. |

**Citations are required.** Any statement about fleet activity cites record
`agent:seq`, member or swarm names; the panel renders them as links. An answer
about the fleet with no citation is shown as unverified.

## How it fits what exists

- **Member mind (#338)**: members' own brains, client-side, in `ltms_mind`.
  The steward is server-side and admin-side. They share nothing at runtime;
  the prompt-safety rules (brain reasons, loop does I/O) are the same idea.
- **Swarm leader**: a member that directs its own swarm on the bus. The
  steward works above swarms and talks to a leader only through directives.
- **MCP server (`ltms_mcp`)**: lets *outside* agents read LTMS. The steward's
  read tools call the same services directly; it does not go through MCP.
- **Federation (#235)**: later, a steward could summarize another fleet's
  shared records. Out of scope.

## Code layout

```
server/ai/
  providers/base.py              # Provider protocol: chat(messages, tools, limits) -> stream
  providers/anthropic.py
  providers/openai_compatible.py
  redact.py                      # the data boundary
  tools/registry.py              # tool spec + tier; act tools wrap existing services
  tools/read_*.py, tools/propose_*.py
  steward.py                     # the tool loop, budget checks, ledger writes
server/models/ai_call.py, ai_proposal.py   # + migration
server/api/admin.py              # /api/admin/ai/* (settings, test, chat SSE, proposals, ledger)
dashboard/src/components/AskPanel.jsx, ProposalCard.jsx
dashboard/src/pages/AISettings.jsx         # Configure › AI
docs/steward.md                  # admin guide
```

`server/services/ingest.py`, `integrity.py` and `swarms.py` are not touched:
`act` tools call the same public service functions the admin endpoints call.

## Implementation issues (to file once accepted)

One issue per agent session; order matters.

| # | Issue | Needs |
|---|---|---|
| S1 | Provider layer: protocol, `anthropic` and `openai_compatible` adapters, settings (env-var credential name, base-URL allow-list), *Test connection* | — |
| S2 | Ledger and budget: `ai_calls` table and migration, token caps, kill switch, transcript retention job | S1 |
| S3 | Tool registry, `read` tools (attention, health, members, swarms, records, episodes, datasets, charter status, system status) and the redaction layer | S1 |
| S4 | Red-team fixtures: injected records, datasets and swarm messages; tests that no `act` runs and nothing redacted leaves | S3 |
| S5 | Steward loop and chat endpoint (SSE), citations, page-context presets | S2, S3, S4 |
| S6 | Configure › AI page: provider, model, boundary, budget, ledger view | S2 |
| S7 | Ask panel drawer (508: keyboard, live region, Calm view) | S5 |
| S8 | `propose` and `act`: proposals table, proposal cards, confirm/dismiss, audit `via steward` (snooze, ping, enrollment decision, directive) | S5, S7 |
| S9 | Surfaces: Needs attention triage, Health/System diagnose | S8 |
| S10 | Surfaces: enrollment review and dataset intake review | S8 |
| S11 | Surfaces: charter and directive drafting, acknowledgement summaries | S8 |
| S12 | Surfaces: profile narration with record citations | S7 |
| S13 | Scheduled fleet digest (read-only background run) | S5 |
| S14 | AIAAP statements for steward activity to the LRS | S8; xapi-ai profile update |

## Open questions **(Sean)**

1. Name: "steward", or something from the fleet's naming convention.
2. v1 ceiling: confirm every `act` (proposed), or allow standing permissions
   for low-risk tools from the start.
3. Reference deployment provider. The reference server is a CPU-only VPS, so a
   local model there means a model server on another machine reachable over a
   private tunnel; otherwise a cloud API with the `cloud` boundary.
4. Should steward activity go to the LRS (S14) in v1, or wait until the AIAAP
   profile covers it?
