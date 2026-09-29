#!/usr/bin/env bash
# Shared dispatcher. The legacy standalone implementation remains in its entrypoints.
dispatch_deployment() {
  local action="$1"
  shift
  local root mode installed=""
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if [ -f /opt/proxy-bootstrap/remnawave/state.json ]; then
    installed=remnawave
  elif [ -f /etc/sing-box/bootstrap.env ]; then
    installed=standalone
  fi
  # Read only the mode here. Preserve the standalone entrypoint's environment precedence.
  mode="$(
    if [ -f "${ENV_FILE:-$root/.env}" ]; then
      # shellcheck disable=SC1090
      . "${ENV_FILE:-$root/.env}"
    fi
    printf '%s' "${DEPLOYMENT_MODE:-${installed:-standalone}}"
  )"
  case "$mode" in
    standalone|remnawave) ;;
    *) echo "Invalid DEPLOYMENT_MODE: expected standalone or remnawave." >&2; exit 1 ;;
  esac
  if [ -n "$installed" ] && [ "$mode" != "$installed" ]; then
    echo "Installed mode is $installed. Changing DEPLOYMENT_MODE is not a migration." >&2
    exit 1
  fi
  if [ "$mode" = remnawave ]; then
    exec bash "$root/scripts/remnawave.sh" "$action" "$@"
  fi
}
