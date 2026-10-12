#!/usr/bin/env bash
# Set up daedalus and start it with Docker Compose. Outside a checkout, it downloads the files to ~/daedalus first.
set -euo pipefail

repo=nemoe7/daedalus
# The image that the Docker workflow builds from main. install --dev pulls it without the source.
dev_image=ghcr.io/$repo:dev

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

# A checkout has compose.yml and Git metadata beside this script. With curl | bash, there is no script file.
src=${BASH_SOURCE[0]:-}
here=
checkout=0
if [ -n "$src" ]; then here=$(cd "$(dirname "$src")" && pwd); fi
if [ -n "$here" ] && [ -f "$here/compose.yml" ]; then
  dir=$here
  [ -e "$here/.git" ] && checkout=1
else
  dir=$HOME/daedalus
fi

# Persistent dev flag: .daedalus-dev marker auto-selects dev unless --prod/--no-dev given.
if [ -f "$dir/.daedalus-dev" ] && [ "$prod" = 0 ]; then dev=1; fi

if [ "$dev" = 1 ] && [ "$prod" = 1 ]; then
  echo "--dev and --prod/--no-dev cannot be used together." >&2; exit 1
fi
# --dev with the source builds the image. Without the source, it pulls the dev image.
if [ "$dev" = 1 ] && [ -d "$dir/daedalus" ] && [ ! -f "$dir/compose.dev.yml" ]; then
  echo "--dev needs compose.dev.yml beside the source in $dir." >&2
  exit 1
fi
# Make --dev persistent: create marker; --prod/--no-dev/--dev=off removes it.
if [ "$dev" = 1 ]; then mkdir -p "$dir"; touch "$dir/.daedalus-dev"; fi
if [ "$prod" = 1 ]; then rm -f "$dir/.daedalus-dev"; dev=0; fi

# Managed Compose/default files update as a unit outside Git checkouts. Local env files and
# compose.override.yml are user-owned and never downloaded or replaced.
service_defaults=(
  services/api/defaults.env
  services/open-webui/defaults.env
  services/webui-db/defaults.env
  services/tika/defaults.env
  services/searxng/defaults.env
  services/headroom/defaults.env
  services/tailscale/defaults.env
  services/tailscale-openwebui/defaults.env
)
managed_files=(compose.yml "${service_defaults[@]}")
# Files to pull: only pull compose.dev.yml when --dev or the marker is present.
# Ordered checks: compose -> .env -> profiles -> required service assets.
if [ "$dev" = 1 ]; then
  managed_files+=(compose.dev.yml)
  base_files=(compose.yml compose.dev.yml .env.example config hooks install.sh "${service_defaults[@]}")
else
  base_files=(compose.yml .env.example config hooks install.sh "${service_defaults[@]}")
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

# Ordered check 1: compose?
missing_compose=0
for f in "${base_files[@]}"; do
  case "$f" in
    compose.yml|compose.dev.yml) [ -e "$dir/$f" ] || missing_compose=1 ;;
  esac
done
[ $missing_compose -eq 0 ] || plan+=("The daedalus files, to $dir")

# Ordered check 2: .env?
missing_env=0
if [ ! -f "$dir/.env" ]; then
  missing_env=1
  plan+=("The settings file $dir/.env, with a new master key")
fi

# Ordered check 3: read .env for COMPOSE_PROFILES to determine required service assets.
profiles=""
if [ -f "$dir/.env" ]; then
  profiles=$(grep -E '^COMPOSE_PROFILES=' "$dir/.env" | tail -n1 | cut -d= -f2- | tr -d '\r' | tr -d '"' | tr -d "'" || true)
fi
profiles_spaced=$(echo "$profiles" | tr ',' ' ')

required_service_files=()
if echo "$profiles_spaced" | grep -qw "search"; then
  [ -e "$dir/services/searxng/settings.yml" ] || required_service_files+=("services/searxng/settings.yml")
fi
if [[ " $profiles_spaced " == *" tailscale "* ]]; then
  [ -e "$dir/services/tailscale/serve.json" ] || required_service_files+=("services/tailscale/serve.json")
fi
if [[ " $profiles_spaced " == *" tailscale-openwebui "* ]]; then
  [ -e "$dir/services/tailscale-openwebui/serve.json" ] || required_service_files+=("services/tailscale-openwebui/serve.json")
