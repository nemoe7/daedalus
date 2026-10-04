#!/bin/bash
# Installer for the arena-preview-steering automatic poll hook and the state directory.
# Idempotent: safe to run repeatedly, and a sandbox restore requires it again.
# Run from the repository root, where the skill lives at .agents/skills/arena-preview-steering.
set -u

VENV="$HOME/.agents/.arena-preview-venv"
REPO_ROOT="$(pwd)"
SKILL_REL=".agents/skills/arena-preview-steering"
STATE_REL="arena-state"
GLOBAL_IGNORE="$HOME/.gitignore_global"
HOOK="$HOME/.arena-preview-hook.sh"
PROFILE="$HOME/.bash_profile"
MARKER="# arena-preview-hook"
PATH_MARKER="# arena-preview-path"

fail(){ echo "arena-preview installer: $*" >&2; exit 1; }

# 1. Verify the skill in this repository checkout.
[ -f "$SKILL_REL/scripts/preview.py" ] || fail "missing $SKILL_REL/scripts/preview.py; run this installer from the repository root"
[ -x "$SKILL_REL/scripts/arena-preview" ] || fail "missing $SKILL_REL/scripts/arena-preview; update the installed skill and rerun this installer"

# 2. Persistent venv for the hook's interpreter.
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || fail "cannot create venv at $VENV"
fi

# 3. markdown-it-py into the venv, so serve can start from it too.
if ! "$VENV/bin/python" -c 'import markdown_it' >/dev/null 2>&1; then
  "$VENV/bin/pip" install --quiet markdown-it-py || fail "cannot install markdown-it-py into $VENV"
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
  fail "core.hooksPath is already set to $LOCAL_HOOKS; refusing to replace it"
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
echo "arena-preview installer: commit-msg validation active at $GIT_HOOK"

# 5. Hook script: save $?, print the unacked-count reminder on stderr, restore the exit code.
cat > "$HOOK" <<EOF || fail "cannot write $HOOK"
#!/bin/bash
# arena-preview-hook: poll the steering inbox after every agent call.
rc=\$?
"$VENV/bin/python" "$REPO_ROOT/$SKILL_REL/scripts/preview.py" --reminder >&2
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
  # Only an agent call shell is gated: Arena runs one as the shell binary with a
  # command. It hosts every long-lived process in a login shell over a launcher
  # script, and an exit in that shell takes the hosted process down.
  case "\${0##*/}" in bash|sh|dash|ksh|zsh) ;; *) return 0 ;; esac
  # Arena's own probe and bookkeeping shells must never meet the gate: an exit
  # in one reads as a dead preview or sandbox while the server stays up. Mark
  # such a shell once from its command line and leave the rest of it alone.
  case "\${_arena_preview_platform:-}" in 1) return 0 ;; esac
  case "\$(tr '\\0' ' ' < /proc/\$\$/cmdline 2>/dev/null)" in
    *arena-workspace*|*"ss -ltn"*|*"netstat -ltn"*|*"source ~/.profile"*|*"mkdir -p '/home/user'"*)
      _arena_preview_platform=1
      return 0
      ;;
  esac
  case "\$BASH_COMMAND" in
    *"git commit"*|*"git push"*|*"gh pr checks"*)
      if [ -z "\${_arena_preview_reminded:-}" ]; then
        _arena_preview_reminded=1
        printf '%s\n' "Finished a task? Update your task-list with arena-preview task <id> --status finished." >&2
      fi
      ;;
  esac
  case "\$BASH_COMMAND" in *preview*|*profile*|*bashrc*|*arena-state*|gh*|sleep*|true*|:*|test*|"git status"*|"git diff"*|"git add"*|"git commit"*|*arena-workspace*|*"ss -ltn"*|*"netstat -ltn"*) return 0 ;; esac
  # A push is a checkpoint: an unread note can change what leaves the sandbox,
  # so it waits for an ack whatever the call count.
  case "\$BASH_COMMAND" in
    *"git push"*)
      "$VENV/bin/python" "$REPO_ROOT/$SKILL_REL/scripts/preview.py" gate --push 2>/dev/null
      case \$? in
        1) exit 130 ;;
      esac
      ;;
  esac
  # A command line that reads or answers the inbox must reach its read: a chain with a cd
  # before the read stays quiet for the count gate, while the push rule above still runs
  # for every push in the same shell. The quiet line holds only when every command on it
  # is an inbox call or an inert prefix, so a read beside work never exempts the work.
  _arena_preview_line="\$(tr '\\0' ' ' < /proc/\$\$/cmdline 2>/dev/null)"
  case "\$_arena_preview_line" in
    *"arena-preview read"*|*"arena-preview ack"*|*"arena-preview poll"*|*"arena-preview task"*|*"preview.py read"*|*"preview.py ack"*|*"preview.py poll"*|*"preview.py task"*)
      if "$VENV/bin/python" "$REPO_ROOT/$SKILL_REL/scripts/preview.py" inbox-line "\$_arena_preview_line" 2>/dev/null; then
        return 0
      fi
      ;;
  esac
  case "\${_arena_preview_gate_checked:-}" in 1) return 0 ;; esac
  _arena_preview_gate_checked=1
  "$VENV/bin/python" "$REPO_ROOT/$SKILL_REL/scripts/preview.py" gate 2>/dev/null
  case \$? in
    1) exit 130 ;;
  esac
}
EOF

# 7. Add this repository's skill scripts to PATH in new Bash shells.
strip_block "$PATH_MARKER"
  cat >> "$PROFILE" <<'EOF' || fail "cannot append to $PROFILE"

# arena-preview-path
# The root is pinned at install time: a source-time lookup reads the cwd of the
# sourcing shell, so a shell outside the checkout would miss the command.
_arena_preview_scripts="__ARENA_PREVIEW_ROOT__/.agents/skills/arena-preview-steering/scripts"
if [ -x "$_arena_preview_scripts/arena-preview" ]; then
  case ":$PATH:" in
    *":$_arena_preview_scripts:"*) ;;
    *) export PATH="$_arena_preview_scripts:$PATH" ;;
  esac
fi
unset _arena_preview_scripts
EOF
sed -i "s|__ARENA_PREVIEW_ROOT__|$REPO_ROOT|" "$PROFILE" || fail "cannot pin the PATH root in $PROFILE"

echo "arena-preview installer: ok; state: $REPO_ROOT/$STATE_REL, ignored through $GLOBAL_IGNORE; command: arena-preview in new Bash shells"

# 8. Install the DEBUG gate trap last in ~/.bash_profile.
GATE_TRAP_MARKER="# arena-preview-gate-trap"
strip_block "$GATE_TRAP_MARKER"
  cat >> "$PROFILE" <<'EOF' || fail "cannot append to $PROFILE"

# arena-preview-gate-trap
trap '_arena_preview_gate : # arena-preview-gate' DEBUG
EOF
