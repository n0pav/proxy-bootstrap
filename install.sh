#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/proxy-bootstrap.log"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
STATE_FILE="/etc/sing-box/bootstrap.env"
CONFIG_DIR="/etc/sing-box"
CONFIG_FILE="$CONFIG_DIR/config.json"
CLIENT_INFO_FILE="$CONFIG_DIR/client-links.txt"
SYSCTL_FILE="/etc/sysctl.d/99-proxy-bootstrap.conf"
LEGACY_SYSCTL_FILE="/etc/sysctl.d/99-vpn-bootstrap.conf"
CERTBOT_HOOK="/etc/letsencrypt/renewal-hooks/deploy/restart-sing-box.sh"
SAGERNET_KEYRING="/etc/apt/keyrings/sagernet.asc"
SAGERNET_SOURCE="/etc/apt/sources.list.d/sagernet.sources"
UFW_BEFORE_RULES="/etc/ufw/before.rules"
UFW_NAT_BEGIN="# BEGIN PROXY_BOOTSTRAP NAT"
UFW_NAT_END="# END PROXY_BOOTSTRAP NAT"
LEGACY_UFW_NAT_BEGIN="# BEGIN VPN_BOOTSTRAP NAT"
LEGACY_UFW_NAT_END="# END VPN_BOOTSTRAP NAT"
NAT_SERVICE_FILE="/etc/systemd/system/proxy-bootstrap-nat.service"
LEGACY_NAT_SERVICE_FILE="/etc/systemd/system/vpn-bootstrap-nat.service"

MANAGED_UFW_RULE_HTTP="${MANAGED_UFW_RULE_HTTP:-}"
MANAGED_UFW_RULE_VLESS="${MANAGED_UFW_RULE_VLESS:-}"
MANAGED_UFW_RULE_HYSTERIA="${MANAGED_UFW_RULE_HYSTERIA:-}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fatal() {
  log "ERROR: $*"
  exit 1
}

init_logging() {
  if bool_is_true "${PROXY_BOOTSTRAP_SKIP_LOG_INIT:-false}"; then
    return 0
  fi

  install -d -m 755 "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"
  chmod 600 "$LOG_FILE"
  exec > >(tee -a "$LOG_FILE") 2>&1
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

bool_is_true() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

validate_bool_setting() {
  local name="$1"
  local value="$2"

  case "${value,,}" in
    1|0|true|false|yes|no|on|off) ;;
    *) fatal "$name must be a boolean-like value: true/false, yes/no, on/off, 1/0." ;;
  esac
}

normalize_bool() {
  if bool_is_true "$1"; then
    printf 'true'
  else
    printf 'false'
  fi
}

vless_enabled() {
  bool_is_true "$ENABLE_VLESS"
}

hysteria_enabled() {
  bool_is_true "$ENABLE_HYSTERIA"
}

validate_port() {
  local name="$1"
  local value="$2"

  [[ "$value" =~ ^[0-9]+$ ]] || fatal "$name must be an integer, got: $value"
  (( value >= 1 && value <= 65535 )) || fatal "$name must be between 1 and 65535, got: $value"
}

validate_optional_hex() {
  local name="$1"
  local value="$2"

  [ -z "$value" ] && return 0
  [[ "$value" =~ ^[0-9a-fA-F]{1,16}$ ]] || fatal "$name must be 1-16 hex characters."
}

validate_optional_uuid() {
  local name="$1"
  local value="$2"

  [ -z "$value" ] && return 0
  [[ "$value" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || fatal "$name must be a valid UUID."
}

check_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "This script must be run as root. Use: sudo bash install.sh" >&2
    exit 1
  fi
}

check_os() {
  local version_id

  [ -r /etc/os-release ] || fatal "/etc/os-release not found."
  # shellcheck disable=SC1091
  . /etc/os-release

  version_id="${VERSION_ID:-}"
  [ "${ID:-}" = "ubuntu" ] || fatal "Unsupported OS: ${ID:-unknown}. Only Ubuntu 22.04 and 24.04 are supported."

  case "$version_id" in
    22.04|24.04) ;;
    *) fatal "Unsupported Ubuntu version: $version_id. Only 22.04 and 24.04 are supported." ;;
  esac
}

