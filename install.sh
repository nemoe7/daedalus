#!/usr/bin/env bash
# Set up daedalus and start it with Docker Compose. Outside a checkout, it downloads the files to ~/daedalus first.
set -euo pipefail

repo=nemoe7/daedalus

dev=0
prod=0
# Support: --dev, --dev=on/off, --dev on/off, --no-dev, --prod
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
  arg="${args[$i]}"
  case "$arg" in
    --dev)
      nxt="${args[$((i+1))]:-}"
      case "$nxt" in
        off|0|false|no|prod) prod=1; i=$((i+1)) ;;
        on|1|true|yes|dev) dev=1; i=$((i+1)) ;;
        *) dev=1 ;;
      esac
      ;;
    --dev=*)
      val="${arg#--dev=}"
      case "$val" in
        off|0|false|no|prod) prod=1 ;;
        on|1|true|yes|dev|"") dev=1 ;;
        *) echo "Unknown --dev value: $val. Use --dev, --dev=off, --no-dev, or --prod." >&2; exit 1 ;;
      esac
      ;;
    --no-dev|--prod) prod=1 ;;
    *) echo "Unknown option: $arg. Options: --dev, --dev=off, --no-dev, --prod." >&2; exit 1 ;;
  esac
  i=$((i+1))
done
# No args: keep dev=0 prod=0 initially, marker may set dev later

# A checkout has compose.yml beside this script. With curl | bash, there is no script file.
here=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)
if [ -f "$here/compose.yml" ]; then dir=$here; else dir=$HOME/daedalus; fi

# Persistent dev flag: .daedalus-dev marker auto-selects dev unless --prod/--no-dev given.
if [ -f "$dir/.daedalus-dev" ] && [ "$prod" = 0 ]; then dev=1; fi

if [ "$dev" = 1 ] && [ "$prod" = 1 ]; then
  echo "--dev and --prod/--no-dev cannot be used together." >&2; exit 1
fi
if [ "$dev" = 1 ] && { [ ! -f "$dir/compose.dev.yml" ] || [ ! -d "$dir/daedalus" ]; }; then
  echo "--dev builds the image from the source. Run it in a git checkout." >&2
  exit 1
fi
# Make --dev persistent: create marker; --prod/--no-dev/--dev=off removes it.
if [ "$dev" = 1 ]; then mkdir -p "$dir"; touch "$dir/.daedalus-dev"; fi
if [ "$prod" = 1 ]; then rm -f "$dir/.daedalus-dev"; dev=0; fi

# Files to pull: only pull compose.dev.yml when --dev or marker present
# Ordered checks: compose -> .env -> profiles -> required service dirs
if [ "$dev" = 1 ]; then
  base_files=(compose.yml compose.dev.yml .env.example config install.sh)
else
  base_files=(compose.yml .env.example config install.sh)
fi
# Service mapping: profile -> dir
# search -> services/searxng, tailscale -> services/tailscale
# Always include services parent dir check via its subdirs

need_docker=0
plan=()
if ! command -v docker >/dev/null 2>&1; then
  if [ "$(uname -s)" != Linux ]; then
    echo "Install Docker Desktop from https://docs.docker.com/get-started/get-docker/, then run this command again." >&2
    exit 1
  fi
  need_docker=1
  plan+=("Docker Engine and Docker Compose, with the official script https://get.docker.com (it asks for your password)")
fi

# Ordered check 1: compose?
missing_compose=0
for f in "${base_files[@]}"; do
  case "$f" in
    compose.yml|compose.dev.yml) [ -e "$dir/$f" ] || missing_compose=1 ;;
  esac
done
[ $missing_compose -eq 0 ] || plan+=("The daedalus files, to $dir")

# Ordered check 2: .env?
[ -f "$dir/.env" ] || plan+=("The settings file $dir/.env, with a new master key")

