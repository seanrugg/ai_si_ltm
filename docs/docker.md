# LTMS with Docker Compose

## Quick start

One command prepares a working `.env` for the whole stack, then start it:

```sh
python3 scripts/setup_env.py --mode local   # devices on your LAN
# or
python3 scripts/setup_env.py --mode web --public-url https://ltms.example.org  # public, behind a TLS reverse proxy
docker compose up -d
```

`--public-url` sets `LTMS_PUBLIC_BASE_URL`, the base used for every xAPI statement's IRIs (`<base>/records/<agent>/<seq>`). It must be the address people and the LRS use to reach this LTMS, and it should not change once statements have been sent (it's part of every statement's IRI).

The script generates every secret, writes `.env` with mode `0600`, prints the
path, mode, profiles and admin username (never the passwords or secrets), and
tells you the next command. Add `--with-lrs` to bootstrap a SQL LRS endpoint
on first start, and/or `--with-mqtt` to include the Mosquitto broker. See
`scripts/setup_env.py --help` for all flags (`--port`, `--out`, `--force`).

<details>
<summary>Manual setup</summary>

```sh
cp deploy/compose/.env.example .env
# edit .env: set the database and admin passwords
docker compose up -d
```

That starts the `db` (Postgres 16) and `ltms` services. LTMS publishes on
`http://localhost:${LTMS_PORT:-8080}`; check it with:

```sh
curl -s http://localhost:8080/api/health   # -> {"ok":true,...}
```

The admin account (`LTMS_ADMIN_USERNAME` / `LTMS_ADMIN_PASSWORD`) is created on
the first start. Change the admin password later with:

```sh
docker compose exec ltms python -m server.manage set-admin-password
```

</details>

## Database schema

LTMS creates its own schema on start, so there is no separate schema command
to run. (The Plesk deploy handles this differently; see `deploy/root-setup.sh`.)

Data lives in the named Compose volume `db-data`; `docker compose down` keeps
it, `docker compose down -v` removes it.

## Exposure

LTMS publishes on `127.0.0.1` only, so it is reachable from this machine but
not the network. To serve it to others, put a TLS reverse proxy (nginx, Caddy,
Traefik) in front, set `FORWARDED_ALLOW_IPS` to the proxy's address as the
container sees it, and keep `LTMS_COOKIE_SECURE=true`. Set `LTMS_BIND=0.0.0.0`
only on a trusted network.

## Optional: MQTT broker (`--profile mqtt`)

For swarms whose hub and members use MQTT. Free agents don't need it; they
write to LTMS over HTTPS. Create the password file and ACL first:

```sh
touch deploy/compose/mosquitto.passwd
docker run --rm -v "$PWD/deploy/compose:/w" eclipse-mosquitto:2 \
  mosquitto_passwd -b /w/mosquitto.passwd <hub-name> <password>
# repeat with -b for each member
cp examples/echo-base/deploy/echo-base.acl deploy/compose/mosquitto.acl
# edit the ACL: replace the example swarm and member names with yours
docker compose --profile mqtt up -d
```

The broker listens on all interfaces (port `MQTT_PORT`, default 1883) so
devices on the local network can reach it. Never port-forward it to the
internet.

## Optional: an LRS (`--profile lrs`)

LTMS works without an LRS. If you want one in the same stack, the `lrs`
profile runs Yet Analytics SQL LRS with its own Postgres:

```sh
# set the LRSQL_* values in .env first
docker compose --profile lrs up -d
```

It publishes on `127.0.0.1:${LRS_PORT:-8081}`. Then, in the LTMS dashboard, an
admin adds it as an LRS endpoint: `http://lrs:8080/xapi` from inside the
stack, with `LRSQL_API_KEY` / `LRSQL_API_SECRET` as the credentials. Any other
xAPI-conformant LRS can be added the same way instead.

