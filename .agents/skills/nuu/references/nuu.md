# `nuu` CLI Reference

`scripts/nuu` (`scripts/nuu.py`) is the single command-line entry point for UI/UX audits, system proposals, design records, measurements, and screenshot annotations.

## Subcommands and Short Flags

| Subcommand | Purpose | Arguments and Shorthands |
| --- | --- | --- |
| `search` | Search vendored design catalogs or fetch an authorized external asset | `<query>`, `-p, --project <path>`, `-o, --output <path>`, `-d, --dataset <name>`, `-s, --stack <name>`, `-l, --limit <n>`, `-f, --fetch <url>`, `-A, --authorize-fetch` |
| `lint` | Run static source, icon, ruler, and manifest checks and extract candidate tokens | `-p, --project <path>`, `-o, --output <path>`, `-m, --manifest <path>`, `-f, --from <x,y>`, `-t, --to <x,y>`, `-s, --sheet <path>`, `-k, --scale <px>` |
| `doctor` | Check browser engine, manifest, and `nuu.toml` | `-p, --project <path>`, `-o, --output <path>`, `-m, --manifest <path>`, `-c, --config <path>` |
| `annotate` | Render boxes, arrows, labels, callouts, and crops (`--crop`) in dark mode by default | `-i, --input <png>`, `-o, --output <png>`, `-p, --project <path>`, `-b, --box <spec>`, `-l, --label <spec>`, `-a, --arrow <spec>`, `-c, --callout <spec>`, `-C, --crop <x,y,w,h>`, `-s, --spec <json>`, `-S, --scale <1-6>` |
| `board` | Render multi-panel or HTML design comparison boards in dark mode by default | `-o, --output <png>`, `--project <path>`, `-T, --type <panels\|design>`, `-O, --only <name>`, `-t, --title <text>`, `-p, --panel <spec>`, `-c, --callout <spec>`, `-s, --spec <json>`, `-S, --scale <1-6>`, `-C, --columns <n>`, `-W, --wireframe` |
| `walk` | Run deterministic dark-mode browser walk steps or capture named view PNGs for `nuu board` | `[target]`, `-s, --script <py>`, `-V, --views`, `-o, --out <dir>` |
| `audit` | Run source, measure, density, ruler, flow, deviation (`--deviations`), and rendered a11y checks | `-p, --project <path>`, `-o, --output <path>`, `-u, --url <url>`, `-M, --measured <json>`, `-d, --artifacts <dir>`, `-w, --widths <px...>`, `-s, --selector <css>`, `-r, --ready <selector>`, `-a, --a11y`, `-e, --engine <name>`, `-x, --executable <path>`, `-t, --timeout <s>`, `-f, --flow <json>`, `-P, --profile <project\|house>`, `-E, --edit <path>`, `-A, --authorize-edit`, `-O, --overlaps`, `-W, --wireframe`, `-D, --deviations` |

## Measurement severity

`TYP101` and `TYP201` findings are nonblocking review by default. Set either rule to `"error"` under `[checks.severity]` in the project `nuu.toml` to make it blocking. Only `"review"` and `"error"` are accepted. All other existing checks retain their blocking behavior.

```toml
[checks.severity]
TYP101 = "error"
TYP201 = "review"
```

`nuu check` reads configuration from the current project directory. `lint`, `audit`, and `collect` use their project directory.

## Audit Coverage

Suppress a rule-ID finding on a line with `nonuu: <ID>` (legacy `NU*` aliases remain supported). Record an approved house-style deviation note for maintainers (without a rule ID) with `nuu-deviation: <reason>`. Agents MUST NEVER add `nuu-deviation:` automatically and ALWAYS require explicit user approval.

