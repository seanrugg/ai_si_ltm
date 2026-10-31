# LTMS with Docker Compose

Quick start (LTMS + Postgres only):

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

## Database schema

LTMS creates its own schema on start, so there is no separate schema command
to run. (The Plesk deploy handles this differently; see `deploy/root-setup.sh`.)

Data lives in the named Compose volume `db-data`; `docker compose down` keeps
it, `docker compose down -v` removes it.

## Optional: MQTT broker

The `mqtt` service (Eclipse Mosquitto 2) is opt-in because a broker is not
required to run LTMS:

```sh
docker compose --profile mqtt up -d
```

It mounts `deploy/compose/mosquitto.conf` (auth required:
`allow_anonymous false`, a `password_file` path) and publishes port 1883.

Before starting it, create the password file and the ACL:

- Password file: generate with `mosquitto_passwd` and mount it at
  `/mosquitto/config/passwd` (add a volume mount to the `mqtt` service).
- ACL: copy the rules from `examples/echo-base/deploy/echo-base.acl` (or
  `deploy/echo-base` if the examples move has merged) into
  `deploy/compose/acl` and mount it at `/mosquitto/config/acl`, then adjust
  the topics to your swarm.

## Optional: an LRS

An `lrs` profile is intentionally not included in this stack. The official Yet
Analytics SQL LRS image name and environment variables could not be confirmed,
so I did not want to guess them (see the issue notes).

LTMS forwards records to an LRS only when an admin adds an LRS endpoint in
LTMS. Any xAPI-conformant LRS works -- it is not limited to Yet Analytics SQL
LRS -- and it runs outside this Compose stack.

## Notes

- All configuration comes from `.env`; no secrets or site values are baked
  into the image (AGENTS.md rule 10).
- The `ltms` image is built from the `Dockerfile` in this repo (issue #57),
  which must be merged to `main` before this stack builds.