load_env() {
  log "Loading configuration from $ENV_FILE."

  [ -f "$ENV_FILE" ] || fatal "Missing $ENV_FILE. Copy .env.example to .env and fill it in first."

  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a

  if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1091
    . "$STATE_FILE"
  fi

  ENABLE_VLESS="${ENABLE_VLESS:-true}"
  ENABLE_HYSTERIA="${ENABLE_HYSTERIA:-true}"
  VLESS_PORT="${VLESS_PORT:-443}"
  HYSTERIA_PORT="${HYSTERIA_PORT:-8443}"
  HYSTERIA_USER="${HYSTERIA_USER:-user1}"
  HYSTERIA_UP_MBPS="${HYSTERIA_UP_MBPS:-100}"
  HYSTERIA_DOWN_MBPS="${HYSTERIA_DOWN_MBPS:-100}"
  REALITY_SERVER_NAME="${REALITY_SERVER_NAME:-github.com}"
  REALITY_HANDSHAKE_PORT="${REALITY_HANDSHAKE_PORT:-443}"
  FIREWALL_BACKEND="${FIREWALL_BACKEND:-auto}"
  MANAGE_NAT="${MANAGE_NAT:-true}"
  CERTBOT_STAGING="${CERTBOT_STAGING:-false}"
  SING_BOX_PACKAGE="${SING_BOX_PACKAGE:-sing-box}"
  SERVER_IPV4="${SERVER_IPV4:-}"
  PRIMARY_INTERFACE="${PRIMARY_INTERFACE:-}"

  VLESS_UUID="${VLESS_UUID:-${STATE_VLESS_UUID:-}}"
  REALITY_PRIVATE_KEY="${REALITY_PRIVATE_KEY:-${STATE_REALITY_PRIVATE_KEY:-}}"
  REALITY_PUBLIC_KEY="${REALITY_PUBLIC_KEY:-${STATE_REALITY_PUBLIC_KEY:-}}"
  REALITY_SHORT_ID="${REALITY_SHORT_ID:-${STATE_REALITY_SHORT_ID:-}}"
  HYSTERIA_PASSWORD="${HYSTERIA_PASSWORD:-${STATE_HYSTERIA_PASSWORD:-}}"

  MANAGED_UFW_RULE_HTTP="${STATE_UFW_RULE_HTTP:-$MANAGED_UFW_RULE_HTTP}"
  MANAGED_UFW_RULE_VLESS="${STATE_UFW_RULE_VLESS:-$MANAGED_UFW_RULE_VLESS}"
  MANAGED_UFW_RULE_HYSTERIA="${STATE_UFW_RULE_HYSTERIA:-$MANAGED_UFW_RULE_HYSTERIA}"
}

