#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

INPUT_VLESS_PORT="${VLESS_PORT:-}"
INPUT_HYSTERIA_PORT="${HYSTERIA_PORT:-}"
INPUT_ENABLE_VLESS="${ENABLE_VLESS:-}"
INPUT_ENABLE_HYSTERIA="${ENABLE_HYSTERIA:-}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

VLESS_PORT="${INPUT_VLESS_PORT:-${VLESS_PORT:-443}}"
HYSTERIA_PORT="${INPUT_HYSTERIA_PORT:-${HYSTERIA_PORT:-443}}"
ENABLE_VLESS="${INPUT_ENABLE_VLESS:-${ENABLE_VLESS:-true}}"
ENABLE_HYSTERIA="${INPUT_ENABLE_HYSTERIA:-${ENABLE_HYSTERIA:-true}}"

bool_is_true() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

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
  if ! bool_is_true "$ENABLE_VLESS" && ! bool_is_true "$ENABLE_HYSTERIA"; then
    echo "FAIL: both ENABLE_VLESS and ENABLE_HYSTERIA are disabled"
    return 1
  fi

  check_service

  if bool_is_true "$ENABLE_VLESS"; then
    check_tcp_port "$VLESS_PORT"
  fi

  if bool_is_true "$ENABLE_HYSTERIA"; then
    check_udp_port "$HYSTERIA_PORT"
  fi
}

main "$@"
