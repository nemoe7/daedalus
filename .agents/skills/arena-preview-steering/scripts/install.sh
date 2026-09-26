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

fail(){ echo "arena-preview installer: $*" >&2; exit 1; }

# 1. Verify the skill in this repository checkout.
[ -f "$SKILL_REL/scripts/preview.py" ] || fail "missing $SKILL_REL/scripts/preview.py; run this installer from the repository root"

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

echo "arena-preview installer: ok; state: $REPO_ROOT/$STATE_REL, ignored through $GLOBAL_IGNORE"
