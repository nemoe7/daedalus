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

## Audit Coverage

Suppress a rule-ID finding on a line with `nonuu: <ID>`. Record an approved house-style deviation note for maintainers (without a rule ID) with `nuu-deviation: <reason>`. Agents MUST NEVER add `nuu-deviation:` automatically and ALWAYS require explicit user approval.

| Code | Pass | Condition |
| --- | --- | --- |
| `NUI001`–`NUI003` | Icons (`audit`) | Missing icon license (`NUI001`), non-ladder icon (`NUI002`), or mixed inline icon sizes (`NUI003`) |
| `NUL001`–`NUL006` | Source (`lint`) | Missing `alt` (`NUL001`), unnamed control (`NUL002`), `outline: 0` (`NUL003`), missing `design.md` (`NUL004`), hover shift (`NUL005`), raw timestamp (`NUL006`) |
| `NUL007` | Source (`lint`) | List CSS uses `list-style-position: inside` or omits the `2ch` hanging-indent reset |
| `NUL008` | Source (`lint`) | Multiple primary buttons or vague Yes/No/Okay labels |
| `NUB101`–`NUB103` | Browser (`inspect`, `flow`) | Horizontal overflow (`NUB101`), uncaught `pageerror` or `console.error` during load, resize, or flow (`NUB102`), or flow step timeout, missing selector, or failed assertion (`NUB103`) |
| `NUB104` | Browser (`--ready`) | Declared `--ready SELECTOR` did not match a visible element within the timeout |
| `NUM201`–`NUM203` | Measure (`--measure`) | Off-scale spacing except validated `2ch` list padding under `NUM211` (`NUM201`), off-ramp typography (`NUM202`), or unapproved border radius (`0/4/6/10/9999 px`) (`NUM203`) |
| `NUM205`–`NUM208` | Density (`density`, `--density`) | Main-layout (`<main>`, `[role="main"]`) ink ratio < 0.40 or other row ink ratio < 0.75 (`NUM205`), sibling gap > 320 px (`NUM206`), form input ending more than 24 px before its row edge with no control after it (`NUM207`), or non-symmetric icon button (`NUM208`) |
| `NUM209`–`NUM210` | Density (`-C`, `-O`) | Non-scrolling container clips child content (`NUM209`) or sibling boxes overlap (`NUM210`) |
| `NUM211` | Measure (`--measure`) | List markers sit outside in a `2ch` hanging-indent slot, and wrapped lines align with the first line |
| `NUM212` | Measure (`--measure`) | Heading (h1-h6) line-height outside 1.1-1.3x font-size or non-heading line-height outside 1.3-1.5x |
| `NUM213` | Measure (`--measure`) | Heading (h1-h6) letter-spacing outside -0.005em to -0.02em or non-zero letter-spacing on non-headings |
| `NUM214` | Measure (`--measure`) | Centered prose over 3 lines or justified text alignment |
| `NUM215` | Measure (`--measure`) | Multi-line prose or standalone prose container outside 50 to 75ch on desktop |
| `NUM216` | Measure (`--measure`) | Card or dialog mixes more than 3 distinct font sizes |
| `NUM217` | Measure (`--measure`) | Inter-group gap is smaller than 1.5x the adjacent intra-group gap |
| `NUM218` | Measure (`--measure`) | Ragged inline card insets, non-house font families, or uppercase text outside .eyebrow |
| `NUA301` | Rendered `a11y` | Visible interactive control without an accessible name |
| `NUA302` | Rendered `a11y` | Broken `aria-labelledby`, `aria-describedby`, `aria-controls`, or `aria-owns` ID target |
| `NUA303` | Rendered `a11y` | Interactive control nested inside another interactive control |
| `NUA304` | Rendered `a11y` | Duplicate `id` attribute across elements in the document |
| `NUA305` | Rendered `a11y` | Heading hierarchy skips one or more heading levels |
| `NUA306` | Rendered `a11y` | Rendered text contrast below `4.5:1` (`3:1` on large text) |
| `NUA307` | Rendered `a11y` | Interactive control shows a visible focus outline on pointer focus before keyboard navigation |
| `NUA308` | Rendered `a11y` | Focus halo or visible tooltip extends outside an overflow-clipping ancestor |
| `NUA309` | Rendered `a11y` | Interactive control changes dimensions on focus, `aria-busy`, disabled state, or a measured flow action |
| `NUA310` | Rendered `a11y` | Visible raw ISO or unadjusted UTC or offset timestamp in rendered text |

![Measure Rules NUM201-NUM204 Dark-Mode Example](examples/rule-num201-204.png)

![Density Rules NUM205-NUM208 Dark-Mode Example](examples/rule-num205-208.png)

## Recipes (Mandatory in Audits When Applicable)

- [references/recipes/offline-fonts.md](recipes/offline-fonts.md): bundled and offline fonts without reload jitter.
- [references/recipes/page-memory.md](recipes/page-memory.md): optimistic writes, content-versioned view cache, and URL/`localStorage` state persistence.
- [references/recipes/modal-manager.md](recipes/modal-manager.md): shared `<dialog>` modal manager with top-right `.close-button` (`-8 px` margin, focus/hover `--text` shift) and delete confirmation.
- [references/recipes/range-sliders.md](recipes/range-sliders.md): `<input type="range">` with `step` ticks, min/max endpoint labels, and live `<output>` bubble.
- [references/recipes/relative-timestamps.md](recipes/relative-timestamps.md): relative timestamps (`just now`, `Xmin ago`, `yesterday`), full or abbreviated by width.
- [references/recipes/loading-bar.md](recipes/loading-bar.md): 200 ms delayed 2 px edge bar and fixed-slot inline control loading.
- [references/recipes/live-dot.md](recipes/live-dot.md): `8x8 px` pulsing `.dot` in a fixed `16x16 px` slot, with row text naming the state.
- [references/recipes/scroll-edge-fade.md](recipes/scroll-edge-fade.md): scroll-driven CSS edge-fade mask (`@supports (animation-timeline: scroll(self))`).
- [references/recipes/floating-overlay-scroll-fade.md](recipes/floating-overlay-scroll-fade.md): full-bleed scroll container fade behind floating header and composer overlays.
- [references/recipes/scrollbars.md](recipes/scrollbars.md): thin 10 px rounded-thumb scrollbar (`#302e2c`) without arrow buttons or hover highlights unless keyboard navigation is active.
- [references/recipes/overflow-and-clipping.md](recipes/overflow-and-clipping.md): focus-ring, shadow, and tooltip anti-clipping rules.