validate_env() {
  log "Validating input variables."

  validate_bool_setting "ENABLE_VLESS" "$ENABLE_VLESS"
  validate_bool_setting "ENABLE_HYSTERIA" "$ENABLE_HYSTERIA"

  ENABLE_VLESS="$(normalize_bool "$ENABLE_VLESS")"
  ENABLE_HYSTERIA="$(normalize_bool "$ENABLE_HYSTERIA")"

  if ! vless_enabled && ! hysteria_enabled; then
    fatal "At least one protocol must be enabled. Set ENABLE_VLESS=true and/or ENABLE_HYSTERIA=true."
  fi

  if vless_enabled; then
    [ -n "${REALITY_SERVER_NAME:-}" ] || fatal "REALITY_SERVER_NAME is required when ENABLE_VLESS=true."
    validate_port "VLESS_PORT" "$VLESS_PORT"
    validate_port "REALITY_HANDSHAKE_PORT" "$REALITY_HANDSHAKE_PORT"
    validate_optional_uuid "VLESS_UUID" "${VLESS_UUID:-}"
    validate_optional_hex "REALITY_SHORT_ID" "${REALITY_SHORT_ID:-}"
  fi

  if hysteria_enabled; then
    [ -n "${HYSTERIA_DOMAIN:-}" ] || fatal "HYSTERIA_DOMAIN is required when ENABLE_HYSTERIA=true."
    [ -n "${LETSENCRYPT_EMAIL:-}" ] || fatal "LETSENCRYPT_EMAIL is required when ENABLE_HYSTERIA=true."
    [ -n "${HYSTERIA_USER:-}" ] || fatal "HYSTERIA_USER is required when ENABLE_HYSTERIA=true."

    [[ "$HYSTERIA_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || fatal "HYSTERIA_DOMAIN must look like a DNS name."
    [[ "$LETSENCRYPT_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || fatal "LETSENCRYPT_EMAIL must be a valid email address."
    validate_port "HYSTERIA_PORT" "$HYSTERIA_PORT"
    [[ "$HYSTERIA_UP_MBPS" =~ ^[0-9]+$ ]] || fatal "HYSTERIA_UP_MBPS must be an integer."
    [[ "$HYSTERIA_DOWN_MBPS" =~ ^[0-9]+$ ]] || fatal "HYSTERIA_DOWN_MBPS must be an integer."

    if [ -n "${HYSTERIA_PASSWORD:-}" ] && [ "${#HYSTERIA_PASSWORD}" -lt 8 ]; then
      fatal "HYSTERIA_PASSWORD must be at least 8 characters long."
    fi
  fi

  if vless_enabled && hysteria_enabled; then
    [ "$VLESS_PORT" != "$HYSTERIA_PORT" ] || fatal "VLESS_PORT and HYSTERIA_PORT must differ."
  fi

  case "$FIREWALL_BACKEND" in
    auto|ufw|iptables|none) ;;
    *) fatal "FIREWALL_BACKEND must be one of: auto, ufw, iptables, none." ;;
  esac

  if [ -z "$PRIMARY_INTERFACE" ]; then
    PRIMARY_INTERFACE="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
  fi
  [ -n "$PRIMARY_INTERFACE" ] || fatal "Failed to auto-detect PRIMARY_INTERFACE. Set it in .env."

  if [ -z "$SERVER_IPV4" ]; then
    SERVER_IPV4="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i+1); exit}}')"
  fi
  [ -n "$SERVER_IPV4" ] || fatal "Failed to auto-detect SERVER_IPV4. Set it in .env."

  if hysteria_enabled; then
    local dns_ips
    dns_ips="$(getent ahostsv4 "$HYSTERIA_DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u || true)"
    [ -n "$dns_ips" ] || fatal "HYSTERIA_DOMAIN does not resolve yet. Create the A record before running install."
    printf '%s\n' "$dns_ips" | grep -Fxq "$SERVER_IPV4" || fatal "HYSTERIA_DOMAIN resolves to [$dns_ips], but this server IPv4 is $SERVER_IPV4."
  fi
}

install_dependencies() {
  log "Installing base dependencies."

  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl gnupg openssl jq certbot iproute2 iptables
}

configure_sysctl() {
  log "Configuring sysctl."

  install -d -m 755 "$(dirname "$SYSCTL_FILE")"
  rm -f "$LEGACY_SYSCTL_FILE"
  cat > "$SYSCTL_FILE" <<'EOF'
# Managed by proxy-bootstrap
net.ipv4.ip_forward=1
EOF

  sysctl --system >/dev/null
}

ufw_is_active() {
  command_exists ufw && ufw status 2>/dev/null | grep -q '^Status: active$'
}

ufw_rule_exists() {
  local rule="$1"
  command_exists ufw && ufw show added 2>/dev/null | grep -Fq "ufw allow $rule"
}

ensure_ufw_rule() {
  local rule="$1"
  local comment="$2"
  local state_var="$3"

  if ufw_rule_exists "$rule"; then
    log "UFW rule already present: $rule"
    return 0
  fi

  log "Adding UFW rule: $rule"
  ufw allow "$rule" comment "$comment"
  printf -v "$state_var" '%s' "$rule"
}

remove_ufw_rule_if_managed() {
  local rule="${1:-}"

  [ -n "$rule" ] || return 0
  command_exists ufw || return 0

  if ufw_rule_exists "$rule"; then
    log "Removing managed UFW rule: $rule"
    ufw --force delete allow "$rule" || true
  fi
}

reconcile_managed_ufw_rule() {
  local existing_rule="$1"
  local desired_rule="$2"

  if [ -n "$existing_rule" ] && [ "$existing_rule" != "$desired_rule" ]; then
    remove_ufw_rule_if_managed "$existing_rule"
  fi
}

write_ufw_nat_file() {
  local temp_file
  local inserted=false

  temp_file="$(mktemp)"
  sed \
    -e "/^${UFW_NAT_BEGIN//\//\\/}$/,/^${UFW_NAT_END//\//\\/}$/d" \
    -e "/^${LEGACY_UFW_NAT_BEGIN//\//\\/}$/,/^${LEGACY_UFW_NAT_END//\//\\/}$/d" \
    "$UFW_BEFORE_RULES" > "$temp_file"

  if grep -q '^\*filter$' "$temp_file"; then
    awk -v begin="$UFW_NAT_BEGIN" -v end="$UFW_NAT_END" -v iface="$PRIMARY_INTERFACE" '
      BEGIN { inserted = 0 }
      /^\*filter$/ && inserted == 0 {
        print begin
        print "*nat"
        print ":POSTROUTING ACCEPT [0:0]"
        print "-A POSTROUTING -o " iface " -m comment --comment \"proxy-bootstrap-nat\" -j MASQUERADE"
        print "COMMIT"
        print end
        inserted = 1
      }
      { print }
    ' "$temp_file" > "${temp_file}.new"
    mv "${temp_file}.new" "$temp_file"
    inserted=true
  fi

  "$inserted" || fatal "Failed to insert managed NAT block into $UFW_BEFORE_RULES."
  install -m 644 "$temp_file" "$UFW_BEFORE_RULES"
  rm -f "$temp_file"
}

render_template() {
  local src="$1"
  local dst="$2"
  local mode="$3"
  local temp_file

  temp_file="$(mktemp)"
  cp "$src" "$temp_file"

  shift 3
  while [ "$#" -gt 1 ]; do
    local key="$1"
    local value="$2"
    local escaped
    escaped="$(printf '%s' "$value" | sed -e 's/[\/&|]/\\&/g')"
    sed -i "s|__${key}__|$escaped|g" "$temp_file"
    shift 2
  done

  install -m "$mode" "$temp_file" "$dst"
  rm -f "$temp_file"
}

ensure_iptables_nat_service() {
  log "Ensuring iptables NAT persistence service."
  remove_legacy_nat_service_if_present
  render_template \
    "$SCRIPT_DIR/templates/proxy-bootstrap-nat.service.tpl" \
    "$NAT_SERVICE_FILE" \
    644 \
    PRIMARY_INTERFACE "$PRIMARY_INTERFACE"

  systemctl daemon-reload
  systemctl enable --now proxy-bootstrap-nat.service
}

remove_legacy_nat_service_if_present() {
  if [ -f "$LEGACY_NAT_SERVICE_FILE" ] || systemctl cat vpn-bootstrap-nat.service >/dev/null 2>&1; then
    log "Removing legacy vpn-bootstrap-nat.service."
    systemctl disable --now vpn-bootstrap-nat.service >/dev/null 2>&1 || true
    rm -f "$LEGACY_NAT_SERVICE_FILE"
    systemctl daemon-reload
  fi
}

remove_nat_service_if_present() {
  remove_legacy_nat_service_if_present
  if [ -f "$NAT_SERVICE_FILE" ] || systemctl cat proxy-bootstrap-nat.service >/dev/null 2>&1; then
    log "Removing managed iptables NAT service."
    systemctl disable --now proxy-bootstrap-nat.service >/dev/null 2>&1 || true
    rm -f "$NAT_SERVICE_FILE"
    systemctl daemon-reload
  fi
}

remove_ufw_nat_block_if_present() {
  local temp_file

  [ -f "$UFW_BEFORE_RULES" ] || return 0
  if ! grep -Fq "$UFW_NAT_BEGIN" "$UFW_BEFORE_RULES" && ! grep -Fq "$LEGACY_UFW_NAT_BEGIN" "$UFW_BEFORE_RULES"; then
    return 0
  fi

  log "Removing managed UFW NAT block."
  temp_file="$(mktemp)"
  sed \
    -e "/^${UFW_NAT_BEGIN//\//\\/}$/,/^${UFW_NAT_END//\//\\/}$/d" \
    -e "/^${LEGACY_UFW_NAT_BEGIN//\//\\/}$/,/^${LEGACY_UFW_NAT_END//\//\\/}$/d" \
    "$UFW_BEFORE_RULES" > "$temp_file"
  install -m 644 "$temp_file" "$UFW_BEFORE_RULES"
  rm -f "$temp_file"
  if ufw_is_active; then
    ufw reload
  fi
}

configure_firewall() {
  log "Configuring firewall and NAT."

  local backend="$FIREWALL_BACKEND"

  if [ "$backend" = "auto" ]; then
    if ufw_is_active; then
      backend="ufw"
    elif bool_is_true "$MANAGE_NAT"; then
      backend="iptables"
    else
      backend="none"
    fi
  fi

  case "$backend" in
    ufw)
      remove_nat_service_if_present
      if hysteria_enabled; then
        reconcile_managed_ufw_rule "$MANAGED_UFW_RULE_HTTP" "80/tcp"
        reconcile_managed_ufw_rule "$MANAGED_UFW_RULE_HYSTERIA" "${HYSTERIA_PORT}/udp"
        ensure_ufw_rule "80/tcp" "proxy-bootstrap-certbot" MANAGED_UFW_RULE_HTTP
        ensure_ufw_rule "${HYSTERIA_PORT}/udp" "proxy-bootstrap-hysteria2" MANAGED_UFW_RULE_HYSTERIA
      else
        remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_HTTP"
        remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_HYSTERIA"
        MANAGED_UFW_RULE_HTTP=""
        MANAGED_UFW_RULE_HYSTERIA=""
      fi

      if vless_enabled; then
        reconcile_managed_ufw_rule "$MANAGED_UFW_RULE_VLESS" "${VLESS_PORT}/tcp"
        ensure_ufw_rule "${VLESS_PORT}/tcp" "proxy-bootstrap-vless" MANAGED_UFW_RULE_VLESS
      else
        remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_VLESS"
        MANAGED_UFW_RULE_VLESS=""
      fi

      if bool_is_true "$MANAGE_NAT"; then
        write_ufw_nat_file
      else
        remove_ufw_nat_block_if_present
      fi
      if ufw_is_active; then
        ufw reload
      else
        log "UFW rules were updated, but UFW is currently inactive."
      fi
      ;;
    iptables)
      remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_HTTP"
      remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_VLESS"
      remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_HYSTERIA"
      MANAGED_UFW_RULE_HTTP=""
      MANAGED_UFW_RULE_VLESS=""
      MANAGED_UFW_RULE_HYSTERIA=""
      remove_ufw_nat_block_if_present
      if bool_is_true "$MANAGE_NAT"; then
        ensure_iptables_nat_service
      else
        remove_nat_service_if_present
      fi
      if ufw_is_active; then
        log "UFW is active while FIREWALL_BACKEND=iptables. NAT was applied, but you must allow proxy ports in UFW yourself or switch FIREWALL_BACKEND to auto/ufw."
      else
        log "UFW is not active; port access is expected to be open on a clean VPS."
      fi
      ;;
    none)
      remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_HTTP"
      remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_VLESS"
      remove_ufw_rule_if_managed "$MANAGED_UFW_RULE_HYSTERIA"
      MANAGED_UFW_RULE_HTTP=""
      MANAGED_UFW_RULE_VLESS=""
      MANAGED_UFW_RULE_HYSTERIA=""
      remove_ufw_nat_block_if_present
      remove_nat_service_if_present
      log "Firewall management disabled via FIREWALL_BACKEND=none."
      ;;
  esac

  save_state
}