| Code | Pass | Condition |
| --- | --- | --- |
| `ICN001`–`ICN002` | Icons (`lint`, `audit`) | Missing icon license (`ICN001`) or non-ladder icon (`ICN002`) |
| `ICN101` | Icons (`lint`, `audit`) | Advisory: mixed inline icon sizes in one file; review whether sizes differ on the same surface |
| `DOC001` | Source (`lint`) | Missing `docs/design.md` or configured `[design].path` |
| `TYP001` | Measure (`--measure`) | Off-ramp typography (`font-size` or `font-weight`) |
| `TYP002`–`TYP003` | Measure (`--measure`) | Non-house or italic body font (`TYP002`), or non-eyebrow `text-transform: uppercase` (`TYP003`) |
| `TYP101` | Measure (`--measure`) | Heading (`h1`–`h6`) `line-height` outside `1.1–1.3x` or non-heading `line-height` outside `1.3–1.5x` |
| `TYP102` | Measure (`--measure`) | Heading (`h1`–`h6`) `letter-spacing` outside `-0.005em` to `-0.03em` or non-zero `letter-spacing` on non-headings |
| `TYP103` | Measure (`--measure`) | Centered prose over 3 lines, justified text, or mismatched heading/body alignment |
| `TYP201` | Measure (`--measure`) | Review: prose wider than `75ch`, or standalone prose narrower than `50ch`, on desktop |
| `TYP202` | Source (`lint`) | List CSS uses `list-style-position: inside` or omits the `2ch` hanging-indent reset |
| `TYP203` | Measure (`--measure`) | List markers sit outside in a `2ch` hanging-indent slot, or wrapped lines fail to align with the first line |
| `SPC001`–`SPC002` | Measure (`--measure`) | Off-scale spacing except validated `2ch` list padding under `TYP203` (`SPC001`), or unapproved border radius (`0/4/6/10/9999 px`) (`SPC002`) |
| `SPC101` | Measure (`--measure`) | Inter-group gap is smaller than `1.5x` the adjacent intra-group gap or `0px` touching |
| `SPC102` | Measure (`--measure`) | Ragged stacked card child insets or asymmetric card inline padding |
| `SPC201`–`SPC203` | Density (`audit`) | Low row ink ratio (`SPC201`), large sibling gap (`SPC202`), or excess slack (`SPC203`) |
| `SPC204`–`SPC205` | Density (`-C`, `-O`) | Non-scrolling container clips child content (`SPC204`) or sibling boxes overlap (`SPC205`) |
| `SPC206` | Browser (`audit`) | Horizontal viewport overflow |
| `HRC001` | Source & Measure | Multiple primary buttons or vague `Yes`/`No`/`Okay` labels |
| `HRC101` | Measure (`--measure`) | Card or dialog mixes more than 2 font sizes or inverts heading/body hierarchy |
| `HRC102` | Density & Browser | Buttons in the same group have unequal width or height, or asymmetric icon-only button |
| `HRC201`–`HRC202` | Source & `a11y` | Raw ISO or unadjusted UTC/offset timestamp in source (`HRC201`) or rendered text (`HRC202`) |
| `ACC001`–`ACC002` | Source (`lint`) | Missing `alt` attribute (`ACC001`) or unnamed interactive control in source (`ACC002`) |
| `ACC003` | Rendered `a11y` | Visible interactive control without an accessible name |
| `ACC004` | Rendered `a11y` | Broken `aria-labelledby`, `aria-describedby`, `aria-controls`, or `aria-owns` ID target |
| `ACC005` | Rendered `a11y` | Interactive control nested inside another interactive control |
| `ACC006` | Rendered `a11y` | Duplicate `id` attribute across elements in the document |
| `ACC007` | Rendered `a11y` | Heading hierarchy skips one or more heading levels |
| `ACC101` | Rendered `a11y` | Rendered text contrast below `4.5:1` (`3:1` on large text) |
| `ACC201` | Source (`lint`) | CSS suppresses focus outline (`outline: 0` or `outline: none`) |
| `ACC202` | Rendered `a11y` | Interactive control shows a visible focus outline on pointer focus before keyboard navigation |
| `ACC203` | Rendered `a11y` | Focus halo or visible tooltip extends outside an overflow-clipping ancestor |
| `ACC204` | Source (`lint`) | CSS `:hover`, `:focus`, or `:active` rule shifts layout dimensions |
| `ACC205` | Rendered `a11y` | Interactive control changes dimensions on focus, `aria-busy`, disabled state, or a measured flow action |
| `RUN001` | Browser (`audit`) | Uncaught `pageerror` or `console.error` during capture |
| `RUN101`–`RUN102` | Browser (`audit`) | Flow step failure (`RUN101`) or `--ready SELECTOR` timeout (`RUN102`) |

![Measure Rules SPC001-SPC002 and TYP001 Dark-Mode Example](examples/rule-num201-204.png)

![Density Rules SPC201-SPC203 and HRC102 Dark-Mode Example](examples/rule-num205-208.png)

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
- [references/recipes/scrollbars.md](recipes/scrollbars.md): use a 6 px rounded thumb (`var(--scrollbar-thumb)`) in a 10 px track. Omit arrows and hover highlights unless keyboard navigation is active.
- [references/recipes/overflow-and-clipping.md](recipes/overflow-and-clipping.md): focus-ring, shadow, and tooltip anti-clipping rules.
