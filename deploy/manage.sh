#!/usr/bin/env bash
# Run an admin command on the server with the right user and settings. As root:
#   bash <app-dir>/deploy/manage.sh set-admin-password
#   bash .../deploy/manage.sh list-members
#   bash .../deploy/manage.sh integrity
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root: bash $0 $*"; exit 1; }
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OWNER="$(stat -c %U "$APP_DIR")"
set -a; . /etc/ltms/ltms.env; set +a
cd "$APP_DIR"
exec runuser -u "$OWNER" -- "$APP_DIR/.venv/bin/python" -m server.manage "$@"
