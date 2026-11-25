#!/usr/bin/env bash
# Build the App Lab app: copies this folder and unpacks ltms-harness (built from ../../harness)
# into python/vendor, so the board needs no access to GitHub or PyPI for the harness.
#
#   examples/app-lab-member/make-app.sh            -> dist/ltms-member/ and dist/ltms-member.zip
#   examples/app-lab-member/make-app.sh OUT_DIR
#
# Needs python3 with pip. Import the zip in App Lab (My Apps -> Import), or copy the
# folder to ~/ArduinoApps/ on the board.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$HERE/dist}"
APP="$OUT/ltms-member"
WHEELS="$(mktemp -d)"
trap 'rm -rf "$WHEELS"' EXIT

rm -rf "$APP" "$OUT/ltms-member.zip"
mkdir -p "$APP"
cp -R "$HERE/app.yaml" "$HERE/README.md" "$HERE/sketch" "$HERE/python" "$APP/"
rm -rf "$APP/python/vendor" "$APP/python/__pycache__"

python3 -m pip wheel --quiet --no-deps --wheel-dir "$WHEELS" "$REPO/harness"
WHEEL="$(ls "$WHEELS"/ltms_harness-*.whl)"
mkdir -p "$APP/python/vendor"
python3 - "$WHEEL" "$APP/python/vendor" <<'PY'
import sys, zipfile
wheel, dest = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(wheel) as z:
    # The packages, and the wheel's metadata so importlib.metadata reports the harness version.
    names = [n for n in z.namelist()
             if n.split("/")[0] in ("ltms_contract", "cli_agent") or n.split("/")[0].endswith(".dist-info")]
    z.extractall(dest, names)
print(f"vendored {len(names)} files from {wheel.rsplit('/', 1)[-1]}")
PY
{
  echo "ltms-harness unpacked from $(basename "$WHEEL")"
  echo "built from $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo 'an unknown commit') on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$APP/python/vendor/VENDORED.txt"

(cd "$OUT" && rm -f ltms-member.zip && python3 -m zipfile -c ltms-member.zip ltms-member)
echo "App: $APP"
echo "Zip: $OUT/ltms-member.zip"
