#!/usr/bin/env bash
set -euo pipefail

bool_is_true() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

require_var() {
  local name="$1"
  local value="${2:-}"

  [ -n "$value" ] || {
    printf 'Missing required variable for client link build: %s\n' "$name" >&2
    exit 1
  }
}

uri_encode() {
  jq -nr --arg value "$1" '$value | @uri'
}

ENABLE_VLESS="${ENABLE_VLESS:-true}"
ENABLE_HYSTERIA="${ENABLE_HYSTERIA:-true}"

if ! bool_is_true "$ENABLE_VLESS" && ! bool_is_true "$ENABLE_HYSTERIA"; then
  echo "At least one protocol must be enabled for client link generation." >&2
  exit 1
fi

require_var "SERVER_IPV4" "${SERVER_IPV4:-}"

if bool_is_true "$ENABLE_VLESS"; then
  require_var "VLESS_PORT" "${VLESS_PORT:-}"
  require_var "VLESS_UUID" "${VLESS_UUID:-}"
  require_var "REALITY_SERVER_NAME" "${REALITY_SERVER_NAME:-}"
  require_var "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC_KEY:-}"
  require_var "REALITY_SHORT_ID" "${REALITY_SHORT_ID:-}"
fi

if bool_is_true "$ENABLE_HYSTERIA"; then
  require_var "HYSTERIA_PORT" "${HYSTERIA_PORT:-}"
  require_var "HYSTERIA_PASSWORD" "${HYSTERIA_PASSWORD:-}"
  require_var "HYSTERIA_DOMAIN" "${HYSTERIA_DOMAIN:-}"
fi

if bool_is_true "$ENABLE_VLESS"; then
  cat <<EOF
VLESS Reality:
vless://$VLESS_UUID@$SERVER_IPV4:$VLESS_PORT?encryption=none&flow=xtls-rprx-vision&security=reality&sni=$REALITY_SERVER_NAME&fp=chrome&pbk=$REALITY_PUBLIC_KEY&sid=$REALITY_SHORT_ID&type=tcp&headerType=none#VLESS-Reality

EOF
fi

if bool_is_true "$ENABLE_HYSTERIA"; then
  ENCODED_HYSTERIA_PASSWORD="$(uri_encode "$HYSTERIA_PASSWORD")"
  cat <<EOF
Hysteria2:
hysteria2://$ENCODED_HYSTERIA_PASSWORD@$HYSTERIA_DOMAIN:$HYSTERIA_PORT?insecure=0&sni=$HYSTERIA_DOMAIN#Hysteria2

EOF
fi

cat <<EOF
Server IPv4: $SERVER_IPV4
EOF

if bool_is_true "$ENABLE_HYSTERIA"; then
  cat <<EOF

Hysteria domain: $HYSTERIA_DOMAIN
EOF
fi
