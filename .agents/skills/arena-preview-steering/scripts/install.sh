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

# 5. Hook script: save $?, print the unacked-count reminder on stderr, restore the exit code.
cat > "$HOOK" <<EOF || fail "cannot write $HOOK"
#!/bin/bash
# arena-preview-hook: poll the steering inbox after every Arena bash call.
rc=\$?
"$VENV/bin/python" "$REPO_ROOT/$SKILL_REL/scripts/preview.py" --state-dir "$REPO_ROOT/$STATE_REL" --reminder >&2
exit "\$rc"
EOF
chmod 755 "$HOOK"

# 6. Idempotent EXIT trap in ~/.bash_profile.
touch "$PROFILE"
if ! grep -qF "$MARKER" "$PROFILE"; then
  cat >> "$PROFILE" <<'EOF' || fail "cannot append to $PROFILE"

# arena-preview-hook
case "$(trap -p EXIT)" in
  *arena-preview-hook*) ;;
  *) trap 'rc=$?; "$HOME/.arena-preview-hook.sh"; exit "$rc"' EXIT ;;
esac
EOF
fi

# 6b. Idempotent DEBUG gate function in ~/.bash_profile: block bash past the call threshold with a pending inbox.
GATE_MARKER="# arena-preview-gate"
if ! grep -qF "$GATE_MARKER" "$PROFILE"; then
  cat >> "$PROFILE" <<EOF || fail "cannot append to $PROFILE"

# arena-preview-gate
_arena_preview_gate() {
  case "\$BASH_COMMAND" in *preview*|*profile*|*bashrc*|*arena-state*|gh*|sleep*|true*|:*|test*|"git status"*|"git diff"*|"git add"*|"git commit"*) return 0 ;; esac
  case "\${_arena_preview_gate_checked:-}" in 1) return 0 ;; esac
  _arena_preview_gate_checked=1
  "$VENV/bin/python" "$REPO_ROOT/$SKILL_REL/scripts/preview.py" --state-dir "$REPO_ROOT/$STATE_REL" gate 2>/dev/null
  case \$? in
    1) exit 130 ;;
  esac
}
EOF
fi

# 7. Add this repository's skill scripts to PATH in new Bash shells.
if ! grep -qF "$PATH_MARKER" "$PROFILE"; then
  cat >> "$PROFILE" <<'EOF' || fail "cannot append to $PROFILE"

# arena-preview-path
_arena_preview_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$_arena_preview_root" ]; then
  _arena_preview_scripts="$_arena_preview_root/.agents/skills/arena-preview-steering/scripts"
  if [ -x "$_arena_preview_scripts/arena-preview" ]; then
    case ":$PATH:" in
      *":$_arena_preview_scripts:"*) ;;
      *) export PATH="$_arena_preview_scripts:$PATH" ;;
    esac
  fi
fi
unset _arena_preview_root _arena_preview_scripts
EOF
fi

echo "arena-preview installer: ok; state: $REPO_ROOT/$STATE_REL, ignored through $GLOBAL_IGNORE; command: arena-preview in new Bash shells"

# 8. Install the DEBUG gate trap last in ~/.bash_profile.
GATE_TRAP_MARKER="# arena-preview-gate-trap"
if ! grep -qF "$GATE_TRAP_MARKER" "$PROFILE"; then
  cat >> "$PROFILE" <<'EOF' || fail "cannot append to $PROFILE"

# arena-preview-gate-trap
trap '_arena_preview_gate : # arena-preview-gate' DEBUG
EOF
fi
