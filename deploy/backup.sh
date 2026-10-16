#!/usr/bin/env bash
# Nightly database backup. Keeps the last 14 days. Install with cron (see the plan, Step 9).
# Reads connection settings from /etc/ltms/ltms.env.
set -euo pipefail
DEST="${LTMS_BACKUP_DIR:-/var/backups/ltms}"
KEEP_DAYS="${LTMS_BACKUP_KEEP_DAYS:-14}"

set -a; . /etc/ltms/ltms.env; set +a
mkdir -p "$DEST"; chmod 700 "$DEST"
FILE="$DEST/ltms-$(date +%Y%m%d-%H%M).dump"

PGPASSWORD="$LTMS_DB_PASSWORD" pg_dump --format=custom --no-owner \
  -h "${LTMS_DB_HOST:-localhost}" -p "${LTMS_DB_PORT:-5432}" -U "$LTMS_DB_USER" -d "$LTMS_DB_NAME" -f "$FILE"
chmod 600 "$FILE"

# A dump that can't be read is not a backup: list its contents as a check.
pg_restore --list "$FILE" >/dev/null
find "$DEST" -name 'ltms-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "backup ok: $FILE ($(du -h "$FILE" | cut -f1))"
