#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

printf '%s\n' 'Cleaning logs and temp files...'
rm -rf -- "$ROOT/logs"/* "$ROOT/cache"/* "$ROOT/data"/*.csv
printf '%s\n' 'Clean finished.'