# Ordered check 3: read .env for COMPOSE_PROFILES to determine required service dirs
profiles=""
if [ -f "$dir/.env" ]; then
  profiles=$(grep -E '^COMPOSE_PROFILES=' "$dir/.env" | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
fi
# Normalize: comma -> space
profiles_spaced=$(echo "$profiles" | tr ',' ' ')

required_service_dirs=()
# config always required
[ -e "$dir/config" ] || required_service_dirs+=("config")
# services/searxng if search profile enabled
if echo "$profiles_spaced" | grep -qw "search"; then
  [ -e "$dir/services/searxng" ] || required_service_dirs+=("services/searxng")
fi
# services/tailscale if tailscale profile enabled
if echo "$profiles_spaced" | grep -qw "tailscale"; then
  [ -e "$dir/services/tailscale" ] || required_service_dirs+=("services/tailscale")
fi
# If required service dirs missing, add to plan
[ ${#required_service_dirs[@]} -eq 0 ] || plan+=("The daedalus files, to $dir (required service dirs: ${required_service_dirs[*]})")

# For update case: if any base file missing, need download
missing_files=0
[ $missing_compose -eq 1 ] && missing_files=1
[ -f "$dir/.env" ] || missing_files=1
for f in "${base_files[@]}"; do
  [ -e "$dir/$f" ] || { missing_files=1; break; }
done
# Also if any required service dir missing, need download
[ ${#required_service_dirs[@]} -gt 0 ] && missing_files=1

# For files list used in copy loop: base + required service dirs + services parent if needed
files=("${base_files[@]}")
# Add required service dirs to files list for copy
for d in "${required_service_dirs[@]}"; do
  # Avoid duplicate
  skip=0
  for existing in "${files[@]}"; do
    [ "$existing" = "$d" ] && skip=1
  done
  [ $skip -eq 0 ] && files+=("$d")
done
# Also always include services parent if any service dir required, to ensure parent exists
if [ ${#required_service_dirs[@]} -gt 0 ]; then
  # Ensure services parent is in files if not already
  has_services=0
  for existing in "${files[@]}"; do
    [ "$existing" = "services" ] && has_services=1
  done
  [ $has_services -eq 1 ] || files+=("services")
fi

if [ "${#plan[@]}" -gt 0 ]; then
  echo "This script installs or downloads:"
  printf '  - %s\n' "${plan[@]}"
  answer=
  # With curl | bash, stdin is the script, so the answer comes from the terminal.
  read -r -p "Continue? [y/N] " answer </dev/tty || true
  case "$answer" in
    y | Y | yes | Yes | YES) ;;
    *) echo "Stopped. Nothing changed."; exit 1 ;;
  esac
fi

if [ "$need_docker" = 1 ]; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Docker is installed. After your next login, docker works without sudo."
fi

if [ $missing_files -eq 1 ]; then
  # The newest v* tag, else main.
  tags=$(curl -fsSL "https://api.github.com/repos/$repo/tags")
  tag=$(printf '%s\n' "$tags" | grep -o '"name": *"v[0-9][^"]*"' | cut -d'"' -f4 | sort -V | tail -n 1 || true)
  ref=refs/heads/main
  [ -z "$tag" ] || ref=refs/tags/$tag
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "https://github.com/$repo/archive/$ref.tar.gz" | tar -xz -C "$tmp" --strip-components=1
  mkdir -p "$dir"
  # Only the missing files: a new run keeps the files that you changed.
  for name in "${files[@]}"; do
    [ -e "$dir/$name" ] || cp -R "$tmp/$name" "$dir/"
  done
  echo "The files of ${tag:-main} are in $dir."
fi
cd "$dir"

# .env handling: never overwrite existing .env, backup before any modification
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
  else
    echo "Missing .env.example in $dir, cannot create .env" >&2; exit 1
  fi
  # The container user must own the state mount on Linux.
  if [ "$(uname -s)" = Linux ]; then
    sed -i "s/^DAEDALUS_UID=.*/DAEDALUS_UID=$(id -u)/; s/^DAEDALUS_GID=.*/DAEDALUS_GID=$(id -g)/" .env
  fi
else
  # Backup existing .env before any in-place edit to prevent data loss
  cp .env .env.bak 2>/dev/null || true
fi
key=
if grep -q '^DAEDALUS_MASTER_KEY=$' .env; then
  key=$(od -An -tx1 -N20 /dev/urandom | tr -d ' \n')
  # Replace empty master key, preserve all other lines
  sed -i.bak "s/^DAEDALUS_MASTER_KEY=$/DAEDALUS_MASTER_KEY=$key/" .env
  rm -f .env.bak
fi
if ! grep -qsE '^DAEDALUS_MASTER_KEY=[^[:space:]]{16,}$' .env; then
  echo "Set DAEDALUS_MASTER_KEY in $dir/.env: 16 or more characters, no spaces." >&2
  echo "Your .env was backed up to $dir/.env.bak if it existed" >&2
  exit 1
fi
if [ "$(uname -s)" = Linux ] && ! grep -qs '^DAEDALUS_UID=' .env; then
  if [ -s .env ] && [ -n "$(tail -c1 .env)" ]; then echo >>.env; fi
  printf 'DAEDALUS_UID=%s\nDAEDALUS_GID=%s\n' "$(id -u)" "$(id -g)" >>.env
fi
mkdir -p .daedalus-state

compose=(compose)
version=()
if [ "$dev" = 1 ]; then
  compose=(compose -f compose.dev.yml)
  # The version under the logo: dev and the commit.
  if commit=$(git rev-parse --short HEAD 2>/dev/null); then version=("DAEDALUS_VERSION=dev-$commit"); fi
fi
# Until the next login, a new member of the docker group must use sudo.
docker=(env ${version[@]+"${version[@]}"} docker)
if [ "$(uname -s)" = Linux ] && ! docker info >/dev/null 2>&1; then docker=(sudo env ${version[@]+"${version[@]}"} docker); fi
if ! "${docker[@]}" compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is necessary. Install Docker, then run this script again." >&2
  exit 1
fi
"${docker[@]}" "${compose[@]}" up -d

echo "daedalus runs on http://localhost:3357/v1"
if [ "$(uname -s)" = Linux ] && address=$(hostname -I 2>/dev/null | awk '{print $1}') && [ -n "$address" ]; then
  echo "From another device: http://$address:3357/"
fi
echo "Dashboard: http://localhost:3357/ (user DAEDALUS_USERNAME or admin, password DAEDALUS_PASSWORD or DAEDALUS_MASTER_KEY)"
if [ -n "$key" ]; then echo "Your new master key, also the dashboard password: $key (it is in $dir/.env)"; fi
echo "Add your provider API keys to $dir/.env, then run this script again."
echo "Make API keys for your clients in the dashboard."
