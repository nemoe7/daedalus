#!/usr/bin/env bash
# Pull the Daedalus image, or build it from the source with --dev, and start Docker Compose.
set -euo pipefail
cd "$(dirname "$0")"

compose=(docker compose)
for arg in "$@"; do
  case "$arg" in
    --dev)
      compose=(docker compose -f compose.dev.yml)
      # The version under the logo: dev and the commit.
      if commit=$(git rev-parse --short HEAD 2>/dev/null); then export DAEDALUS_VERSION="dev-$commit"; fi
      ;;
    *) echo "Unknown option: $arg. The only option is --dev." >&2; exit 1 ;;
  esac
done

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
"${compose[@]}" up -d
echo "Daedalus runs on http://localhost:3357/v1"
echo "Dashboard: http://localhost:3357/ (user DAEDALUS_USERNAME or admin, password DAEDALUS_PASSWORD or DAEDALUS_MASTER_KEY)"
echo "Make API keys for your clients in the dashboard."
