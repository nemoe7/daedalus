#!/usr/bin/env bash
# Set up daedalus and start it with Docker Compose. Outside a checkout, it downloads the files to ~/daedalus first.
set -euo pipefail

repo=nemoe7/daedalus
# The files that compose.yml needs, and this script for the next run.
files=(compose.yml compose.dev.yml .env.example config searxng tailscale install.sh)

dev=0
for arg in "$@"; do
  case "$arg" in
    --dev) dev=1 ;;
    *) echo "Unknown option: $arg. The only option is --dev." >&2; exit 1 ;;
  esac
done

# A checkout has compose.yml beside this script. With curl | bash, there is no script file.
here=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)
if [ -f "$here/compose.yml" ]; then dir=$here; else dir=$HOME/daedalus; fi
if [ "$dev" = 1 ] && { [ ! -f "$dir/compose.dev.yml" ] || [ ! -d "$dir/daedalus" ]; }; then
  echo "--dev builds the image from the source. Run it in a git checkout." >&2
  exit 1
fi

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
[ -f "$dir/compose.yml" ] || plan+=("The daedalus files, to $dir")
[ -f "$dir/.env" ] || plan+=("The settings file $dir/.env, with a new master key")

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

if [ ! -f "$dir/compose.yml" ]; then
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

if [ ! -f .env ]; then
  cp .env.example .env
  # The container user must own the state mount on Linux.
  if [ "$(uname -s)" = Linux ]; then
    sed -i "s/^DAEDALUS_UID=.*/DAEDALUS_UID=$(id -u)/; s/^DAEDALUS_GID=.*/DAEDALUS_GID=$(id -g)/" .env
  fi
fi
key=
if grep -q '^DAEDALUS_MASTER_KEY=$' .env; then
  key=$(od -An -tx1 -N20 /dev/urandom | tr -d ' \n')
  sed -i.bak "s/^DAEDALUS_MASTER_KEY=\$/DAEDALUS_MASTER_KEY=$key/" .env
  rm -f .env.bak
fi
if ! grep -qsE '^DAEDALUS_MASTER_KEY=[^[:space:]]{16,}$' .env; then
  echo "Set DAEDALUS_MASTER_KEY in $dir/.env: 16 or more characters, no spaces." >&2
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