install_vpn() {
  log "Installing sing-box from the official package repository."

  install -d -m 755 /etc/apt/keyrings
  curl -fsSL https://sing-box.app/gpg.key -o "$SAGERNET_KEYRING"
  chmod 644 "$SAGERNET_KEYRING"

  cat > "$SAGERNET_SOURCE" <<'EOF'
Types: deb
URIs: https://deb.sagernet.org/
Suites: *
Components: *
Enabled: yes
Signed-By: /etc/apt/keyrings/sagernet.asc
EOF

  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y "$SING_BOX_PACKAGE"
}

save_state() {
  log "Saving bootstrap state to $STATE_FILE."

  install -d -m 700 "$CONFIG_DIR"
  {
    printf 'STATE_VLESS_UUID=%q\n' "$VLESS_UUID"
    printf 'STATE_REALITY_PRIVATE_KEY=%q\n' "$REALITY_PRIVATE_KEY"
    printf 'STATE_REALITY_PUBLIC_KEY=%q\n' "$REALITY_PUBLIC_KEY"
    printf 'STATE_REALITY_SHORT_ID=%q\n' "$REALITY_SHORT_ID"
    printf 'STATE_HYSTERIA_PASSWORD=%q\n' "$HYSTERIA_PASSWORD"
    printf 'STATE_UFW_RULE_HTTP=%q\n' "$MANAGED_UFW_RULE_HTTP"
    printf 'STATE_UFW_RULE_VLESS=%q\n' "$MANAGED_UFW_RULE_VLESS"
    printf 'STATE_UFW_RULE_HYSTERIA=%q\n' "$MANAGED_UFW_RULE_HYSTERIA"
  } > "$STATE_FILE"
  chmod 600 "$STATE_FILE"
}

