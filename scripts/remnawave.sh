#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "${ENV_FILE:-$ROOT/.env}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE:-$ROOT/.env}"
  set +a
fi
command -v python3 >/dev/null || { echo 'Python 3 is required (apt-get install python3).' >&2; exit 1; }
exec python3 "$ROOT/scripts/remnawave/main.py" "$@"
