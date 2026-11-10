# Testing at scale

## Why a separate environment

Volume runs write millions of records, and deleting them afterwards is slow,
bloats PostgreSQL and skews the measurements. So scale tests get their own
database and LRS, which are thrown away afterwards; a real instance's records
are never touched. **Don't run volume tests on a host that also serves a live
instance:** they compete for CPU, disk and memory, and a mistyped flag could
hit the wrong stack.

## Disposable stack with Docker Compose

A compose project name (`-p`) gives the test stack its own containers and
volumes, so `down -v` removes only the test data:

```sh
python3 scripts/setup_env.py --mode local --with-lrs --port 18080 --out .env.test
# edit .env.test: set LRS_PORT=18081, and LTMS_PUBLIC_BASE_URL if the forwarder will run
docker compose -p ltms-test --env-file .env.test --profile lrs up -d
# ... run the test ...
docker compose -p ltms-test --env-file .env.test --profile lrs down -v   # removes everything
```

`setup_env.py` requires `--mode`; `--port` sets `LTMS_PORT`. The ports must
differ from any other stack on the same host. `.env.test` holds generated
passwords; it is git-ignored, like `.env`.

## Without Docker

Create a PostgreSQL database whose name ends in `_test` (for example
`<name>_test`), point `LTMS_DATABASE_URL` at it, and reset it between runs:

```sh
LTMS_DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>/<name>_test \
  python -m scripts.reset_test_db --confirm <name>_test
```

The script refuses any database whose name doesn't end in `_test`, any driver
other than PostgreSQL, and any `--confirm` that doesn't repeat the name
exactly. It drops and recreates the database through the `postgres`
maintenance database (the user needs `CREATEDB`), prints only the database
name, and never prints the URL. Start the server against the empty database
to create the schema.

## Resetting a standing LRS

SQL LRS's admin UI can't delete statements, and xAPI statements are
immutable. To clear an LRS you keep between runs:

1. Drop and recreate **the LRS's own database**, never the LTMS database.
2. Recreate its API key if it wasn't seeded from the environment.
3. Update the endpoint's credentials in LTMS (dashboard, LRS page).

An LRS only receives records ingested while its endpoint is on: a new
endpoint, or one turned back on after a pause, starts from the newest record
and never sends what came before. So a cleared LRS fills up again only with
records written after that point; there is no way to re-send history to it.