generate_reality_keypair() {
  local output

  output="$(sing-box generate reality-keypair)"
  REALITY_PRIVATE_KEY="$(printf '%s\n' "$output" | awk -F': ' '/Private/ {print $2; exit}')"
  REALITY_PUBLIC_KEY="$(printf '%s\n' "$output" | awk -F': ' '/Public/ {print $2; exit}')"

  [ -n "$REALITY_PRIVATE_KEY" ] || fatal "Failed to parse generated Reality private key."
  [ -n "$REALITY_PUBLIC_KEY" ] || fatal "Failed to parse generated Reality public key."
}

generate_keys() {
  log "Ensuring proxy secrets exist."

  if vless_enabled; then
    if [ -z "${VLESS_UUID:-}" ]; then
      VLESS_UUID="$(sing-box generate uuid)"
      log "Generated VLESS UUID."
    fi

    if [ -z "${REALITY_PRIVATE_KEY:-}" ] || [ -z "${REALITY_PUBLIC_KEY:-}" ]; then
      generate_reality_keypair
      log "Generated Reality keypair."
    fi

    if [ -z "${REALITY_SHORT_ID:-}" ]; then
      REALITY_SHORT_ID="$(sing-box generate rand --hex 8)"
      log "Generated Reality short ID."
    fi
  fi

  if hysteria_enabled; then
    if [ -z "${HYSTERIA_PASSWORD:-}" ]; then
      HYSTERIA_PASSWORD="$(sing-box generate rand --hex 16)"
      log "Generated Hysteria2 password."
    fi
  fi

  save_state
}