To use the SQL LRS admin UI through a TLS reverse proxy (for example at
`https://lrs.example.org/admin`), set `LRSQL_ALLOWED_ORIGINS` to that public
address (`setup_env.py --lrs-public-url https://lrs.example.org`) and recreate
the `lrs` container. SQL LRS rejects browser requests from origins it doesn't
know, so without it the login fails with 403 even with the right credentials.
The admin account is `LRSQL_ADMIN_USER` / `LRSQL_ADMIN_PASS` from `.env`; it is
created only the first time the LRS database starts.

## Prebuilt image (issue #105)

The `ltms` Compose service uses `image: ${LTMS_IMAGE:-ghcr.io/seanrugg/ai_si_ltm:latest}` and keeps `build: .`. Compose pulls the published image when `docker compose up` runs (so the stack deploys without SSH or a local build), and `docker compose build` still builds from the local Dockerfile when you want a local image.

A new image is published to `ghcr.io/seanrugg/ai_si_ltm` on every push to `main` and on every `v*` tag (see `.github/workflows/image.yml`). For production, pin `LTMS_IMAGE` to a tag or sha (`…:v1.2.3` or `…@sha256:…`) rather than `latest`, and make the package public in GitHub's package settings so anonymous pulls work. The image embeds no secrets or site values; all configuration comes from `.env` (AGENTS.md rule 10).

## Remote MCP (`/mcp`)

LTMS serves read-only MCP tools at `https://<your host>/mcp`. Read access is
off until you set it in `.env` and recreate the container
(`docker compose up -d`):

```
LTMS_MCP_READ_MODE=token     # or open; default off
LTMS_MCP_OPEN_SCOPE={"types":["thought","decision","lesson","policy"],"swarms":null}
```

`token` needs a read token from the dashboard's Access page. `open` needs no
token and grants only `LTMS_MCP_OPEN_SCOPE` (an empty or missing `types`
list sees nothing; payloads are never included). `"swarms": null` means every
swarm; a list such as `["<your-swarm>", null]` limits it (`null` in the list
means free agents). The Access page shows the current mode; changing it means
editing `.env`. `LTMS_PUBLIC_BASE_URL` must match the public host, because
`/mcp` only accepts requests addressed to it.

## Notes

- All configuration comes from `.env`; no secrets or site values are baked
  into the image (AGENTS.md rule 10).
- Scale and volume tests run on a disposable stack: see [testing-at-scale.md](testing-at-scale.md).

## Upgrades and migrations

Upgrades run automatically on start: the container's entrypoint runs
`python -m server.migrate upgrade --backup-dir /backups` before the server
starts, but only when `LTMS_DATABASE_URL` points at PostgreSQL (SQLite keeps
`create_all`). A backup is written before any schema change, to `/backups`
inside the container (the `ltms-backups` compose volume). To roll back,
restore that dump with `pg_restore` into a fresh database and redeploy the
previous image version.

## Fleet dataset files

Uploaded dataset files (see `docs/datasets-design.md`) are stored by content
hash under `/data/datasets` inside the container: the `ltms-data` compose
volume, set by `LTMS_DATA_DIR`. PostgreSQL keeps only the catalog and the
extracted text, so **back up `ltms-data` along with the database**: a database
restore without it lists items whose files are missing.

Limits:
- Files up to 25 MB (`LTMS_DATASET_TEXT_MAX_MB`) in a text or document format
  get their text extracted.
- Anything else, up to 500 MB (`LTMS_DATASET_UPLOAD_MAX_MB`), is stored whole
  and served as a file, with range requests.
- The dashboard uploads files over 25 MB in resumable 8 MB chunks. Partial
  uploads live in `/data/datasets/uploads`, and those unfinished after 24 hours
  are deleted nightly.

Behind a reverse proxy, allow request bodies of at least 26 MB on
`/api/admin/datasets/`. `deploy/nginx-directives.conf` does this; the default
there is 2 MB for everything else.
