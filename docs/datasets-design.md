# Design: fleet datasets and reference reading

Status: **accepted** (Sean, 11 Nov 2026). Implementation issues are linked at the end.

LTMS is the fleet management system for physical and agentic AI. Members
already learn from their own records. This design adds **external reference
material**: datasets the whole fleet can read, so a member can consult
documents, tables and sensor logs while it rests or charges (its "dream
cycle"), and write what it took from them as lessons.

## Decisions

1. **Reference reading, not model training.** Members *read* datasets: the
   text ends up in their context, not in their weights. LTMS stores, serves,
   catalogs and logs. It never trains a model, runs GPU jobs or hosts model
   files.
2. **Every dataset belongs to the fleet.** Each dataset has `scope`, which is
   `"fleet"` from day one and is the only allowed value for now. Any fleet
   member can read any approved dataset. Narrower scopes may come later as a
   new rule, without a migration.
3. **Approval is the gate.** With no per-swarm restrictions, the only control
   is at intake. A new dataset or version is `pending` (quarantined) until an
   admin approves it. Members never see pending or retired datasets.
4. **Every read is logged, and every read is an xAPI statement.** The log
   answers "what did this member read, and when, and in which cycle?". The LRS
   gets the same facts as statements, live-only per endpoint like records (#175).
5. **Light by default.** Small text datasets are the main path: LTMS extracts
   their text, indexes it and serves it. Large files can still be uploaded, but
   the dashboard warns about performance at scale and recommends a catalog
   integration instead.
6. **Large data and model files live elsewhere.** A *catalog integration* is an
   entry that describes data held somewhere else (an object store, a web
   server, a model registry such as Ollama, Hugging Face or an
   OpenAI-compatible endpoint). LTMS keeps the metadata; members fetch from the
   source and report the read.
7. **The record contract does not change.** `refs` values must be
   `"agent:seq"` (CONTRACT.md), so references to datasets go in the **payload**
   (see "Lessons cite their sources").

## Why reading avoids the problems of training

| Problem with training | With reference reading |
|---|---|
| Model collapse: training on your own outputs degrades the model | Weights never change, so the base model can't drift |
| A memory wipe doesn't remove what was trained into weights | A wipe stays a clean slate: recall is cut off and context starts fresh |
| Deleted or purged records stay inside a trained model | Deleted records simply stop being readable |
| Licences that forbid training | Reading for reference is a much narrower use. Each dataset still records its licence, and the approving admin checks it: LTMS stores a copy and serves it to many members, which some licences limit |

What remains is **lesson drift**: a wrong lesson is read back, built upon, and
the error compounds. The defences are already in the data model:

- a lesson cites its evidence (records in `refs`, dataset items in `payload.sources`);
- lessons are checked against outcomes (the Activity tab's sensing-to-learning chain);
- external datasets act as ground truth that members' records can't overwrite;
- an admin retires a wrong lesson with a superseding policy.

## How reading changes behaviour

Reading leaves no trace unless the member writes something down. A dream cycle is:

1. **start**: an `event` record tagged `dream_cycle` with `payload {"cycle": "<uuid>", "phase": "start"}`;
2. **read**: the member reads its own recent records and dataset items, passing `cycle=<uuid>` on each read;
3. **write**: it records what it learned as `lesson` (or proposes a `policy`), citing the records in `refs` and the items in `payload.sources`;
4. **end**: an `event` tagged `dream_cycle` with `payload {"cycle": "<uuid>", "phase": "end", "items_read": n, "lessons": m}`.

At decision time, recall surfaces those lessons. A decision that follows a
lesson cites it (`refs.because`), so the effect is measurable: outcomes of
decisions that cite lesson X, before and after it existed.

Two honest limits:
- reading improves **planning and decisions**, not low-level motor control;
- the benefit depends on **finding the right passage**, so text datasets are
  searchable (PostgreSQL full-text search first; embeddings only if needed later).

Clients should start cycles with random jitter (for example 0 to 15 minutes),
so a whole fleet charging at night doesn't hit LTMS at once.

## Lessons cite their sources

```json
{"type": "lesson",
 "summary": "Rugs stall the left wheel; route around them",
 "refs": {"because": ["z3-r0:4120", "z3-r0:4133"]},
 "payload": {"cycle": "6f1c…",
             "sources": [{"dataset": "<dataset id>", "version": 3, "item": "manuals/drive.md#stall"}]}}
```

`payload.sources` holds at most 16 entries of `{dataset, version, item}`;
`item` may carry a `#fragment` naming a section.

## Data model

| Table | Holds |
|---|---|
| `datasets` | `id` (uuid), `name` (unique), `description`, `kind` (`upload` \| `url` \| `integration`), `scope` (CHECK `'fleet'`), `licence` (text), `use` (`reference`), `status` (`pending` \| `approved` \| `retired`), `added_by`, `approved_by`, timestamps |
| `dataset_versions` | `dataset_id`, `version` (1, 2, …), `status` (`pending` \| `approved`), `manifest_hash` (sha256), `item_count`, `size_bytes`, `created_at`, `source_url` and `snapshot_at` for URL datasets |
| `dataset_items` | `version_id`, `key` (path inside the dataset), `media_type`, `size_bytes`, `sha256`, `tier` (`text` \| `large` \| `external`), `text` (extracted, capped at 1 MB), `meta` (JSON: rows, columns, duration, topics…), and on PostgreSQL a `tsvector` for search |
| `dataset_reads` | `member_id`, `dataset_id`, `version`, `item_key`, `cycle` (uuid, nullable), `source` (`served` \| `reported`), `ts`. One row per item per cycle (repeat reads in a cycle aren't logged again) |
| `catalog_integrations` | `name`, `kind` (`http` \| `s3` \| `huggingface` \| `ollama` \| `openai`), `base_url`, `credential_env` (the **name** of an environment variable, never the secret itself), `status` |

A new version is a new row. Old versions stay readable, so a lesson that cites
`version: 3` can always be checked. The dataset's current version is its
newest approved one.

Content is stored **content-addressed** under `<data_dir>/datasets/<sha256>` (a new
`data_dir` setting, env `LTMS_DATA_DIR`, default `./data`),
on a volume, never in PostgreSQL. PostgreSQL keeps only the catalog, the
extracted text and the read log.

## File tiers

**Accept any file; understand some.** Every file is stored whole with its hash,
size and sniffed media type (from its content, not its extension).

| Tier | Types | LTMS extracts | Served by LTMS |
|---|---|---|---|
| **Text** (priority) | txt, md, html; csv, tsv, json, jsonl, yaml, xml | text, plus schema, rows and a sample for tables | yes, searchable |
| **Documents** | pdf, docx, pptx, xlsx, epub | text (in a sandboxed subprocess, with a time and memory limit) | yes, searchable |
| **Robotics and sensor** | ROS bags (bag, mcap, db3), pcd/ply, URDF/SDF, npy/npz, hdf5, tfrecord, parquet | metadata: topics, duration, message counts, shapes, columns | yes, as files (range requests) |
| **Media** | png, jpg, webp, tiff; wav, flac, mp3; mp4, mkv | metadata: resolution, duration | yes, as files (range requests) |
| **Archives** | zip, tar, tar.gz | unpacked into one version with a manifest (guarded against zip bombs, path traversal, links) | per item |
| **Model files** | gguf, safetensors, onnx | not uploaded | no: catalog integrations only |

Limits (admin settings):
- the text and document tiers allow up to 25 MB per file by default;
- other uploads allow up to 500 MB per file by default; above that, use a catalog integration;
- uploads larger than 25 MB are resumable (chunked);
- the dashboard's upload control warns: *"Large files served to many members at once slow LTMS down. For large or frequently read data, add a catalog integration instead."*

Large items are streamed with range requests and `ETag`s, and are cacheable
by hubs.

## APIs (outline)

Admin:
- `POST /api/admin/datasets` (create, `pending`)
- upload items
- `POST /api/admin/datasets/{id}/versions/{v}/approve`
- `POST /api/admin/datasets/{id}/retire`
- `GET /api/admin/datasets` with read counts
- `POST /api/admin/integrations`

Member (approved, fleet-scoped datasets only):
- `GET /api/datasets` lists the catalog;
- `GET /api/datasets/{id}/search?q=` (version optional);
- `GET /api/datasets/{id}/items/{key}?version=&cycle=` reads an item, and is **logged**;
- `POST /api/members/me/reads` reports a read of external (integration) data.

MCP (`ltms_mcp`) gets `list_datasets`, `search_datasets` and `read_item`, so
agents read the same way robots do, logged the same way.

## xAPI and the LRS

Every row in `dataset_reads` becomes a statement:

- **actor**: the member (the same account as its record statements);
- **verb**: AIAAP `consulted` ("read reference material");
- **object**: the item, `{public_base_url}/datasets/{id}/v/{version}/items/{key}`, activity type AIAAP `dataset-item`;
- **context**:
  - `contextActivities.parent`: the dataset version, type `dataset`;
  - `contextActivities.grouping`: the fleet;
  - `registration`: the dream cycle's uuid, when there is one;
  - extensions `dataset-version`, `read-source` (`served` or `reported`).

The statement id is UUIDv5(NAMESPACE, `"read:" + <dataset_reads.id>`). Reads
are forwarded by the existing forwarder as a second stream, batched and
live-only per endpoint.

The AIAAP terms (`consulted`, `dataset`, `dataset-item`, `dream-cycle`, the two
extensions) arrive in profile 0.11 (xapi-ai).

## Dashboard

- **Datasets** page: the catalog, Add (upload or integration), pending versions
  to approve, retire, read counts, and the large-file warning.
- **Member profile → Activity tab**: a "What it read" section listing items
  read and dream cycles, with the lessons each cycle wrote. This keeps the
  seven tabs; a separate tab can come later if it grows.

## Not now

- Restricting datasets to swarms or members (scope stays `fleet`).
- Scheduled API pulls and LTMS as an MCP **client** of external servers.
- Embeddings or vector search.
- Federation (#235): sharing datasets between fleets.

## Implementation issues

Listed in the order to build; each fits one session.

| # | Issue | Needs |
|---|---|---|
| 1 | #236 Catalog: models, approval, `scope: fleet` (migration) | — |
| 2 | #237 Upload text files; extract text | #236 |
| 3 | #238 Member read and search; read log (migration) | #237 |
| 4 | #239 Reads to the LRS as xAPI | #238, AIAAP 0.11 |
| 5 | #240 Dream cycles; `payload.sources` | #238 |
| 6 | #241 MCP tools | #238 |
| 7 | #242 Datasets page | #236, #237 |
| 8 | #243 Profile: what a member read | #238, #240 |
| 9 | #244 Documents: pdf, docx, pptx, xlsx, epub | #237 |
| 10 | #245 Large, resumable uploads; range serving | #237 |
| 11 | #246 Archives | #237 |
| 12 | #247 Robotics, sensor and media metadata | #245 |
| 13 | #248 Catalog integrations, including models (migration) | #238 |
| 14 | #249 URL snapshots (allow-listed hosts) | #237 |