certificate_is_fresh() {
  local cert_path="$1"
  [ -f "$cert_path" ] && openssl x509 -checkend 2592000 -noout -in "$cert_path" >/dev/null 2>&1
}

obtain_certificate() {
  local cert_dir="/etc/letsencrypt/live/$HYSTERIA_DOMAIN"
  local cert_args=()

  CERT_FULLCHAIN="$cert_dir/fullchain.pem"
  CERT_PRIVKEY="$cert_dir/privkey.pem"

  if [ -f "$CERT_FULLCHAIN" ] && [ -f "$CERT_PRIVKEY" ] && certificate_is_fresh "$CERT_FULLCHAIN"; then
    log "Existing Let's Encrypt certificate is valid for more than 30 days; skipping issuance."
    return 0
  fi

  if ss -H -ltn "( sport = :80 )" 2>/dev/null | grep -q ':80'; then
    fatal "Port 80/tcp is already in use, but certbot standalone needs it to issue/renew the certificate."
  fi

  log "Issuing or renewing Let's Encrypt certificate for $HYSTERIA_DOMAIN."

  if bool_is_true "$CERTBOT_STAGING"; then
    cert_args+=(--test-cert)
  fi

  certbot certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --keep-until-expiring \
    --email "$LETSENCRYPT_EMAIL" \
    -d "$HYSTERIA_DOMAIN" \
    "${cert_args[@]}"

  [ -f "$CERT_FULLCHAIN" ] || fatal "Certificate file was not created: $CERT_FULLCHAIN"
  [ -f "$CERT_PRIVKEY" ] || fatal "Certificate key file was not created: $CERT_PRIVKEY"
}

