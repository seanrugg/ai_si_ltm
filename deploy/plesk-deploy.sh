#!/usr/bin/env bash
# Runs after every pull. In Plesk: Git -> (repository) -> Deployment settings ->
# "Enable additional deployment actions":   bash deploy/plesk-deploy.sh
# Runs as the subscription's system user (no root). Output appears in Plesk's deploy log.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "LTMS deploy in $(pwd)"

# Python 3.11+: take the newest one the server has.
PY="${LTMS_PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || { echo "ERROR: no Python 3.11+ found. See PLESK_DEPLOYMENT_PLAN.md, 'Python version'."; exit 1; }

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet .
echo "python: $(.venv/bin/python --version)"

# Node: Plesk ships its own under /opt/plesk/node/<version>/bin.
NODE_BIN=$(ls -d /opt/plesk/node/*/bin 2>/dev/null | sort -V | tail -1 || true)
[ -n "$NODE_BIN" ] && export PATH="$NODE_BIN:$PATH"
if command -v npm >/dev/null; then
  (cd dashboard && npm ci --no-audit --no-fund --silent && npm run build --silent)
  echo "dashboard built with node $(node --version)"
else
  echo "WARNING: npm not found; dashboard NOT rebuilt (turn on Plesk's Node.js support)."
fi

# Ask systemd (ltms-restart.path) to restart the server, then check it came back.
mkdir -p tmp && touch tmp/restart.txt
if ! systemctl is-enabled ltms-restart.path >/dev/null 2>&1; then
  echo "NOTE: restart watcher not installed yet. First deployment? Run deploy/root-setup.sh as root once."
  exit 0
fi
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  if curl -fsS http://127.0.0.1:8090/api/health >/dev/null 2>&1; then echo "LTMS is up."; exit 0; fi
done
echo "ERROR: LTMS did not come back within 20 s. As root: journalctl -u ltms -n 80"
exit 1
