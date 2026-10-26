# `nuu` CLI Reference

`scripts/nuu` (`scripts/nuu.py`) is the single command-line entry point for UI/UX audits, system proposals, design records, measurements, and screenshot annotations.

## Subcommands and Short Flags

| Subcommand | Purpose | Arguments and Shorthands |
| --- | --- | --- |
| `search` | Search vendored design catalogs | `<query>`, `-p, --project <path>`, `-o, --output <path>`, `-d, --dataset <name>`, `-s, --stack <name>`, `-l, --limit <n>` |
| `assets` | Verify or fetch licensed assets | `-p, --project <path>`, `-o, --output <path>`, `-m, --manifest <path>`, `-f, --fetch <id>`, `-d, --destination <rel>`, `-A, --authorize-fetch` |
| `lint`, `extract` | Run static source checks or extract candidate tokens | `-p, --project <path>`, `-o, --output <path>` |
| `doctor` | Check browser engine, manifest, and `nuu.toml` | `-p, --project <path>`, `-o, --output <path>`, `-m, --manifest <path>`, `-c, --config <path>` |
| `check` | Check computed-style measure JSON against the sheet | `<report>`, `-s, --sheet <path>`, `-k, --scale <px>`, `-d, --density` |
| `density` | Check row/card ink ratio (`NUM205`), sibling gaps (`NUM206`), form inputs that leave row space unused (`NUM207`), and symmetric icon buttons (`NUM208`) | `<report>` |
| `ruler` | Measure pixel distance between two `x,y` guides | `-f, --from <x,y>`, `-t, --to <x,y>`, `-s, --sheet <path>`, `-k, --scale <px>` |
| `annotate` | Render boxes, arrows, labels, callouts, and crops (`--crop`) in dark mode by default | `-i, --input <png>`, `-o, --output <png>`, `-p, --project <path>`, `-b, --box <spec>`, `-l, --label <spec>`, `-a, --arrow <spec>`, `-c, --callout <spec>`, `-C, --crop <x,y,w,h>`, `-s, --spec <json>`, `-S, --scale <1-6>` |
| `board` | Render multi-panel or HTML design comparison boards in dark mode by default | `-o, --output <png>`, `--project <path>`, `-T, --type <panels\|design>`, `-O, --only <name>`, `-t, --title <text>`, `-p, --panel <spec>`, `-c, --callout <spec>`, `-s, --spec <json>`, `-S, --scale <1-6>`, `-C, --columns <n>`, `-W, --wireframe` |
| `walk` | Run deterministic dark-mode browser walk steps or capture named view PNGs for `nuu board` | `[target]`, `-s, --script <py>`, `-V, --views`, `-o, --out <dir>` |
| `inspect`, `flow`, `a11y`, `live`, `audit`, `collect` | Run source, measure, flow, deviation (`--deviations`), and rendered a11y checks | `-p, --project <path>`, `-o, --output <path>`, `-u, --url <url>`, `-d, --artifacts <dir>`, `-w, --widths <px...>`, `-s, --selector <css>`, `-r, --ready <selector>`, `-a, --a11y`, `-e, --engine <name>`, `-x, --executable <path>`, `-t, --timeout <s>`, `-f, --flow <json>`, `-P, --profile <project\|house>`, `-E, --edit <path>`, `-A, --authorize-edit`, `-O, --overlaps`, `-W, --wireframe`, `-D, --deviations` |

## Audit Coverage (`NUL001`–`NUA309`)

Suppress a rule-ID finding on a line with `nonuu: <ID>`. Record an approved house-style deviation note for maintainers (without a rule ID) with `nuu-deviation: <reason>`. Agents MUST NEVER add `nuu-deviation:` automatically and ALWAYS require explicit user approval.

