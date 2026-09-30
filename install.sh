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
if [ "$dev" = 1 ]; then
  files=(compose.yml compose.dev.yml .env.example config services install.sh)
else
  files=(compose.yml .env.example config services install.sh)
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
