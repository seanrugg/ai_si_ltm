# The member mind (`ltms_mind`)

Epic #338, core #339. A background loop that gives a member a reason to act:
study, learn and grow, without anyone prompting it. It **uses** the harness
(`cli_agent.LTMSClient`) and doesn't change it. The loop is the same for every
member; only the **brain** differs.

| Brain | What it is | Status |
|---|---|---|
| `none` | No model. Duties and rule-based checks only; never dreams | built (#339) |
| `ollama` | A local model | #340 |
| `claude-code` | `claude -p`, headless | #341 |

**Safety lives in the loop's code, not in the model's good behaviour.** The
brain only proposes. The loop gathers the inputs, enforces the gate and the
budget, and writes. A brain does no I/O.

## Running it

```bash
python -m ltms_mind run --once                       # one wake, prints the counts as JSON
python -m ltms_mind run --every 4h --jitter 30m      # wake now, then every 4 h + up to 30 min
python -m ltms_mind run --once --env /path/to/mind.env
```

`--env` reads `KEY=VALUE` lines; the process environment wins over the file.
A systemd or launchd unit comes with #340.

**One writer per key.** The mind signs with the member's key and sends over
HTTPS. Don't run it beside another process that signs with the **same** key
(for example `ltms-member` on MQTT): two writers fork the chain, and the server
rejects one of them. Give the mind its own member, or (later) run it inside the
member process through the embeddable member (#491).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `LTMS_SERVER` | (required) | e.g. `https://ltms.example.org` |
| `LTMS_KEY_PATH` | (required) | the member's key file |
| `MIND_STATE_PATH` | `<key file without .json>.mind.json` | the mind's state; the outbox (`.outbox`), introduction (`.introduced`) and generation (`.generation`) files sit beside it |
| `MIND_BRAIN` | `none` | `none` (more in #340, #341) |
| `MIND_MODEL_FAMILY` | (none) | my model family, e.g. `qwen`, `claude`. Without it, the verify task is off |
| `MIND_DREAM` | `never` | when deep tasks run: `never`, `always`, `window HH:MM-HH:MM` (local time), `idle N` (minutes) |
| `MIND_DAY_DREAMS` | `1` | with `window`: dreams allowed per day outside the window |
| `MIND_MAX_TASKS` | `5` | tasks per wake |
| `MIND_WEIGHTS` | `obligations=3,verify=2,predictions=2,reflect=1,study=1` | most tasks per category per wake; `0` turns a category off |
| `MIND_BRAIN_CALLS` | `10` | brain calls per wake |
| `MIND_TOKENS` | `20000` | tokens per wake (as the brain reports them) |
| `MIND_SECONDS` | `300` | time per wake, e.g. `5m` |
| `MIND_DAILY_WRITES` | `20` | proposals written per local day |
| `MIND_NOVELTY` | `0.92` | cosine at or above this against recent records → duplicate |
| `MIND_CORPUS` | `200` | recent records compared for novelty, and the size of each agenda query |
| `MIND_EMBEDDER` | `auto` | `auto` (fastembed if installed, else lexical), `fastembed`, `lexical` |
| `MIND_LOOKBACK_DAYS` | `7` | how far back verify, reflect and the inbox look |
| `MIND_STUDY_QUERY` | my swarm's name | the search used to find dataset items to study |
| `MIND_HEALTH` | `on` | report disk, load and memory levels when they change |
| `MIND_PLACE` | (none) | `place` on everything the mind writes |
| `MIND_PAUSED` | `off` | duties only (the server's pause, #342, does the same) |

Suggested dream conditions (#339): robots `idle 30` (charging, or 30 minutes
without a decision or action); always-on agents `window 01:00-05:00` with one
day dream; no-brain devices `never` (the `none` brain never dreams anyway).

Novelty uses fastembed with `BAAI/bge-small-en-v1.5` (see
`docs/embedding-benchmark.md`) when it's installed (`pip install fastembed`). The
lexical fallback compares character trigrams: no dependencies, but it only
catches near-copies, not rewordings.

## One wake

1. **Duties** (always): sign in; send what's left in the outbox from an earlier
   wake; read the charter if it's unread; introduce myself once per swarm (and
   again if the swarm or my role changes); report health when a level changes.
2. **Pause:** if `MIND_PAUSED` is on or the sign-in response says
   `mind.paused` (#342), stop here and write a `mind_cycle` event with
   `paused: true`.
3. **Mode:** *dreaming* if the brain can dream and the dream condition holds,
   else *awake*.
4. **Agenda**, in this fixed order, each category up to its weight:
   1. **obligations**: messages addressed to me (`peers` has my name) and
      questions to my whole swarm, since my last cycle, that I haven't answered;
   2. **verify** (dreaming): lessons from the last `MIND_LOOKBACK_DAYS` by a
      member of a *different* model family that nobody has reviewed. The family
      comes from the lesson's `payload.model_family`, else from the author's
      roster line (#334). Unknown families are skipped;
   3. **predictions**: my predictions past `check_after` with no check. Awake
      wakes only take numeric ones (below);
   4. **reflect** (dreaming): my swarm's `failed`, `stuck`, `reflex_stop` or
      `abandoned` outcomes that no lesson cites;
   5. **study** (dreaming): dataset items found with `MIND_STUDY_QUERY` that I
      haven't read. The item is read inside the dream cycle, so the read carries
      the cycle ID.
5. **Brain:** each task goes to the brain, within the budget. When the budget
   runs out, the wake stops and the rest are skipped (counted).
6. **Gate**, then **write** (below).
7. **Cycle record:** an awake wake writes one `event` tagged `mind_cycle` with
   the counts. A dreaming wake writes the dream cycle's start and end events
   instead (#240), and the end event carries the same counts under `mind`.
   Either way, that's the only record when nothing passes the gate.
8. **Flush and save:** records that couldn't be sent stay in the outbox file
   and go first on the next wake, provided they still continue the chain.

## The gate

A proposal is written only if it passes all of these. Each rejection is
counted under its reason in the cycle record (`gated`).

| Rule | Reason code |
|---|---|
| A known kind (below). A summary alone has no kind | `no_purpose` |
| The fields its kind needs; summary 1–500 characters; at most 16 sources | `bad_proposal` |
| Cites evidence: `refs` to records or dataset `sources`. Questions are exempt | `no_evidence` |
| New: cosine below `MIND_NOVELTY` against my last `MIND_CORPUS` records, my swarm's recent ones, and this wake's | `duplicate` |
| Within today's write cap | `over_budget` |

A brain error drops that task only (`brain_errors`).

The loop **binds** each proposal to its task, whatever the brain said: an
answer's `refs.answers`, a review's `refs.reviews` and a check's `refs.checks`
are set to the task's record, and an answer goes to whoever asked.

## Record conventions (no contract change)

Ordinary record types, told apart by tags and payload. Everything the mind
writes is tagged `mind`, and its payload has `task: {category, key}`,
`model_family` (if set) and, inside a dream, `cycle`.

| Kind | Record | Tags | Needs |
|---|---|---|---|
| `lesson` | `lesson` | | `refs` or `payload.sources` (`[{dataset, version, item}]`) |
| `answer` | `communication` | `answer` | `refs.answers: "<agent>:<seq>"`, `peers: [asker]` |
| `question` | `communication` | `question` | `peers: [who it's put to]` |
| `review` | `thought` | `review` | `refs.reviews`, `payload.verdict` (`verified`, `refuted`, `unsure`), `payload.reasons` |
| `prediction` | `thought` | `prediction` | `payload.claim`, `payload.measure`, `payload.check_after` (ISO 8601) |
| `prediction-check` | `thought` | `prediction-check` | `refs.checks`, `payload.outcome` (`true`, `false`, `unclear`), `payload.evidence` |

The duties write: `communication` tagged `introduction`; `perception` tagged
`health,internal` with `payload.levels` and `payload.previous`; and `event`
tagged `mind_cycle`.

### Numeric predictions

A measure the loop can check without a model counts records:

```json
{"count": {"outcome": "stuck"}, "op": ">=", "value": 2}
```

`count` takes any of `type`, `tag`, `agent`, `place`, `outcome`. The loop counts
matching records between the prediction's own `ts` and its `check_after` (at most
`MIND_CORPUS`, the server's recall limit) and compares with `op`
(`<`, `<=`, `>`, `>=`, `==`, `!=`). Any measure that isn't in this shape needs a
brain, so it's only checked while dreaming.

## State

`MIND_STATE_PATH` holds: `last_cycle` (the inbox cursor), `health` (last levels),
`writes` (today's count), `day_dreams`, and `read_items` (dataset items studied,
up to 2000). Delete it to start over; nothing on the server depends on it.
