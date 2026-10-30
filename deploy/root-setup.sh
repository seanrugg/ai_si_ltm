#!/usr/bin/env bash
# ONE-TIME setup, run as root after the first Plesk Git deployment.
# After this, every update is just "Pull now" in Plesk (no root needed).
#
#   bash <app-dir>/deploy/root-setup.sh
#
# It works out the app folder (where this script lives) and the Plesk system
# user that owns it, then installs:
#   /etc/ltms/ltms.env                              secrets, outside the web space
#   /etc/systemd/system/ltms.service                the server, running as that user
#   /etc/systemd/system/ltms-restart.{path,service} restart after each deployment
#   /etc/cron.d/ltms-backup                         nightly database backup
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root: bash $0"; exit 1; }

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${LTMS_RUN_USER:-$(stat -c %U "$APP_DIR")}"
RUN_GROUP="$(id -gn "$RUN_USER")"
say() { printf '\n==> %s\n' "$*"; }

say "App folder: $APP_DIR   runs as: $RUN_USER:$RUN_GROUP"
[ -x "$APP_DIR/.venv/bin/uvicorn" ] || {
  echo "No $APP_DIR/.venv yet. Run the Plesk Git deployment first (it builds the venv), then re-run this."; exit 1; }

render() { sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@RUN_USER@#$RUN_USER#g" -e "s#@RUN_GROUP@#$RUN_GROUP#g" "$1"; }

say "Secrets file /etc/ltms/ltms.env"
mkdir -p /etc/ltms
if [ ! -f /etc/ltms/ltms.env ]; then
  # @APP_DIR@ in the template becomes this install's app folder.
  ( umask 027; render "$APP_DIR/deploy/ltms.env.example" > /etc/ltms/ltms.env )
  chown root:"$RUN_GROUP" /etc/ltms/ltms.env; chmod 640 /etc/ltms/ltms.env
  echo "Created. EDIT IT NOW: nano /etc/ltms/ltms.env  (database + first admin password)"
else
  chgrp "$RUN_GROUP" /etc/ltms/ltms.env; chmod 640 /etc/ltms/ltms.env
  echo "Exists; left unchanged."
fi

say "systemd units"
render "$APP_DIR/deploy/ltms.service.template" > /etc/systemd/system/ltms.service
render "$APP_DIR/deploy/ltms-restart.path.template" > /etc/systemd/system/ltms-restart.path
install -m 644 "$APP_DIR/deploy/ltms-restart.service" /etc/systemd/system/ltms-restart.service
mkdir -p "$APP_DIR/tmp" && touch "$APP_DIR/tmp/restart.txt" && chown -R "$RUN_USER:$RUN_GROUP" "$APP_DIR/tmp"
systemctl daemon-reload
systemctl enable --quiet ltms.service ltms-restart.path
systemctl restart ltms-restart.path

say "Nightly backup (02:30 server time) to /var/backups/ltms"
command -v pg_dump >/dev/null || echo "WARNING: pg_dump not found; backups will fail until the PostgreSQL client is installed."
echo "30 2 * * * root bash $APP_DIR/deploy/backup.sh >> /var/log/ltms-backup.log 2>&1" > /etc/cron.d/ltms-backup

cat <<NEXT

Setup done. Next:
  1. nano /etc/ltms/ltms.env                    (if you haven't filled it in)
  2. systemctl restart ltms
  3. curl -s http://127.0.0.1:8090/api/health   (expect {"ok":true,...})
  4. journalctl -u ltms -n 50                   (if it isn't up)
NEXT
