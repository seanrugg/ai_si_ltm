# Datasets: a member's guide

Fleet datasets are reference material: manuals, procedures, glossaries and
other text an admin has approved for the whole fleet. LTMS stores and serves
them. It doesn't train anything. A member reads an item, decides what it means
for its own behaviour, and writes that down as a lesson that cites the item.

Every read is logged, and if an LRS is configured it is sent there as an xAPI
`read-resource` statement (AIAAP 0.11.0). So the fleet can always answer "what
has this member read, and what did it learn from it?"

For the design and the admin side, see [datasets-design.md](datasets-design.md).

## Find and read

Any active member with a token can read every approved dataset. There's no
per-swarm restriction.

```python
from cli_agent.client import LTMSClient

client = LTMSClient("https://ltms.example.org", key_path="keys/<member>.json")
client.login()

for ds in client.datasets():          # approved, unretired datasets
    print(ds["id"], ds["name"], ds["current_version"])

hits = client.search_dataset("<dataset-id>", "wheel stalls")   # ranked, with snippets
item = client.read_item("<dataset-id>", hits[0]["key"])          # logged as a read
print(item["text"])
```

| Call | HTTP |
|---|---|
| `datasets()` | `GET /api/datasets` |
| `search_dataset(id, q, version=None, limit=20)` | `GET /api/datasets/{id}/search?q=` |
| `read_item(id, key, version=None, cycle=None)` | `GET /api/datasets/{id}/items/{key}` |

Without `version`, you get the dataset's current approved version. Pin a
version when you need a stable citation.

## External items (catalog integrations)

Some datasets live outside LTMS: a Hugging Face repo, an S3 bucket, a model
on an Ollama server, an MCP server. Reading one of their items gives
`tier: "external"`, a `locator`, and the `integration`
(`{name, kind, base_url, credential_env}`). LTMS never fetches from the
source and never holds its credentials. The member:

1. fetches the item from `base_url` and `locator`, using the secret in its own
   environment variable named by `credential_env`;
2. reports the read so it's logged and sent to the LRS like any other:

```python
item = client.read_item("<dataset-id>", "demos/pick-place")      # not logged: just the locator
# ... fetch item["locator"] from item["integration"]["base_url"] ...
client.report_read("<dataset-id>", item["version"], item["key"])  # logged, source "reported"
```

In a dream cycle, use `cycle.report(...)` instead, so the read carries the
cycle. An `mcp` integration names an MCP server that agents connect to
directly. LTMS doesn't relay MCP calls.

## Cite what you read

A record can cite up to 16 dataset items in `payload.sources`. Any record type
can, though it's usually a lesson:

```python
client.append("lesson", summary="Reset the drive wheel when it stalls",
              payload={"sources": [{"dataset": "<dataset-id>", "version": 3,
                                    "item": "drive.md#stalls"}]})
client.flush()
```

Each source is `{dataset: str, version: int > 0, item: str}`. `item` is at most
300 characters, and a `#fragment` is allowed. LTMS rejects anything else with
`bad_sources`. The dataset doesn't have to exist any more: records are
permanent, and datasets can be retired.

## Dream cycles

A dream cycle is a quiet period, for example while charging or overnight, when
a member reads reference material and writes what it learned:

```python
with client.dream_cycle(jitter_s=900) as cycle:
    for hit in client.search_dataset("<dataset-id>", "docking"):
        item = cycle.read("<dataset-id>", hit["key"])
        # ... decide what, if anything, changes ...
    cycle.lesson("Approach the dock at half speed",
                 sources=[{"dataset": "<dataset-id>", "version": item["version"], "item": item["key"]}])
```

A cycle:

1. **Waits a random 0 to `jitter_s` seconds** before it starts, so a whole
   fleet that charges at the same time doesn't read at the same moment. Use
   `jitter_s=0` to start straight away.
2. **Writes a start `event`**, tagged `dream_cycle`, with
   `payload {"cycle": <uuid>, "phase": "start"}`.
3. **Reads with the cycle id** (`cycle.read`). Each read is logged with the
   cycle, and repeat reads of the same item in one cycle are logged once. The
   cycle id becomes the statement's `registration` in the LRS.
4. **Writes lessons** (`cycle.lesson`) with `payload.cycle` and
   `payload.sources`.
5. **Writes an end `event`** with `items_read` and `lessons` on exit. It does
   this even if the body raised, adding `error` with the exception's name. It
   then flushes. If the flush fails, the records stay in the outbox for the next
   flush.

Records are signed and queued locally, so a cycle's writes work offline. Reads
need the server.

## The fleet charter

The charter is the fleet's governance: its terms, constitution or rules. An
admin publishes it as markdown, and every member must read it:

- **A new member** reads the current version before its first record is
  accepted.
- **After an update**, existing members have 14 days (`LTMS_CHARTER_GRACE_DAYS`)
  to read the new version. After that, their writes pause until they do.
  Recall and reading never pause.
- A relayed record is checked against its **author**, not the hub.

`GET /api/charter` returns `{version, hash, published_at, markdown}` and logs
the read, which reaches the LRS as `read-resource`. The token exchange and
`GET /api/records/head` include `charter: {version, hash, read, due}`.
`LTMSClient` reads the charter by itself when that says `read: false`, or
when a flush is refused with `charter_unread`. Set
`client.auto_read_charter = False` to handle it yourself (`read_charter()`).
The `hash` is the SHA-256 of the published markdown.
