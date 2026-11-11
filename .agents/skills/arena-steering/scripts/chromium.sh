#!/bin/sh
# Install a reusable Sparticuz Chromium runtime and print its paths.
set -eu

version=153.0.0
root=${STEER_CHROMIUM_DIR:-${XDG_CACHE_HOME:-"$HOME/.cache"}/steer-chromium/$version}
mkdir -p "$root"
root=$(cd "$root" && pwd)
pkg="$root/node_modules/@sparticuz/chromium"
if [ ! -f "$pkg/build/index.js" ] || ! node -e \
  'process.exit(require(process.argv[1]).version === process.argv[2] ? 0 : 1)' \
  "$pkg/package.json" "$version" 2>/dev/null; then
  npm --prefix "$root" install --no-audit --no-fund "@sparticuz/chromium@$version" >&2
fi
mkdir -p "$root/runtime/$version"
paths=$(TMPDIR="$root/runtime/$version" CHROMIUM_PACKAGE="$pkg" node --input-type=module <<'NODE'
import { access, constants } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const pkg = process.env.CHROMIUM_PACKAGE;
const root = process.env.TMPDIR;
const chromium = await import(pathToFileURL(join(pkg, "build/index.js")));
const bin = join(pkg, "bin");
await chromium.inflate(join(bin, "al2023.tar.br"));
await chromium.inflate(join(bin, "fonts.tar.br"));
const lib = join(root, "al2023", "lib");
const fonts = join(root, "fonts");
chromium.setupLambdaEnvironment(lib);
const executable = await chromium.default.executablePath(bin);
await Promise.all([
  access(executable, constants.X_OK),
  access(lib, constants.R_OK),
  access(fonts, constants.R_OK),
]);
console.log(executable);
console.log(lib);
console.log(fonts);
NODE
)
bin=$(printf '%s\n' "$paths" | sed -n 1p)
lib=$(printf '%s\n' "$paths" | sed -n 2p)
fonts=$(printf '%s\n' "$paths" | sed -n 3p)

conf="$root/fonts.conf"
printf '%s\n' \
  '<?xml version="1.0"?>' \
  '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">' \
  '<fontconfig>' \
  "  <dir>$fonts/fonts</dir>" \
  "  <cachedir>$root/fonts-cache</cachedir>" \
  '</fontconfig>' >"$conf"

launcher="$root/chromium-launcher"
printf '%s\n' \
  '#!/bin/sh' \
  "LD_LIBRARY_PATH=$lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}" \
  "FONTCONFIG_FILE=$conf" \
  "FONTCONFIG_PATH=$root" \
  'export LD_LIBRARY_PATH FONTCONFIG_FILE FONTCONFIG_PATH' \
  "exec '$bin' \"\$@\"" >"$launcher"
chmod 755 "$launcher"

targets=$(python3 - <<'PY'
import sys
from pathlib import Path

try:
  from playwright.sync_api import sync_playwright
except ImportError:
  raise SystemExit(0)

with sync_playwright() as pw:
  browser = Path(pw.chromium.executable_path)
print(browser)
root = next((p for p in browser.parents if p.name.startswith("chromium-")), None)
if root is not None:
  shell = root.name.replace("chromium-", "chromium_headless_shell-")
  print(root.parent / shell / "chrome-headless-shell-linux64" / "chrome-headless-shell")
  print(root.parent / shell / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")
PY
)

bin=""
for target in $targets; do
  mkdir -p "$(dirname "$target")"
  ln -sfn "$launcher" "$target"
  if [ -z "$bin" ]; then
    bin=$target
  fi
done

echo "CHROMIUM_BIN=$bin"
echo "CHROMIUM_LIB=$lib"
echo "CHROMIUM_FONTS=$fonts"
