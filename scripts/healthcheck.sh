#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

INPUT_VLESS_PORT="${VLESS_PORT:-}"
INPUT_HYSTERIA_PORT="${HYSTERIA_PORT:-}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

VLESS_PORT="${INPUT_VLESS_PORT:-${VLESS_PORT:-443}}"
HYSTERIA_PORT="${INPUT_HYSTERIA_PORT:-${HYSTERIA_PORT:-8443}}"

check_service() {
  if systemctl is-active --quiet sing-box; then
    echo "OK: sing-box service is active"
  else
    echo "FAIL: sing-box service is not active"
    return 1
  fi
}

check_tcp_port() {
  local port="$1"
  if ss -H -ltn "( sport = :$port )" 2>/dev/null | grep -q ":$port"; then
    echo "OK: TCP port $port is listening"
  else
    echo "FAIL: TCP port $port is not listening"
    return 1
  fi
}

check_udp_port() {
  local port="$1"
  if ss -H -lun "( sport = :$port )" 2>/dev/null | grep -q ":$port"; then
    echo "OK: UDP port $port is listening"
  else
    echo "FAIL: UDP port $port is not listening"
    return 1
  fi
}

main() {
  check_service
  check_tcp_port "$VLESS_PORT"
  check_udp_port "$HYSTERIA_PORT"
}

main "$@"
