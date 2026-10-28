#!/bin/bash
# Installer for the arena automatic poll hook and the state directory. Idempotent: safe to run repeatedly, and a sandbox restore requires it again.
# Run from the repository root, where the skill lives at .agents/skills/arena-skill.
set -u

VENV="$HOME/.agents/.arena-preview-venv"
REPO_ROOT="$(pwd)"
SKILL_REL=".agents/skills/arena-skill"
STATE_REL="arena-state"
GLOBAL_IGNORE="$HOME/.gitignore_global"
HOOK="$HOME/.arena-preview-hook.sh"
PROFILE="$HOME/.bash_profile"
MARKER="# arena-preview-hook"
PATH_MARKER="# arena-preview-path"

fail(){ echo "arena-preview installer: $*" >&2; exit 1; }

# 1. Check the skill in this repository checkout.
[ -f "$SKILL_REL/scripts/preview.py" ] || fail "missing $SKILL_REL/scripts/preview.py; run this installer from repo root"
[ -x "$SKILL_REL/scripts/arena-preview" ] || fail "missing $SKILL_REL/scripts/arena-preview; update installed skill; rerun this installer"

# 2. Persistent venv for the hook's interpreter.
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || fail "cannot create venv: $VENV"
fi

# 3. Dependencies for the server and skill metadata.
if ! "$VENV/bin/python" -c 'import markdown_it, yaml' >/dev/null 2>&1; then
  "$VENV/bin/pip" install --quiet markdown-it-py PyYAML || fail "cannot install markdown-it-py and PyYAML into $VENV"
fi

# 3b. Pinned Acorn and CSS-tree parsers in ignored ~/.agents tool storage. Keep the preview usable on failure.
if ! "$VENV/bin/python" -c 'import sys; sys.path.insert(0, "skills/refs/arena-skill/scripts"); sys.path.insert(0, ".agents/skills/arena-skill/scripts"); import preview; preview.install_portable_parsers()' >/dev/null 2>&1; then
  echo "arena-preview installer: warning: portable parser setup skipped; preview remains usable" >&2
fi

# 4. Ignore the state directory through core.excludesFile, never through the repository .gitignore.
touch "$GLOBAL_IGNORE" || fail "cannot write $GLOBAL_IGNORE"
grep -qxF "$STATE_REL/" "$GLOBAL_IGNORE" || echo "$STATE_REL/" >> "$GLOBAL_IGNORE" || fail "cannot append to $GLOBAL_IGNORE"
git config --global core.excludesFile "$GLOBAL_IGNORE" || fail "cannot set core.excludesFile"
mkdir -p "$STATE_REL" || fail "cannot create $STATE_REL"
git check-ignore -q "$STATE_REL/state.sqlite3" || echo "arena-preview installer: warning: $STATE_REL is not ignored, so state shows in git status until this installer runs again" >&2

# 4b. Install a project commit-msg hook that enforces the spec without adding a trailer.
GIT_DIR="$(git rev-parse --absolute-git-dir)" || fail "cannot locate the Git directory"
GIT_HOOKS="$GIT_DIR/hooks"
GIT_HOOK="$GIT_HOOKS/commit-msg"
LOCAL_HOOKS="$(git config --local --get core.hooksPath 2>/dev/null || true)"
if [ -n "$LOCAL_HOOKS" ] && [ "$LOCAL_HOOKS" != "$GIT_HOOKS" ]; then
  fail "core.hooksPath=$LOCAL_HOOKS; refuse replacement"
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
git config --local core.hooksPath "$GIT_HOOKS" || fail "cannot set core.hooksPath"

# 5. Hook script: save $?, print the unacked-count reminder on stderr, restore the exit code.
cat > "$HOOK" <<EOF || fail "cannot write $HOOK"
#!/bin/bash
# arena-preview-hook: poll the steering inbox after every agent call.
rc=\$?
cmd="\$(tr '\\0' ' ' < /proc/\$PPID/cmdline 2>/dev/null)"
# The platform runs processes and probes through login shells. Those shells would otherwise count as agent calls and stamp call ends.
# These are the shapes the gate already ignores, so the count and the stamp answer for the agent's own calls. The hook is a child of the shell that exits, so its parent carries that shell's line.
# case: platform-shell
case "\$cmd" in
  *arena-workspace*|*"ss -ltn"*|*"netstat -ltn"*|*"source ~/.profile"*|*"mkdir -p '/home/user'"*|*"/bin/bash -l -c mkdir -p '"*|*"/bin/bash -l -c [ -e '"*|*"python3 -I - <<'PY'"*)
    exit "\$rc" ;;
esac
"$VENV/bin/python" "$REPO_ROOT/$SKILL_REL/scripts/preview.py" --reminder --rc "\$rc" --elapsed "\$SECONDS" --line "\$cmd" >&2
exit "\$rc"
EOF
chmod 755 "$HOOK"

