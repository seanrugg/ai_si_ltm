#!/bin/sh
# Run pending migrations before starting the server, so an upgrade is
# just "pull, restart". Only PostgreSQL databases migrate; SQLite keeps
# create_all and skips this step.
set -eu

case "$LTMS_DATABASE_URL" in
  postgresql*)
    python -m server.migrate upgrade --backup-dir /backups
    ;;
esac

exec "$@"
