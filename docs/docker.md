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

## Notes

- All configuration comes from `.env`; no secrets or site values are baked
  into the image (AGENTS.md rule 10).
