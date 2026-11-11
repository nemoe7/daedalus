#!/usr/bin/env bash
set -eu

SCRIPTS_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV="$HOME/.agents/.steer-venv"
PYTHON="$VENV/bin/python"
BIN_DIR="$HOME/.local/bin"
COMMAND="$BIN_DIR/steer"
PROFILE="$HOME/.bash_profile"
COMMAND_MARKER="# arena-steering-installed"
PATH_MARKER="# arena-steering-path"

fail() { echo "steer installer: $*" >&2; exit 1; }

[ -f "$SCRIPTS_DIR/steer" ] || fail "missing $SCRIPTS_DIR/steer"
if [ -L "$COMMAND" ]; then
  fail "$COMMAND already exists"
fi
if [ -e "$COMMAND" ]; then
  [ -f "$COMMAND" ] && grep -qxF "$COMMAND_MARKER" "$COMMAND" || fail "$COMMAND already exists"
fi

GIT_DIR="$(git -C "$SCRIPTS_DIR" rev-parse --absolute-git-dir 2>/dev/null)" || fail "cannot locate the Git directory"
REPO_ROOT="$(git -C "$SCRIPTS_DIR" rev-parse --show-toplevel 2>/dev/null)" || fail "cannot locate the checkout"
GIT_HOOKS="$GIT_DIR/hooks"
GIT_HOOK="$GIT_HOOKS/commit-msg"
LOCAL_HOOKS="$(git -C "$SCRIPTS_DIR" config --local --get core.hooksPath 2>/dev/null || true)"
if [ -n "$LOCAL_HOOKS" ] && [ "$LOCAL_HOOKS" != "$GIT_HOOKS" ]; then
  fail "core.hooksPath=$LOCAL_HOOKS; refuse replacement"
fi

if [ ! -x "$PYTHON" ]; then
  python3 -m venv "$VENV" || fail "cannot create $VENV"
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11)' || fail "Python 3.11 or later is required"
if ! env -u PYTHONPATH "$PYTHON" -c 'import markdown_it' >/dev/null 2>&1; then
  "$PYTHON" -m pip install --quiet 'markdown-it-py==4.2.0' || fail "cannot install markdown-it-py"
fi

"$PYTHON" - "$SCRIPTS_DIR" <<'PY' || fail "cannot initialize the default store"
import sys

sys.path.insert(0, sys.argv[1])
from arena_steering.cli import open_services, resolve_state_dir

store, *_services = open_services(resolve_state_dir())
store.close()
PY

mkdir -p "$BIN_DIR"
printf -v python_quoted '%q' "$PYTHON"
printf -v entry_quoted '%q' "$SCRIPTS_DIR/steer"
printf '#!/usr/bin/env bash\n%s\nexec %s %s "$@"\n' "$COMMAND_MARKER" "$python_quoted" "$entry_quoted" > "$COMMAND"
chmod 755 "$COMMAND"
touch "$PROFILE"
if ! grep -qxF "$PATH_MARKER" "$PROFILE"; then
  cat >> "$PROFILE" <<'EOF'

# arena-steering-path
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
EOF
fi
ROOT_MARKER="# arena-steering-root"
if ! grep -qxF "$ROOT_MARKER" "$PROFILE"; then
  printf -v root_quoted '%q' "$REPO_ROOT"
  printf '\n%s\n_steer_repo_root=%s\n' "$ROOT_MARKER" "$root_quoted" >> "$PROFILE"
fi
GATE_MARKER="# arena-steering-gate"
if ! grep -qxF "$GATE_MARKER" "$PROFILE"; then
  cat >> "$PROFILE" <<'EOF'