| Code | Pass | Condition |
| --- | --- | --- |
| `NUL001`–`NUL005` | Source (`lint`) | Missing image `alt` (`NUL001`), missing control naming or raw colour literal (`NUL002`), `outline: none` / `0` (`NUL003`), missing `docs/design.md` (`NUL004`), or hover/focus/active rule changing layout dimensions (`NUL005`) |
| `NUB101`–`NUB103` | Browser (`inspect`, `flow`) | Horizontal overflow (`NUB101`), uncaught `pageerror` or `console.error` during load, resize, or flow (`NUB102`), or flow step timeout, missing selector, or failed assertion (`NUB103`) |
| `NUB104` | Browser (`--ready`) | Declared `--ready SELECTOR` did not match a visible element within the timeout |
| `NUM201`–`NUM204` | Measure (`--measure`) | Off-scale spacing (`4/8/12/16/24/32/48 px` or `8 px` scale) (`NUM201`), off-ramp typography (`11/12/14/16/18/22/28 px`, weights `400-700`) (`NUM202`), unapproved border radius (`0/4/6/10/9999 px`) (`NUM203`), or text contrast below 4.5:1 (`3:1` large text) (`NUM204`) |
| `NUM205`–`NUM208` | Density (`density`, `--density`) | Main-layout (`<main>`, `[role="main"]`) ink ratio < 0.40 or other row ink ratio < 0.75 (`NUM205`), sibling gap > 320 px (`NUM206`), form input ending more than 24 px before its row edge with no control after it (`NUM207`), or non-symmetric icon button (`NUM208`) |
| `NUM209`–`NUM210` | Density (`-C`, `-O`) | Non-scrolling container clipping child content (`NUM209`) or unintended sibling box overlap (`NUM210`) |
| `NUA301` | Rendered `a11y` | Landmarks (`<main>` or `[role="main"]`) and skip-link target missing or broken |
| `NUA302` | Rendered `a11y` | Visible interactive control without an accessible name or with `aria-hidden="true"` while focusable |
| `NUA303` | Rendered `a11y` | Visible interactive control with bounding box smaller than `24x24` CSS px (WCAG 2.5.8 AA floor, e.g. `16x16 px` bare icon button instead of `32x32 px`) |
| `NUA304` | Rendered `a11y` | Interactive control whose `:focus-visible` ring clips or matches its unfocused outline/box-shadow (instead of a 2 px solid `var(--focus)` ring with `>= 3:1` contrast) |
| `NUA305` | Rendered `a11y` | Invalid `role`, missing required ARIA state (`aria-checked`, `aria-expanded`, `aria-selected`), or broken `aria-labelledby` / `aria-describedby` / `aria-controls` ID target |
| `NUA306` | Rendered `a11y` | Stylesheet animation or transition without a `@media (prefers-reduced-motion: reduce)` rule |
| `NUA307` | Rendered `a11y` | Interactive control that renders a visible focus outline on pointer focus before keyboard navigation |
| `NUA308` | Rendered `a11y` | Interactive control focus ring, shadow, or tooltip clipped by an overflow or mask ancestor |
| `NUA309` | Rendered `a11y` | Interactive control that changes width or height across hover, focus, active, or loading states |

![Measure Rules NUM201-NUM204 Dark-Mode Example](examples/rule-num201-204.png)

![Density Rules NUM205-NUM208 Dark-Mode Example](examples/rule-num205-208.png)

## Recipes (Mandatory in Audits When Applicable)

- [references/recipes/offline-fonts.md](recipes/offline-fonts.md): bundled and offline fonts without reload jitter.
- [references/recipes/page-memory.md](recipes/page-memory.md): optimistic writes, content-versioned view cache, and URL/`localStorage` state persistence.
- [references/recipes/modal-manager.md](recipes/modal-manager.md): shared `<dialog>` modal manager with top-right `.close-button` (`-8 px` margin, focus/hover `--text` shift) and delete confirmation.
- [references/recipes/range-sliders.md](recipes/range-sliders.md): `<input type="range">` with `step` ticks, min/max endpoint labels, and live `<output>` bubble.
- [references/recipes/relative-timestamps.md](recipes/relative-timestamps.md): relative timestamps (`just now`, `Xmin ago`, `yesterday`), full or abbreviated by width.
- [references/recipes/loading-bar.md](recipes/loading-bar.md): 200 ms delayed 2 px edge bar and fixed-slot inline control loading.
- [references/recipes/live-dot.md](recipes/live-dot.md): `8x8 px` pulsing `.dot` (`NUA309`, `NUL005`) inside a fixed `16x16 px` slot.
- [references/recipes/scroll-edge-fade.md](recipes/scroll-edge-fade.md): scroll-driven CSS edge-fade mask (`@supports (animation-timeline: scroll(self))`).
- [references/recipes/floating-overlay-scroll-fade.md](recipes/floating-overlay-scroll-fade.md): full-bleed scroll container fade behind floating header and composer overlays.
- [references/recipes/scrollbars.md](recipes/scrollbars.md): thin 10 px rounded-thumb scrollbar (`#302e2c`) without arrow buttons or hover highlights unless keyboard navigation is active.
- [references/recipes/overflow-and-clipping.md](recipes/overflow-and-clipping.md): focus-ring, shadow, and tooltip anti-clipping rules.
