#!/usr/bin/env bash
# Build the Daedalus image and start the service with Docker Compose.
set -euo pipefail
cd "$(dirname "$0")"

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is necessary. Install Docker, then run this script again." >&2
  exit 1
fi
mkdir -p .daedalus-state
if [ ! -f .env ]; then
  echo "No .env file. Daedalus skips each provider that has no API key."
fi
if ! grep -qsE '^DAEDALUS_MASTER_KEY=[^[:space:]]{16,}$' .env; then
  echo "Add DAEDALUS_MASTER_KEY to .env: 16 or more characters, no spaces." >&2
  exit 1
fi
# The container user must own the state mount on Linux.
if [ "$(uname -s)" = "Linux" ] && ! grep -qs '^DAEDALUS_UID=' .env; then
  if [ -s .env ] && [ -n "$(tail -c1 .env)" ]; then echo >> .env; fi
  printf 'DAEDALUS_UID=%s\nDAEDALUS_GID=%s\n' "$(id -u)" "$(id -g)" >> .env
fi
docker compose up -d --build
echo "Daedalus runs on http://localhost:3357/v1"
echo "Dashboard: http://localhost:3357/ (user admin, password DAEDALUS_MASTER_KEY)"
echo "Make API keys for your clients in the dashboard."