write_client_info() {
  log "Writing client connection details to $CLIENT_INFO_FILE."

  ENABLE_VLESS="$ENABLE_VLESS" \
  ENABLE_HYSTERIA="$ENABLE_HYSTERIA" \
  SERVER_IPV4="$SERVER_IPV4" \
  VLESS_PORT="$VLESS_PORT" \
  VLESS_UUID="${VLESS_UUID:-}" \
  REALITY_SERVER_NAME="${REALITY_SERVER_NAME:-}" \
  REALITY_PUBLIC_KEY="${REALITY_PUBLIC_KEY:-}" \
  REALITY_SHORT_ID="${REALITY_SHORT_ID:-}" \
  HYSTERIA_PORT="$HYSTERIA_PORT" \
  HYSTERIA_PASSWORD="${HYSTERIA_PASSWORD:-}" \
  HYSTERIA_DOMAIN="${HYSTERIA_DOMAIN:-}" \
  "$SCRIPT_DIR/scripts/build-client-links.sh" > "$CLIENT_INFO_FILE"

  chmod 600 "$CLIENT_INFO_FILE"
}

write_configs() {
  log "Writing sing-box configuration."

  install -d -m 700 "$CONFIG_DIR"
  CERT_FULLCHAIN="${CERT_FULLCHAIN:-}"
  CERT_PRIVKEY="${CERT_PRIVKEY:-}"

  if hysteria_enabled; then
    obtain_certificate
  else
    rm -f "$CERTBOT_HOOK"
  fi

  ENABLE_VLESS="$ENABLE_VLESS" \
  ENABLE_HYSTERIA="$ENABLE_HYSTERIA" \
  VLESS_PORT="$VLESS_PORT" \
  VLESS_UUID="${VLESS_UUID:-}" \
  REALITY_SERVER_NAME="${REALITY_SERVER_NAME:-}" \
  REALITY_HANDSHAKE_PORT="$REALITY_HANDSHAKE_PORT" \
  REALITY_PRIVATE_KEY="${REALITY_PRIVATE_KEY:-}" \
  REALITY_SHORT_ID="${REALITY_SHORT_ID:-}" \
  HYSTERIA_PORT="$HYSTERIA_PORT" \
  HYSTERIA_UP_MBPS="$HYSTERIA_UP_MBPS" \
  HYSTERIA_DOWN_MBPS="$HYSTERIA_DOWN_MBPS" \
  HYSTERIA_USER="${HYSTERIA_USER:-}" \
  HYSTERIA_PASSWORD="${HYSTERIA_PASSWORD:-}" \
  HYSTERIA_DOMAIN="${HYSTERIA_DOMAIN:-}" \
  CERT_FULLCHAIN="$CERT_FULLCHAIN" \
  CERT_PRIVKEY="$CERT_PRIVKEY" \
  "$SCRIPT_DIR/scripts/build-singbox-config.sh" > "$CONFIG_FILE"

  chmod 600 "$CONFIG_FILE"

  sing-box check -c "$CONFIG_FILE"
  sing-box format -w -c "$CONFIG_FILE" >/dev/null 2>&1 || true

  if hysteria_enabled; then
    install -d -m 755 "$(dirname "$CERTBOT_HOOK")"
    render_template \
      "$SCRIPT_DIR/templates/restart-sing-box-hook.sh.tpl" \
      "$CERTBOT_HOOK" \
      755
  fi

  write_client_info
}

