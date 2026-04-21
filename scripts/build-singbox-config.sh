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
    printf 'Missing required variable for config build: %s\n' "$name" >&2
    exit 1
  }
}

validate_port() {
  local name="$1"
  local value="$2"

  [[ "$value" =~ ^[0-9]+$ ]] || {
    printf '%s must be an integer, got: %s\n' "$name" "$value" >&2
    exit 1
  }
}

validate_non_negative_int() {
  local name="$1"
  local value="$2"

  [[ "$value" =~ ^[0-9]+$ ]] || {
    printf '%s must be an integer, got: %s\n' "$name" "$value" >&2
    exit 1
  }
}

ENABLE_VLESS="${ENABLE_VLESS:-true}"
ENABLE_HYSTERIA="${ENABLE_HYSTERIA:-true}"

VLESS_PORT_JSON=0
REALITY_HANDSHAKE_PORT_JSON=0
HYSTERIA_PORT_JSON=0
HYSTERIA_UP_MBPS_JSON=0
HYSTERIA_DOWN_MBPS_JSON=0

if ! bool_is_true "$ENABLE_VLESS" && ! bool_is_true "$ENABLE_HYSTERIA"; then
  echo "At least one protocol must be enabled for config generation." >&2
  exit 1
fi

if bool_is_true "$ENABLE_VLESS"; then
  require_var "VLESS_UUID" "${VLESS_UUID:-}"
  require_var "REALITY_SERVER_NAME" "${REALITY_SERVER_NAME:-}"
  require_var "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE_KEY:-}"
  require_var "REALITY_SHORT_ID" "${REALITY_SHORT_ID:-}"
  validate_port "VLESS_PORT" "${VLESS_PORT:-}"
  validate_port "REALITY_HANDSHAKE_PORT" "${REALITY_HANDSHAKE_PORT:-}"

  VLESS_PORT_JSON="$VLESS_PORT"
  REALITY_HANDSHAKE_PORT_JSON="$REALITY_HANDSHAKE_PORT"
fi

if bool_is_true "$ENABLE_HYSTERIA"; then
  require_var "HYSTERIA_DOMAIN" "${HYSTERIA_DOMAIN:-}"
  require_var "HYSTERIA_USER" "${HYSTERIA_USER:-}"
  require_var "HYSTERIA_PASSWORD" "${HYSTERIA_PASSWORD:-}"
  require_var "CERT_FULLCHAIN" "${CERT_FULLCHAIN:-}"
  require_var "CERT_PRIVKEY" "${CERT_PRIVKEY:-}"
  validate_port "HYSTERIA_PORT" "${HYSTERIA_PORT:-}"
  validate_non_negative_int "HYSTERIA_UP_MBPS" "${HYSTERIA_UP_MBPS:-}"
  validate_non_negative_int "HYSTERIA_DOWN_MBPS" "${HYSTERIA_DOWN_MBPS:-}"

  HYSTERIA_PORT_JSON="$HYSTERIA_PORT"
  HYSTERIA_UP_MBPS_JSON="$HYSTERIA_UP_MBPS"
  HYSTERIA_DOWN_MBPS_JSON="$HYSTERIA_DOWN_MBPS"
fi

jq -n \
  --argjson enable_vless "$(bool_is_true "$ENABLE_VLESS" && echo true || echo false)" \
  --argjson enable_hysteria "$(bool_is_true "$ENABLE_HYSTERIA" && echo true || echo false)" \
  --argjson vless_port "$VLESS_PORT_JSON" \
  --arg vless_uuid "${VLESS_UUID:-}" \
  --arg reality_server_name "${REALITY_SERVER_NAME:-}" \
  --argjson reality_handshake_port "$REALITY_HANDSHAKE_PORT_JSON" \
  --arg reality_private_key "${REALITY_PRIVATE_KEY:-}" \
  --arg reality_short_id "${REALITY_SHORT_ID:-}" \
  --argjson hysteria_port "$HYSTERIA_PORT_JSON" \
  --argjson hysteria_up_mbps "$HYSTERIA_UP_MBPS_JSON" \
  --argjson hysteria_down_mbps "$HYSTERIA_DOWN_MBPS_JSON" \
  --arg hysteria_user "${HYSTERIA_USER:-}" \
  --arg hysteria_password "${HYSTERIA_PASSWORD:-}" \
  --arg hysteria_domain "${HYSTERIA_DOMAIN:-}" \
  --arg cert_fullchain "${CERT_FULLCHAIN:-}" \
  --arg cert_privkey "${CERT_PRIVKEY:-}" \
  '{
    log: {
      level: "info",
      timestamp: true
    },
    inbounds:
      ((if $enable_vless then [
        {
          type: "vless",
          tag: "in-vless-reality",
          listen: "::",
          listen_port: $vless_port,
          users: [
            {
              name: "vless-user",
              uuid: $vless_uuid,
              flow: "xtls-rprx-vision"
            }
          ],
          tls: {
            enabled: true,
            server_name: $reality_server_name,
            reality: {
              enabled: true,
              handshake: {
                server: $reality_server_name,
                server_port: $reality_handshake_port
              },
              private_key: $reality_private_key,
              short_id: [$reality_short_id]
            }
          }
        }
      ] else [] end) +
      (if $enable_hysteria then [
        {
          type: "hysteria2",
          tag: "in-hysteria2",
          listen: "::",
          listen_port: $hysteria_port,
          up_mbps: $hysteria_up_mbps,
          down_mbps: $hysteria_down_mbps,
          users: [
            {
              name: $hysteria_user,
              password: $hysteria_password
            }
          ],
          tls: {
            enabled: true,
            server_name: $hysteria_domain,
            certificate_path: $cert_fullchain,
            key_path: $cert_privkey
          }
        }
      ] else [] end)),
    outbounds: [
      {
        type: "direct",
        tag: "direct"
      }
    ]
  }'