# arena-steering-gate
_steer_single_push_approved=
_steer_gate() {
  if [ "${_steer_gate_running:-}" = 1 ]; then return 0; fi
  if [ "${_steer_single_push_approved:-}" = 1 ]; then return 0; fi
  _steer_gate_running=1
  local command=$BASH_COMMAND reply status line _steer_route_rc=0
  if [ -z "${TZ:-}" ] && [ -n "${_steer_repo_root:-}" ] && [ -s "$_steer_repo_root/arena-state/display_tz" ]; then
    IFS= read -r TZ < "$_steer_repo_root/arena-state/display_tz" || :
    [ -n "$TZ" ] && export TZ
  fi
  if [ -z "${_steer_line:-}" ]; then
    _steer_line="$(tr '\0' ' ' < "/proc/$$/cmdline" 2>/dev/null)" || :
  fi
  [ -n "${_steer_line:-}" ] || _steer_line=$command
  if reply="$("$HOME/.local/bin/steer" pre --line "$_steer_line" --command "$command" --shell "${0##*/}" --done "${_steer_done:-}")"; then
    status=0
  else
    status=$?
  fi
  if [ "$status" = 2 ]; then
    local route_marker=${reply##*$'\n'} route_rc
    if [[ $route_marker =~ ^_steer_route_rc=([0-9]{1,3})$ ]]; then
      route_rc=${BASH_REMATCH[1]}
      if (( 10#$route_rc <= 255 )); then
        _steer_route_rc=$((10#$route_rc))
        if [[ $reply == *$'\n'* ]]; then reply=${reply%$'\n'*}; else reply=; fi
      fi
    fi
  fi
  if [ -n "$reply" ]; then
    local route_output=0
    while IFS= read -r line; do
      if [ "$route_output" = 1 ]; then
        printf '%s\n' "$line"
        continue
      fi
      case "$line" in
        _steer_route_output=1)
          if [ "$status" = 2 ]; then route_output=1; else printf '%s\n' "$line"; fi
          ;;
        _steer_done=*)
          _steer_done=${line#_steer_done=}
          _steer_done=${_steer_done#\"}
          _steer_done=${_steer_done%\"}
          ;;
        _steer_single_push_approved=1)
          _steer_single_push_approved=1
          ;;
        *) printf '%s\n' "$line" ;;
      esac
    done <<< "$reply"
  fi
  _steer_gate_running=
  case $status in
    2) exit "$_steer_route_rc" ;;
    130) exit 130 ;;
    0) return 0 ;;
    *)
      printf 'STEER WARNING: gate exited %s; work may continue.\n' "$status" >&2
      case "$command $_steer_line" in
        *"git push"*)
          printf 'PUSH BLOCKED: steering gate failed.\n' >&2
          if [ -n "${_steer_repo_root:-}" ]; then
            local audit_script="$_steer_repo_root/.agents/skills/arena-steering/scripts/arena_steering/blocked_bash.py"
            [ -f "$audit_script" ] || audit_script="$_steer_repo_root/skills/arena-steering/scripts/arena_steering/blocked_bash.py"
            if [ -f "$audit_script" ] && \
              python3 "$audit_script" "$_steer_repo_root/arena-state" "$_steer_line" "$command" >&2; then
              exit 130
            fi
          fi
          printf 'BLOCK AUDIT FAILED: unavailable hook fallback.\n' >&2
          exit 130 ;;
      esac
      ;;
  esac
}
EOF
fi
EXIT_MARKER="# arena-steering-exit"
if ! grep -qxF "$EXIT_MARKER" "$PROFILE"; then
  cat >> "$PROFILE" <<'EOF'

# arena-steering-exit
_steer_on_exit() {
  _steer_exit_rc=$1
  _steer_gate_running=1
  "$HOME/.local/bin/steer" post-exit --rc "$_steer_exit_rc" --line "${_steer_line:-}" --elapsed "$SECONDS" >&2 || :
  return "$_steer_exit_rc"
}
_steer_previous_exit=$(trap -p EXIT)
case $_steer_previous_exit in
  *arena-steering-exit-trap*) ;;
  *)
    if [ -n "$_steer_previous_exit" ]; then
      eval "_steer_exit_parts=( ${_steer_previous_exit#trap -- } )"
      _steer_exit_action=${_steer_exit_parts[0]}
      unset _steer_exit_parts
      printf -v _steer_exit_hook '%s\n%s\n%s' '_steer_exit_rc=$?; set +e; _steer_on_exit "$_steer_exit_rc"' "$_steer_exit_action" 'exit "$_steer_exit_rc" # arena-steering-exit-trap'
    else
      _steer_exit_hook='_steer_exit_rc=$?; set +e; _steer_on_exit "$_steer_exit_rc"; exit "$_steer_exit_rc" # arena-steering-exit-trap'
    fi
    trap "$_steer_exit_hook" EXIT
    unset _steer_exit_hook _steer_exit_action
    ;;
esac
unset _steer_previous_exit
EOF
fi

DEBUG_MARKER="# arena-steering-debug"
if ! grep -qxF "$DEBUG_MARKER" "$PROFILE"; then
  cat >> "$PROFILE" <<'EOF'

# arena-steering-debug
_steer_previous=$(trap -p DEBUG)
case $_steer_previous in
  *arena-steering-gate-trap*) unset _steer_previous ;;
  *)
    if [ -n "$_steer_previous" ]; then
      # Bash quotes trap -p output, so its original action stays one argument.
      eval "_steer_previous_parts=( ${_steer_previous#trap -- } )"
      _steer_previous_action=${_steer_previous_parts[0]}
      unset _steer_previous_parts
      printf -v _steer_hook '%s\n%s' "$_steer_previous_action" '_steer_gate : # arena-steering-gate-trap'
    else
      _steer_hook='_steer_gate : # arena-steering-gate-trap'
    fi
    unset _steer_previous _steer_previous_action
    # Keep the DEBUG registration last: following cleanup commands could block platform probes.
    trap "$_steer_hook" DEBUG
    ;;
