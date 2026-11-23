#!/usr/bin/env bash
# Create .env from .env.example with random secrets. Safe to re-run: never overwrites a value.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
fill() {  # fill KEY only if it is currently empty
  local key="$1"
  if grep -qE "^${key}=[[:space:]]*(#.*)?$" .env; then
    local val; val="$(openssl rand -hex 16)"
    sed -i.bak -E "s|^${key}=[[:space:]]*(#.*)?$|${key}=${val}|" .env && rm -f .env.bak
  fi
}
for k in DB_PASSWORD DB_ROOT_PASSWORD SECRET_KEY CTFD_ADMIN_PASSWORD AGENT_USER_PASSWORD; do fill "$k"; done
chmod 600 .env
echo ".env ready"