fi
[ ${#required_service_files[@]} -eq 0 ] || plan+=("The daedalus files, to $dir (required service files: ${required_service_files[*]})")

# For an update, download when a base file or profile asset is missing. Standalone runs also
# fetch the release to compare managed hashes even when every file exists.
missing_files=0
[ $missing_compose -eq 1 ] && missing_files=1
[ -f "$dir/.env" ] || missing_files=1
for f in "${base_files[@]}"; do
  [ -e "$dir/$f" ] || { missing_files=1; break; }
done
[ ${#required_service_files[@]} -gt 0 ] && missing_files=1

files=("${base_files[@]}")
for path in "${required_service_files[@]}"; do
  skip=0
  for existing in "${files[@]}"; do
    [ "$existing" = "$path" ] && skip=1
  done
  [ $skip -eq 0 ] && files+=("$path")
done

if [ "${#plan[@]}" -gt 0 ]; then
  echo "This script installs or downloads:"
  printf '  - %s\n' "${plan[@]}"
  answer=
  # With curl | bash, stdin is the script, so the answer comes from the terminal.
  if [ -c /dev/tty ]; then
    read -r -p "Continue? [y/N] " answer </dev/tty || true
  fi
  case "$answer" in
    y | Y | yes | Yes | YES) ;;
    *) echo "Stopped. Nothing changed."; exit 1 ;;
  esac
fi

# Ask only on the first install. Updates keep the profiles in .env.
ask_profile() {
  local profile="$1" label="$2" answer=
  if [ -c /dev/tty ]; then
    read -r -p "$label [y/N] " answer </dev/tty || true
  fi
  case "$answer" in
    y|Y|yes|Yes|YES) selected_profiles+=("$profile") ;;
  esac
}
profile_list=
if [ "$missing_env" = 1 ]; then
  selected_profiles=()
  ask_profile headroom "Install Headroom?"
  ask_profile webui "Install Open WebUI?"
  ask_profile tailscale "Install Tailscale for Daedalus?"
  if [[ " ${selected_profiles[*]} " == *" webui "* ]]; then
    ask_profile tika "Install Tika?"
    ask_profile search "Enable SearXNG search?"
    ask_profile tailscale-openwebui "Install Tailscale for Open WebUI?"
  fi
  profile_list=$(IFS=,; printf '%s' "${selected_profiles[*]}")
  profile_service_files=()
  if [[ " ${selected_profiles[*]} " == *" search "* ]]; then
    profile_service_files+=("services/searxng/settings.yml")
  fi
  if [[ " ${selected_profiles[*]} " == *" tailscale "* ]]; then
    profile_service_files+=("services/tailscale/serve.json")
  fi
  if [[ " ${selected_profiles[*]} " == *" tailscale-openwebui "* ]]; then
    profile_service_files+=("services/tailscale-openwebui/serve.json")
  fi
  for path in "${profile_service_files[@]}"; do
    if [ -e "$dir/$path" ]; then continue; fi
    required_service_files+=("$path")
    missing_files=1
    found=0
    for existing in "${files[@]}"; do
      if [ "$existing" = "$path" ]; then found=1; break; fi
    done
    [ $found -eq 1 ] || files+=("$path")
  done
fi

if [ "$need_docker" = 1 ]; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Docker is installed. After your next login, docker works without sudo."
fi

if [ $missing_files -eq 1 ] || [ "$checkout" = 0 ]; then
  # The newest v* tag, else main.
  tags=$(curl -fsSL "https://api.github.com/repos/$repo/tags")
  tag=$(printf '%s\n' "$tags" | grep -o '"name": *"v[0-9][^"]*"' | cut -d'"' -f4 | sort -V | tail -n 1 || true)
  ref=refs/heads/main
  # dev tracks main: the Docker workflow builds the dev image from main.
  if [ "$dev" = 0 ] && [ -n "$tag" ]; then ref=refs/tags/$tag; fi
  [ "$dev" = 0 ] || tag=
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "https://github.com/$repo/archive/$ref.tar.gz" | tar -xz -C "$tmp" --strip-components=1
  mkdir -p "$dir"

  # A main-branch installer may run before its files reach a stable tag. Support that older
  # archive when its Compose file does not refer to the new defaults yet.
  archive_managed=()
  for name in "${managed_files[@]}"; do
    if [ -f "$tmp/$name" ]; then
      archive_managed+=("$name")
    elif [[ "$name" == services/*/defaults.env ]] && ! grep -Fq "./$name" "$tmp/compose.yml"; then
      continue
    else
      echo "The downloaded release is missing $name." >&2
      exit 1
    fi
  done
  managed_files=("${archive_managed[@]}")

  if [ "$checkout" = 0 ]; then
    normalized_sha256() {
      if command -v sha256sum >/dev/null 2>&1; then
        tr -d '\r' <"$1" | sha256sum | cut -d' ' -f1
      else
        tr -d '\r' <"$1" | shasum -a 256 | cut -d' ' -f1
      fi
    }

    state_file="$dir/.daedalus-state/managed-files.sha256"
    update_dir="$dir/.daedalus-state/updates"
    legacy_file="$tmp/config/legacy-compose.sha256"
    conflicts=()
    for name in "${managed_files[@]}"; do
      source_file="$tmp/$name"
      [ -f "$source_file" ] || { echo "The downloaded release is missing $name." >&2; exit 1; }
      destination="$dir/$name"
      [ -f "$destination" ] || continue
      current_hash=$(normalized_sha256 "$destination")
      source_hash=$(normalized_sha256 "$source_file")
      [ "$current_hash" = "$source_hash" ] && continue
      recorded_hash=
      if [ -f "$state_file" ]; then
        recorded_hash=$(awk -v path="$name" '$2 == path { print $1; exit }' "$state_file")
      fi
      if [ -n "$recorded_hash" ] && [ "$current_hash" = "$recorded_hash" ]; then continue; fi
      if [ -f "$legacy_file" ] && grep -Fqx "$current_hash  $name" "$legacy_file"; then continue; fi
      conflicts+=("$name")
    done

    if [ ${#conflicts[@]} -gt 0 ]; then
      for name in "${conflicts[@]}"; do
        candidate="$update_dir/$name.new"
        mkdir -p "$(dirname "$candidate")"
        cp "$tmp/$name" "$candidate"
        echo "Managed file $name has local changes; the new file is $candidate." >&2
      done
      echo "Nothing was replaced. Move your changes to services/*/local.env or compose.override.yml, install the candidate managed files, then run the installer again." >&2
      exit 1
    fi

    state_tmp="$tmp/managed-files.sha256"
    : >"$state_tmp"
    for name in "${managed_files[@]}"; do
      destination="$dir/$name"
      mkdir -p "$(dirname "$destination")"
      source_hash=$(normalized_sha256 "$tmp/$name")
      if [ ! -f "$destination" ] || [ "$(normalized_sha256 "$destination")" != "$source_hash" ]; then
        cp "$tmp/$name" "$destination.tmp"
        mv "$destination.tmp" "$destination"
      fi
      printf '%s  %s\n' "$source_hash" "$name" >>"$state_tmp"
    done
    mkdir -p "$(dirname "$state_file")"
    mv "$state_tmp" "$state_file"
    rm -rf "$update_dir"
  fi

  # Config, scripts and profile assets remain user-owned after installation; only add them when absent.
  for name in "${files[@]}"; do
    [ -e "$tmp/$name" ] || continue
    if [ ! -e "$dir/$name" ]; then
      mkdir -p "$(dirname "$dir/$name")"
      cp -R "$tmp/$name" "$dir/$name"
    fi
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
  cp .env .env.bak
fi
if [ "$missing_env" = 1 ]; then
  if grep -q '^COMPOSE_PROFILES=' .env; then
    sed -i "s/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=$profile_list/" .env
  else
    if [ -s .env ] && [ -n "$(tail -c1 .env)" ]; then printf '\n' >>.env; fi
    printf 'COMPOSE_PROFILES=%s\n' "$profile_list" >>.env
  fi
fi
# A .env from a Windows editor carries a carriage return at the end of each line.
sed -i 's/\r$//' .env
key=
if grep -q '^DAEDALUS_MASTER_KEY=$' .env; then
  # The sk- prefix matches the API keys that daedalus makes in the dashboard.
  key=sk-$(od -An -tx1 -N20 /dev/urandom | tr -d ' \n')
  # Replace empty master key, preserve all other lines
  sed -i "s/^DAEDALUS_MASTER_KEY=$/DAEDALUS_MASTER_KEY=$key/" .env
fi
if ! grep -qsE '^DAEDALUS_MASTER_KEY=[^[:space:]]{16,}$' .env; then
  echo "Set DAEDALUS_MASTER_KEY in $dir/.env: 16 or more characters, no spaces." >&2
  echo "Your .env was backed up to $dir/.env.bak if it existed" >&2
  exit 1
fi
# Generate WEBUI_SECRET_KEY for safety if empty (keeps Open WebUI logins after update)
if grep -q '^WEBUI_SECRET_KEY=$' .env; then
  webui_key=sk-$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')
  sed -i "s/^WEBUI_SECRET_KEY=$/WEBUI_SECRET_KEY=$webui_key/" .env
fi
if [ "$(uname -s)" = Linux ] && ! grep -qs '^DAEDALUS_UID=' .env; then
  if [ -s .env ] && [ -n "$(tail -c1 .env)" ]; then echo >>.env; fi
  printf 'DAEDALUS_UID=%s\nDAEDALUS_GID=%s\n' "$(id -u)" "$(id -g)" >>.env
fi
# Both files hold the master key.
chmod 600 .env
if [ -f .env.bak ]; then chmod 600 .env.bak; fi

mkdir -p .daedalus-state

compose=(compose)
version=()
if [ "$dev" = 1 ]; then
  compose=(compose -f compose.dev.yml)
  if [ -d "$dir/daedalus" ]; then
    # The version under the logo: dev and the commit.
    if commit=$(git rev-parse --short HEAD 2>/dev/null); then version=("DAEDALUS_VERSION=dev-$commit"); fi
  else
    # No source in $dir: run the dev image of main instead of building.
    version=("DAEDALUS_DEV_IMAGE=$dev_image" "DAEDALUS_DEV_PULL=always")
  fi
fi
if [ -f compose.override.yml ]; then
  if [ "$dev" = 1 ]; then
    compose+=(-f compose.override.yml)
  else
    compose=(compose -f compose.yml -f compose.override.yml)
  fi
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