esac
EOF
fi

GATE_BLOCK=$(awk '/^# arena-steering-gate$/ {copy=1} copy && /^EOF$/ {exit} copy {print}' "$SCRIPTS_DIR/install.sh")
EXIT_BLOCK=$(awk '/^# arena-steering-exit$/ {copy=1} copy && /^EOF$/ {exit} copy {print}' "$SCRIPTS_DIR/install.sh")
[ -n "$GATE_BLOCK" ] || fail "cannot read the managed gate block"
[ -n "$EXIT_BLOCK" ] || fail "cannot read the managed exit block"
STEER_GATE_BLOCK="$GATE_BLOCK" STEER_EXIT_BLOCK="$EXIT_BLOCK" "$PYTHON" - "$PROFILE" <<'PY' || fail "cannot refresh the managed profile blocks"
from pathlib import Path
import os
import sys

profile = Path(sys.argv[1])
text = profile.read_text(encoding="utf-8")
original = text
gate_start = text.index("# arena-steering-gate\n")
exit_start = text.index("# arena-steering-exit\n")
debug_start = text.index("# arena-steering-debug\n")
# Pre-EXIT profiles need the appended EXIT block moved before DEBUG.
suffix = text[debug_start:exit_start] if exit_start > debug_start else text[debug_start:]
updated = (
  text[:gate_start]
  + os.environ["STEER_GATE_BLOCK"]
  + "\n\n"
  + os.environ["STEER_EXIT_BLOCK"]
  + "\n\n"
  + suffix
)
if updated != original:
  profile.write_text(updated, encoding="utf-8")
PY

GLOBAL_IGNORE="${XDG_CONFIG_HOME:-$HOME/.config}/git/ignore"
mkdir -p "$(dirname -- "$GLOBAL_IGNORE")" || fail "cannot create $(dirname -- "$GLOBAL_IGNORE")"
touch "$GLOBAL_IGNORE" || fail "cannot create $GLOBAL_IGNORE"
if ! grep -qxF ".worktrees/" "$GLOBAL_IGNORE"; then
  printf '%s\n' ".worktrees/" >> "$GLOBAL_IGNORE" || fail "cannot update $GLOBAL_IGNORE"
fi

mkdir -p "$GIT_HOOKS" || fail "cannot create $GIT_HOOKS"
cat > "$GIT_HOOK" <<'COMMIT_MSG_HOOK' || fail "cannot write $GIT_HOOK"
#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then
  echo "commit-msg hook: expected the commit message file" >&2
  exit 1
fi
exec python3 - "$1" <<'PY'
from pathlib import Path
import re
import sys

def reject(message):
  print(f"commit-msg: {message}", file=sys.stderr)
  raise SystemExit(1)

allowed_types = "feat fix refactor perf style docs test build chore ci revert".split()
subject_limit = 72

try:
  message = Path(sys.argv[1]).read_text(encoding="utf-8")
except OSError as error:
  reject(f"cannot read commit message: {error}")

lines = [
  line
  for line in message.splitlines()
  if line.strip() and not line.lstrip().startswith("#")
]
if len(lines) != 1:
  reject("use one subject line and no body")

subject = lines[0]
if subject != subject.strip():
  reject("subject cannot start or end with whitespace")
if len(subject) > subject_limit:
  reject(f"subject must be at most {subject_limit} characters")

types = "|".join(re.escape(commit_type) for commit_type in allowed_types)
pattern = re.compile(
  rf"^(?:{types})(?:\([^()\s]+\))?!?: (?P<description>.+)$"
)
match = pattern.fullmatch(subject)
if not match:
  reject(
    "subject must match <type>[optional scope][!]: <description>; "
    f"allowed types: {', '.join(allowed_types)}"
  )
if not match.group("description")[0].islower():
  reject("description must start with a lowercase letter")
if subject.endswith("."):
  reject("subject must not end with a period")
PY
COMMIT_MSG_HOOK
chmod 755 "$GIT_HOOK" || fail "cannot make $GIT_HOOK executable"
git -C "$SCRIPTS_DIR" config --local core.hooksPath "$GIT_HOOKS" || fail "cannot set core.hooksPath"
printf 'Installed steer in %s.\n' "$COMMAND"
