#!/usr/bin/env bash
# Build the single-file `nju-connect` executable (a Python zipapp) from nju_connect/.
#   tools/build.sh [OUTPUT]   (default: dist/nju-connect)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${1:-$root/dist/nju-connect}"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

mkdir -p "$stage/nju_connect" "$(dirname "$output")"
cp "$root"/nju_connect/*.py "$stage/nju_connect/"
printf 'from nju_connect.cli import main\n\nmain()\n' >"$stage/__main__.py"
python3 -m zipapp "$stage" -p "/usr/bin/env python3" -o "$output"
chmod 755 "$output"
echo "Built $output"
