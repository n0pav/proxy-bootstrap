#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_sh="$repo_root/install.sh"

for version in 22.04 24.04 26.04; do
  if ! grep -Eq "(^|[|[:space:]])${version//./\\.}([|)])" "$install_sh"; then
    printf 'install.sh does not allow Ubuntu %s\n' "$version" >&2
    exit 1
  fi
done

if ! grep -Fq 'Ubuntu 22.04, 24.04 and 26.04 are supported' "$install_sh"; then
  printf 'install.sh unsupported-version message is out of sync\n' >&2
  exit 1
fi