ensure_sing_box_service() {
  if systemctl cat sing-box >/dev/null 2>&1; then
    log "Using existing sing-box systemd service."
    return 0
  fi

  log "Installing fallback sing-box systemd unit."
  render_template \
    "$SCRIPT_DIR/templates/sing-box.service.tpl" \
    "/etc/systemd/system/sing-box.service" \
    644 \
    SING_BOX_BIN "/usr/bin/sing-box" \
    CONFIG_FILE "$CONFIG_FILE"

  systemctl daemon-reload
}

enable_services() {
  log "Enabling and starting services."

  ensure_sing_box_service
  systemctl daemon-reload
  systemctl enable sing-box
  systemctl restart sing-box
  systemctl is-active --quiet sing-box || fatal "sing-box service failed to start."
}

wait_for_service_readiness() {
  local attempts=15
  local tcp_ready=false
  local udp_ready=false

  log "Waiting for sing-box ports to start listening."

  if ! vless_enabled; then
    tcp_ready=true
  fi

  if ! hysteria_enabled; then
    udp_ready=true
  fi

  for ((i = 1; i <= attempts; i++)); do
    if ! "$tcp_ready" && ss -H -ltn "( sport = :$VLESS_PORT )" 2>/dev/null | grep -q ":$VLESS_PORT"; then
      tcp_ready=true
    fi

    if ! "$udp_ready" && ss -H -lun "( sport = :$HYSTERIA_PORT )" 2>/dev/null | grep -q ":$HYSTERIA_PORT"; then
      udp_ready=true
    fi

    if "$tcp_ready" && "$udp_ready"; then
      log "sing-box ports are ready."
      return 0
    fi

    sleep 1
  done

  log "Port readiness wait timed out; running healthcheck anyway."
}

run_healthcheck() {
  wait_for_service_readiness
  log "Running health check."
  ENABLE_VLESS="$ENABLE_VLESS" \
  ENABLE_HYSTERIA="$ENABLE_HYSTERIA" \
  VLESS_PORT="$VLESS_PORT" \
  HYSTERIA_PORT="$HYSTERIA_PORT" \
  "$SCRIPT_DIR/scripts/healthcheck.sh"
}

main() {
  check_root
  init_logging

  log "Starting proxy bootstrap."
  check_os
  load_env
  validate_env
  install_dependencies
  configure_sysctl
  configure_firewall
  install_vpn
  generate_keys
  write_configs
  enable_services
  run_healthcheck
  log "Proxy bootstrap completed successfully."
  log "Client links saved to $CLIENT_INFO_FILE."
}

main "$@"
