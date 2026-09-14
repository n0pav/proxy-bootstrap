#!/usr/bin/env bash
set -euo pipefail

bool_is_true() {
  case "$1" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn]) return 0 ;;
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

validate_http_url() {
  local name="$1"
  local value="$2"
  local authority host

  case "$value" in
    http://*|https://*) ;;
    *)
      printf '%s must be an absolute http:// or https:// URL.\n' "$name" >&2
      exit 1
      ;;
  esac

  authority="${value#*://}"
  authority="${authority%%[/?#]*}"
  authority="${authority##*@}"

  case "$authority" in
    \[*\]|\[*\]:*) host="${authority#\[}"; host="${host%%\]*}" ;;
    *) host="${authority%%:*}" ;;
  esac

  if [ -z "$host" ] || [[ "$value" == *[[:space:]]* ]]; then
    printf '%s must include a host and contain no whitespace.\n' "$name" >&2
    exit 1
  fi
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
  require_var "HYSTERIA_MASQUERADE_URL" "${HYSTERIA_MASQUERADE_URL:-}"
  validate_http_url "HYSTERIA_MASQUERADE_URL" "$HYSTERIA_MASQUERADE_URL"
  require_var "CERT_FULLCHAIN" "${CERT_FULLCHAIN:-}"
  require_var "CERT_PRIVKEY" "${CERT_PRIVKEY:-}"
  validate_port "HYSTERIA_PORT" "${HYSTERIA_PORT:-}"
  validate_non_negative_int "HYSTERIA_UP_MBPS" "${HYSTERIA_UP_MBPS:-}"
  validate_non_negative_int "HYSTERIA_DOWN_MBPS" "${HYSTERIA_DOWN_MBPS:-}"

  HYSTERIA_PORT_JSON="$HYSTERIA_PORT"
  HYSTERIA_UP_MBPS_JSON="$HYSTERIA_UP_MBPS"
  HYSTERIA_DOWN_MBPS_JSON="$HYSTERIA_DOWN_MBPS"

  case "${HYSTERIA_OBFS_TYPE:-}" in
    "") ;;
    salamander|gecko)
      require_var "HYSTERIA_OBFS_PASSWORD" "${HYSTERIA_OBFS_PASSWORD:-}"
      ;;
    *)
      printf 'HYSTERIA_OBFS_TYPE must be empty, salamander, or gecko.\n' >&2
      exit 1
      ;;
  esac
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
  --arg hysteria_masquerade_url "${HYSTERIA_MASQUERADE_URL:-}" \
  --arg hysteria_obfs_type "${HYSTERIA_OBFS_TYPE:-}" \
  --arg hysteria_obfs_password "${HYSTERIA_OBFS_PASSWORD:-}" \
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
        ({
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
          masquerade: {
            type: "proxy",
            url: $hysteria_masquerade_url,
            rewrite_host: true
          },
          tls: {
            enabled: true,
            server_name: $hysteria_domain,
            certificate_path: $cert_fullchain,
            key_path: $cert_privkey
          }
        } + (if $hysteria_obfs_type != "" then {
          obfs: {
            type: $hysteria_obfs_type,
            password: $hysteria_obfs_password
          }
        } else {} end))
      ] else [] end)),
    outbounds: [
      {
        type: "direct",
        tag: "direct"
      }
    ]
  }'
