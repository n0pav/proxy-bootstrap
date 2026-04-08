#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/proxy-bootstrap.log"

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
    echo "This script must be run as root. Use: sudo bash update.sh" >&2
    exit 1
  fi
}

main() {
  check_root
  init_logging

  log "Starting proxy update."
  PROXY_BOOTSTRAP_SKIP_LOG_INIT=true bash "$SCRIPT_DIR/install.sh"

  if command -v certbot >/dev/null 2>&1; then
    log "Running certbot renew."
    certbot renew --quiet
  fi

  if systemctl cat sing-box >/dev/null 2>&1; then
    systemctl restart sing-box
  fi

  "$SCRIPT_DIR/scripts/healthcheck.sh"
  log "Proxy update completed."
}

main "$@"
