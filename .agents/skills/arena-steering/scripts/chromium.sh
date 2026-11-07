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
TMPDIR="$root/runtime/$version" CHROMIUM_PACKAGE="$pkg" node --input-type=module <<'NODE'
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
console.log(`CHROMIUM_BIN=${executable}`);
console.log(`CHROMIUM_LIB=${lib}`);
console.log(`CHROMIUM_FONTS=${fonts}`);
NODE