# Drop one marked block from $PROFILE so this run's copy always wins.
strip_block() {
  awk -v marker="$1" '
    $0 == marker { skip = 1; if (n > 0 && out[n] == "") n--; next }
    skip && /^# arena-preview-/ { skip = 0 }
    !skip { out[++n] = $0 }
    END { for (i = 1; i <= n; i++) print out[i] }
  ' "$PROFILE" > "$PROFILE.new" || fail "cannot rewrite $PROFILE"
  mv "$PROFILE.new" "$PROFILE"
}

# 6. Refreshing EXIT trap in ~/.bash_profile.
touch "$PROFILE"
strip_block "$MARKER"
  cat >> "$PROFILE" <<'EOF' || fail "cannot append to $PROFILE"

# arena-preview-hook
export ARENA_PREVIEW_SHELL_HOOK=1
case "$(trap -p EXIT)" in
  *arena-preview-hook*) ;;
  *) trap 'rc=$?; "$HOME/.arena-preview-hook.sh"; exit "$rc"' EXIT ;;
esac
EOF

# 6b. Idempotent DEBUG gate function in ~/.bash_profile: block bash past the call threshold with a pending inbox.
GATE_MARKER="# arena-preview-gate"
strip_block "$GATE_MARKER"
  cat >> "$PROFILE" <<EOF || fail "cannot append to $PROFILE"

# arena-preview-gate
_arena_preview_gate() {
  # The shell command line never changes, so one read per shell serves every command.
  # case: shell-line
  if [ -z "\${TZ:-}" ] && [ -s "$REPO_ROOT/$STATE_REL/display_tz" ]; then
    export TZ="\$(head -n 1 "$REPO_ROOT/$STATE_REL/display_tz")"
  fi
  case "\${_arena_preview_line:-}" in
    "") _arena_preview_line="\$(tr '\\0' ' ' < /proc/\$\$/cmdline 2>/dev/null)" ;;
  esac
  # Every rule runs in one call: 0 continues, 2 ends the call clean, and 130 blocks it.
  # The reply names the steps this shell finished, so a later line skips them.
  case "\${_arena_preview_pre_running:-}" in 1) return 0 ;; esac
  _arena_preview_pre_running=1
  local _arena_preview_reply _arena_preview_status _arena_preview_output_line
  _arena_preview_reply="\$("$VENV/bin/python" "$REPO_ROOT/$SKILL_REL/scripts/preview.py" pre --shell "\${0##*/}" --line "\$_arena_preview_line" --command "\$BASH_COMMAND" --repo "$REPO_ROOT" --done "\${_arena_preview_done:-}")"
  _arena_preview_status=\$?
  if [ -n "\$_arena_preview_reply" ]; then
    while IFS= read -r _arena_preview_output_line; do
      case "\$_arena_preview_output_line" in
        _arena_preview_done=*) eval "\$_arena_preview_output_line" ;;
        *) printf '%s\\n' "\$_arena_preview_output_line" ;;
      esac
    done <<< "\$_arena_preview_reply"
  fi
  _arena_preview_pre_running=
  case \$_arena_preview_status in
    2) exit 0 ;;
    130) exit 130 ;;
  esac
}
EOF

# 7. Add this repository's skill scripts to PATH in new Bash shells.
strip_block "$PATH_MARKER"
  cat >> "$PROFILE" <<'EOF' || fail "cannot append to $PROFILE"

# arena-preview-path
# The installer pins the root at install time.
# A source-time lookup could read a shell outside the checkout.
_arena_preview_scripts="__ARENA_PREVIEW_ROOT__/.agents/skills/arena-skill/scripts"
if [ -x "$_arena_preview_scripts/arena-preview" ]; then
  case ":$PATH:" in
    *":$_arena_preview_scripts:"*) ;;
    *) export PATH="$_arena_preview_scripts:$PATH" ;;
  esac
fi
unset _arena_preview_scripts
EOF
sed -i "s|__ARENA_PREVIEW_ROOT__|$REPO_ROOT|" "$PROFILE" || fail "cannot pin PATH root in $PROFILE"

cat <<'EOF'
commitlint: ok
gitignore: ok
arena-preview: ok
next action: `start_process`: `arena-preview serve --port 8000`
EOF

# 8. Install the DEBUG gate trap last in ~/.bash_profile.
GATE_TRAP_MARKER="# arena-preview-gate-trap"
strip_block "$GATE_TRAP_MARKER"
  cat >> "$PROFILE" <<'EOF' || fail "cannot append to $PROFILE"

# arena-preview-gate-trap
trap '_arena_preview_gate : # arena-preview-gate' DEBUG
EOF
