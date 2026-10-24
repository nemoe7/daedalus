# `nuu` CLI Reference

`scripts/nuu` (`scripts/nuu.py`) is the single command-line entry point for UI/UX audits, system proposals, design records, measurements, and screenshot annotations.

## Subcommands and Short Flags

| Subcommand | Purpose | Arguments and Shorthands |
| --- | --- | --- |
| `search` | Search vendored design catalogs | `<query>`, `-d, --dataset <name>`, `-s, --stack <name>`, `-n, --limit <n>`, `-j, --json` |
| `system` | Generate a five-part design system proposal | `<query>`, `-p, --project <name>`, `-s, --stack <name>`, `-w, --write [path]`, `-f, --force`, `-j, --json` |
| `record` | Initialize or check `docs/design.md` | `<init\|check>`, `-r, --root <path>`, `-p, --path <rel>`, `-t, --title <text>`, `-s, --status <state>`, `-f, --force`, `-j, --json` |
| `asset` | Fetch, verify, or record licensed assets | `<fetch\|verify\|record>`, `-r, --root <path>`, `-u, --url <https-url>`, `-o, --output <rel>`, `-s, --sha256 <hex>`, `-l, --license <spdx>`, `-a, --attribution <text>`, `-p, --purpose <text>`, `-d, --design-doc <rel>`, `-f, --force`, `-j, --json` |
| `extract` | Extract candidate tokens from source or measure JSON | `-r, --root <path>`, `-m, --measure <path>`, `-o, --output <rel>`, `-f, --force`, `-j, --json` |
| `check` | Check computed-style measure JSON against the sheet | `<report>`, `-s, --sheet <path>`, `-g, --scale <px>`, `-d, --density`, `-j, --json` |
| `density` | Check row/card ink ratio (`NUM205`), sibling gaps (`NUM206`), control width caps (`NUM207`), and symmetric icon buttons (`NUM208`) | `<report>` |
| `ruler` | Measure pixel distance between two `x,y` guides | `-f, --from <x,y>`, `-t, --to <x,y>`, `-g, --scale <px>`, `-j, --json` |
| `annotate`, `board` | Render boxes, arrows, labels, callouts, and multi-panel comparison boards with the project's default text font | `-i, --input <png>`, `-o, --output <png>`, `-b, --box <spec>`, `-l, --label <spec>`, `-a, --arrow <spec>`, `-c, --callout <spec>`, `-t, --title <text>`, `-p, --panel <spec>`, `-s, --spec <json>`, `-S, --scale <1-6>` |
| `audit` | Run source, measure, flow, and rendered a11y checks | `-r, --root <path>`, `-u, --url <url>`, `-s, --sheet <path>`, `-m, --measure <path>`, `-f, --flows <json>`, `-c, --change <spec>`, `-A, --allow-live-edits`, `-R, --ready <selector>`, `-o, --out <dir>`, `-j, --json` |

## Audit Coverage (`NUL001`–`NUA306`)

Suppress a source-lint finding on a line with `nonuu: <ID>` (for example, `<!-- nonuu: NUL001 -->` or `/* nonuu: NUL003 */`).

| Code | Pass | Condition |
| --- | --- | --- |
| `NUL001`–`NUL004` | Source (`lint`) | Missing image `alt` (`NUL001`), missing control naming evidence (`NUL002`), `outline: none` / `0` (`NUL003`), or missing `docs/design.md` (`NUL004`) |
| `NUB101`–`NUB103` | Browser (`inspect`, `flow`) | Horizontal overflow (`NUB101`), runtime console/page error (`NUB102`), or failed interaction flow (`NUB103`) |
| `NUB104` | Browser (`--ready`) | Declared `--ready SELECTOR` did not match a visible element within the timeout |
| `NUM201`–`NUM204` | Measure (`--measure`) | Off-scale spacing, off-ramp typography, unapproved border radius, or text contrast below 4.5:1 (`3:1` large text) |
| `NUM205`–`NUM208` | Density (`density`, `--density`) | Row/card ink ratio below `0.75` at `>= 900` px excluding header and toolbar bars (`NUM205`), sibling horizontal gap `> 320` px and `> 40%` width (`NUM206`), form control width `> 480` px (`NUM207`), or non-symmetric icon-only button (`NUM208`) |
| `NUA301` | Rendered `a11y` | Landmarks (`<main>` or `[role="main"]`) and skip-link target missing or broken |
| `NUA302` | Rendered `a11y` | Visible interactive control without an accessible name or with `aria-hidden="true"` while focusable |
| `NUA303` | Rendered `a11y` | Visible interactive control with bounding box smaller than `24x24` CSS px |
| `NUA304` | Rendered `a11y` | Interactive control whose `:focus-visible` ring is clipped or identical to its unfocused outline/box-shadow |
| `NUA305` | Rendered `a11y` | Invalid `role`, missing required ARIA state (`aria-checked`, `aria-expanded`, `aria-selected`), or broken `aria-labelledby` / `aria-describedby` / `aria-controls` ID target |
| `NUA306` | Rendered `a11y` | Stylesheet animation or transition without a `@media (prefers-reduced-motion: reduce)` rule |

## Optional Recipes

- [references/recipes/offline-fonts.md](recipes/offline-fonts.md): bundled and offline fonts without reload jitter.
- [references/recipes/scroll-edge-fade.md](recipes/scroll-edge-fade.md): scroll-driven CSS edge-fade mask (`@supports (animation-timeline: scroll(self))`).


