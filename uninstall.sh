#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/proxy-bootstrap.log"
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

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

init_logging() {
  install -d -m 755 "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"
  chmod 600 "$LOG_FILE"
  exec > >(tee -a "$LOG_FILE") 2>&1
}

check_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "This script must be run as root. Use: sudo bash uninstall.sh" >&2
    exit 1
  fi
}

load_state() {
  if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1091
    . "$STATE_FILE"
  fi
}

ufw_is_active() {
  command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active$'
}

remove_ufw_rule_if_added() {
  local rule="$1"
  local added="$2"

  if [ -n "$added" ] && ufw_is_active; then
    log "Removing UFW rule: $rule"
    ufw --force delete allow "$rule" || true
  fi
}

remove_ufw_nat_block() {
  local temp_file

  [ -f "$UFW_BEFORE_RULES" ] || return 0
  temp_file="$(mktemp)"
  sed \
    -e "/^${UFW_NAT_BEGIN//\//\\/}$/,/^${UFW_NAT_END//\//\\/}$/d" \
    -e "/^${LEGACY_UFW_NAT_BEGIN//\//\\/}$/,/^${LEGACY_UFW_NAT_END//\//\\/}$/d" \
    "$UFW_BEFORE_RULES" > "$temp_file"
  install -m 644 "$temp_file" "$UFW_BEFORE_RULES"
  rm -f "$temp_file"
  ufw reload || true
}

remove_nat_service() {
  if [ -f "$NAT_SERVICE_FILE" ] || systemctl cat proxy-bootstrap-nat.service >/dev/null 2>&1; then
    log "Stopping and disabling proxy-bootstrap-nat.service."
    systemctl disable --now proxy-bootstrap-nat.service || true
  fi

  rm -f "$NAT_SERVICE_FILE"

  if [ -f "$LEGACY_NAT_SERVICE_FILE" ] || systemctl cat vpn-bootstrap-nat.service >/dev/null 2>&1; then
    log "Stopping and disabling legacy vpn-bootstrap-nat.service."
    systemctl disable --now vpn-bootstrap-nat.service || true
  fi

  rm -f "$LEGACY_NAT_SERVICE_FILE"
}

remove_sing_box_service_override() {
  if [ -f /etc/systemd/system/sing-box.service ] && { grep -Fq 'Managed by proxy-bootstrap' /etc/systemd/system/sing-box.service || grep -Fq 'Managed by vpn-bootstrap' /etc/systemd/system/sing-box.service; }; then
    log "Removing fallback sing-box systemd unit."
    rm -f /etc/systemd/system/sing-box.service
  fi
}

remove_package_if_installed() {
  local package="$1"

  if dpkg -s "$package" >/dev/null 2>&1; then
    log "Removing package: $package"
    export DEBIAN_FRONTEND=noninteractive
    apt-get remove -y "$package"
  fi
}

main() {
  check_root
  init_logging
  load_state

  log "Starting proxy uninstall."

  if systemctl cat sing-box >/dev/null 2>&1; then
    systemctl disable --now sing-box || true
  fi

  remove_ufw_rule_if_added "${STATE_UFW_RULE_HTTP:-}" "${STATE_UFW_RULE_HTTP:-}"
  remove_ufw_rule_if_added "${STATE_UFW_RULE_VLESS:-}" "${STATE_UFW_RULE_VLESS:-}"
  remove_ufw_rule_if_added "${STATE_UFW_RULE_HYSTERIA:-}" "${STATE_UFW_RULE_HYSTERIA:-}"
  remove_ufw_nat_block
  remove_nat_service

  rm -f "$CERTBOT_HOOK"
  rm -f "$CONFIG_FILE"
  rm -f "$CLIENT_INFO_FILE"
  rm -f "$STATE_FILE"
  rm -f "$SYSCTL_FILE"
  rm -f "$LEGACY_SYSCTL_FILE"

  sysctl --system >/dev/null || true
  remove_sing_box_service_override

  remove_package_if_installed "sing-box"
  remove_package_if_installed "sing-box-beta"

  rm -f "$SAGERNET_SOURCE"
  rm -f "$SAGERNET_KEYRING"

  if [ -d "$CONFIG_DIR" ] && [ -z "$(find "$CONFIG_DIR" -mindepth 1 -maxdepth 1 2>/dev/null)" ]; then
    rmdir "$CONFIG_DIR"
  fi

  systemctl daemon-reload || true
  log "Proxy uninstall completed."
  log "Let's Encrypt certificates were left in place intentionally."
}

main "$@"
