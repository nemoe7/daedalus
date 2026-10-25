# Design

This document is the design record for the Daedalus operator dashboard in `daedalus/dashboard/ui/`. It records the layout rules, color tokens, typography scale, components, states, breakpoints, and revisions of the web console.

## Visual Reference Index

- [Dashboard Visual Reference](design-visuals/dashboard.md) — Default and changed states, interactive modal and mobile states, component map, and responsive layout map
- [Component Visual Catalog](design-visuals/components.md) — Composite reference board and state matrix for shared dashboard controls
- [Foundation Style Reference](design-visuals/styles.md) — Dark and light role palettes, typography scale, spacing tokens, and surface elevation levels

## 1. Overview

Daedalus ships a self-hosted web console for an OpenAI-compatible AI router. The console serves 1 operator persona: an engineer who monitors live model traffic, provider quotas, fallback chains, and router settings.

The interface follows 3 core principles:

1. **High signal density**: Every panel shows live operational counters, quota bars, or configuration keys without decorative filler.
2. **Dual-polarity readability**: The interface supports both a carbon-slate dark theme (`:root[data-theme="dark"]`) and an alabaster-white light theme (`:root`).
3. **Zero-Step inspection**: The header bar stays pinned across all 6 views. Every table row or setting row opens its detail or edit dialog in 1 click or keypress.

![Dashboard overview composite board](images/dashboard-overview.png)

## 2. Design Paradigm

| Paradigm | Weight | Role |
| --- | --- | --- |
| P4 Atmospheric Minimalism and High-Craft UI | 65% | Whisper-quiet surface hierarchy, crisp `1px solid var(--line)` borders, and high-contrast text tokens |
| P2 Kinetic Typography and Neo-Brutalist Swiss Grids | 35% | Tabular numerals, monospace model identifiers, exposed hairline row dividers, and structured data tables |

The primary P4 paradigm keeps long monitoring sessions calm in both dark and light rooms. The secondary P2 paradigm gives request logs, latency counters, and provider quota bars strict column alignment and fast scannability.

## 3. Visual Direction

The visual direction matches the operational tooling domain:

- **Surface treatment**: Panels (`--panel`) use `1px solid var(--line)`, `10px` `--radius`, and `16px` card padding (`8px` vertical in `Settings`). Page cards use no blur or drop shadows.
- **Border discipline**: Every `.panel`, `.stat`, and `.card` uses a single `1px solid var(--line)` border. Nested elements inside a panel use horizontal hairline dividers (`border-top: 1px solid var(--line)`) instead of nested boxes.
- **Density calibration**: Desktop views (`>= 1024px`) cap each row at 4 stat cards (`repeat(4, 1fr)`) or 2 balanced workspace columns (`1.05fr 1fr` or `1fr 260px`).

![Dashboard detail composite board](images/dashboard-detail.png)

## 4. Color and Atmospheric Polarity

`daedalus/dashboard/ui/style.css` defines a dual-adaptive palette. `:root` sets the light theme tokens, and `:root[data-theme="dark"]` sets the dark theme tokens. The active theme follows `personalization.theme` (`system`, `light`, or `dark`).

### Surface Tokens

| Token | Light (`:root`) | Dark (`:root[data-theme="dark"]`) | Role |
| --- | --- | --- | --- |
| `--bg` | `#f3f3f5` | `#1c1c1c` | Root page canvas background |
| `--panel` | `#ffffff` | `#262626` | Primary card, table, and modal surface |
| `--field` | `#f7f7f9` | `#1f1f1f` | Input, select, search, and code block fill |
| `--line` | `#e5e5e7` | `#353535` | Hairline border and row divider token |
| `--control-line` | `#b3b3b3` | `#565656` | Interactive control border token |
| `--scrollbar-thumb` | `#c8c8d0` | `#302e2c` | `10px` rounded scrollbar thumb token |

### Text and Accent Tokens

| Token | Light (`:root`) | Dark (`:root[data-theme="dark"]`) | Contrast on `--panel` | Role |
| --- | --- | --- | --- | --- |
| `--text` | `#212121` | `#e6e6e6` | 16.1:1 (light) / 12.6:1 (dark) | Primary body, heading, and table value ink |
| `--muted` | `#616161` | `#adadad` | 6.1:1 (light) / 6.7:1 (dark) | Secondary label, timestamp, and hint ink |
| `--accent` | `#304ffe` | `#4b63e8` | 5.6:1 (light) / 4.6:1 (dark) | Primary button fill, active tab underline, and focus ring |
| `--accent-text` | `#304ffe` | `#8c9eff` | 5.6:1 (light) / 6.9:1 (dark) | Inline link and active tab text ink |
| `--tile-ink` | `#ffffff` | `#1c1c1c` | 14.3:1 (dark tile) | Ink inside category icon tiles on Settings |

### Semantic and Status Tokens

| Token | Light (`:root`) | Dark (`:root[data-theme="dark"]`) | Role |
| --- | --- | --- | --- |
| `--ok` | `#283593` | `#90caf9` | Healthy dot, HTTP 200 status, and primary quota fill |
| `--warn` | `#a3386c` | `#e1bee7` | Warning badge, HTTP 429 rate-limit status, and fallback code |
| `--bad` | `#781f4c` | `#d4608f` | Fault badge, HTTP 5xx status, and exhausted quota fill |
| `--bar` | `#e3e8fd` | `#212c70` | Quota progress bar track background |
| `--orange` | `#b45309` | `#ffa040` | Elevated usage and warning indicator |
| `--red` | `#d32f2f` | `#ff6663` | Destructive action border and critical alert ink |
| `--tier` | `#212c70` | `#90caf9` | Tier badge ink (`TIER-A` through `TIER-D`) |
| `--tag` | `#5c388c` | `#e1bee7` | Capability and hook tag ink |

## 5. Typography and Scannable Hierarchy

The console bundles variable `.woff2` fonts in `daedalus/dashboard/ui/fonts/` and preloads them with `crossorigin` and `font-display: block`:

- **UI sans stack (`--font`)**: `"Inter", "Inter Fallback", system-ui, -apple-system, "Segoe UI", sans-serif`
- **Monospace stack (`--font-mono`, `--mono`)**: `"Monaspace Neon", "Monaspace Neon Fallback", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace`

| Element | Selector | Size | Weight | Line Height | Numeric Discipline |
| --- | --- | --- | --- | --- | --- |
| Brand mark | `.brand` | `18px` | `650` | `1.1` | Standard |
| Hero stat value | `.kpi b` | `22px` | `650` | `1.2` | `font-variant-numeric: tabular-nums` |
| Modal / section title | `.modal h3`, `.card h3` | `15px` | `600` | `1.35` | Standard |
| Panel title | `h2` | `14px` | `600` | `1.4` | Standard |
| Body and table cells | `body`, `td` | `14px` | `400` | `1.5` | `tabular-nums` on `.num` and `.mono` |
| Table header | `th` | `12px` | `500` | `1.4` | Standard |
| Micro label / badge | `.hint`, `.chip`, `.kpi small` | `12px` | `500` | `1.4` | `tabular-nums` on counters |

### Time Formatting (`personalization.time_format`)

| Rule | Behavior |
| --- | --- |
| Default 24-hour clock | `stamp()` uses 24-hour time (`YYYY-MM-DD HH:MM:SS`) by default. Selecting `12h` in `Settings` switches stamps to `YYYY-MM-DD hh:MM:SS AM/PM`. |
| Relative labels and hover stamps | Views show `nuu` relative durations (`just now`, `10min ago`) and carry `stamp()` in hover titles and dialogs. |

## 6. Layout, Spatial Architecture, and Focal Point Design

### Shell and Container

- **Root container (`#app`)**: Full-viewport column (`100dvh`) with centered `main` (`padding-inline: max(32px, calc((100% - 1116px) / 2))` on desktop, `16px` at `<= 40em`).
- **Top bar (`header`)**: Sticky header (`border-bottom: 1px solid var(--line)`) with `.brand` left, `.tabs` centered, and `#status` plus `#logout` right (`#status` hides at `<= 75em`).
- **View switching**: Only the active `section[data-page]` is visible (`hidden` removed). The header tab (`aria-current="page"`) names the active view without a redundant `<h1>` title block.

### View Grids

| View | Route (`data-page`) | Desktop Grid (`> 60em`) | Focal Point |
| --- | --- | --- | --- |
| Overview | `overview` | 4-column `.kpis` strip above a 2-column `.flow` (`gap: 16px`) | Top-left `.kpi` card (`In flight now`) and `.status-card` health row |
| Requests | `requests` | `.requests-bar` filter bar above 2-column `.flow` (`table.requests` + `260px` `.legend`) | Live request table (`table.requests`) with right `.legend` |
| Models | `models` | `.pools` strip above `.panel` with sticky `.toolbar` and `table.models` | Search input (`#search`) and pool summary cards |
| Limits | `limits` | `.editbar` above 2-column `.flow` (`table.limits` + `260px` `#balances`) | Lowest remaining quota progress bar |
| Notifications | `notifications` | Single-column `.notification-feed` card | Recent provider reset, limit, and catalog rebuild rows |
| Settings | `settings` | 2-column `.settings` (`240px` `.rail` with `minmax(0, 1fr)` track + `.section-pane`) | Active settings category card in `.section-pane` |

## 7. Core Functional and Domain Components

The UI implementation in `daedalus/dashboard/ui/app.js` and `daedalus/dashboard/ui/index.html` builds all views from 8 shared component families. See [Component Visual Catalog](design-visuals/components.md) for annotated visual boards.

1. **Header Navigation (`.top`, `.tabs a`)**: 6 route links (`Overview` through `Settings`) with model (`#tab-models-count`) and notification (`#tab-notifications-count`) count badges.
2. **KPI Stat Strip (`.stats`, `.stat`)**: 4 equal-width cards on Overview showing `In flight now`, `Success rate`, `Lowest remaining limit`, and `Models in the catalog`.
3. **Pool and Quota Bars (`.pool-row`, `.limit-row`, `.bar`)**: Horizontal `6px` progress bars (`border-radius: 999px`, track `var(--bar)`, fill `var(--accent)` or `var(--bad)`) that show model share or remaining quota.
4. **Filter Toolbar (`.toolbar`)**: Flex row (`gap: 8px`) with text search, status `<select>`, time-range `<select>`, `#live-toggle`, and `#request-clear`.
5. **Requests Table and Code Legend (`table.requests`, `.code-legend`)**: 11-column log table paired with a `260px` reference card for routing transition codes (`ctx`, `hlt`, `lmt`, `err`).
6. **Settings Shell (`.settings-shell`, `.settings-menu`, `.setting-row`)**: 4 grouped category cards (`Routing / Affinity / Load Distribution / Limits / Optimization`, `Privacy / Catalog / Hooks`, `Personalization`, `Providers / API Keys / YAML / Maintenance`) that load the right-hand editor pane. Desktop rail and single-line pane rows use a `40px` height (`44px` below `900px`).
7. **Token Chip Editor (`.pills`, `.pill`, `[data-add]`)**: Inline keyword and pattern tag lists with confirmation-gated removal and modal or inline addition.
8. **Modal Dialogs (`<dialog id="modal">`, `<dialog id="chain-modal">`, `<dialog id="model-modal">`)**: Native `<dialog>` overlays opened via `showModal()` (`border-radius: 10px`, `border: 1px solid var(--line)`). Each dialog has a top-right `x` button, bottom-right actions, `Cancel` autofocus on confirmations, and focus return on `close`.

## 8. Motion, Interaction, and States

### Motion Tokens

- `--fast: .12s`
- `--soft: .2s`
- `--ease: cubic-bezier(.2, .7, .3, 1)`

Transitions apply only to `background-color`, `border-color`, `color`, `opacity`, and `transform`. Layout properties (`width`, `height`, `padding`, `margin`) never animate.

### Applied `nuu` Recipes

| Recipe | Surface / Selector | Behavior |
| --- | --- | --- |
| `offline-fonts` | `index.html`, `@font-face`, `start()` | Preloads bundled WOFF2 fonts with `crossorigin`, pairs `font-display: block` with metric-matched fallbacks, and awaits `document.fonts?.ready` before revealing `#app`. |
| `scrollbars` | `::-webkit-scrollbar`, `@supports not selector(::-webkit-scrollbar)` | Styles a `10px` scrollbar with an `8px` rounded `--scrollbar-thumb` (`#c8c8d0` light, `#302e2c` dark) on `var(--bg)`, hides arrow buttons, and scopes `scrollbar-width: thin` to non-WebKit engines. |
| `loading-bar` | `header::after`, `section[data-page][data-loading="true"]` | Anchors a `2px` bottom-edge `scaleX` bar on `header` after a `200ms` delay (`0.45` static opacity under reduced motion) and sets `aria-busy` in `finally`. |
| `page-memory` | `state.memory`, `requestPages`, `loadSettings()` | Paints cached views immediately, keys request slices on catalog and head version, and skips DOM redraws when fresh JSON matches memory. |
| `live-dot` | `.pulse`, `tr.live-row` | Renders a `6px` amber (`var(--orange)`) `aria-hidden="true"` dot with a `1.4s` pulse and zero dimension shift. |
| `modal-manager` | `.modal`, `.modal-head`, `.modal-close`, `confirmDeleteEntry()` | Places a top-right `x` button, restores trigger focus on `close`, focuses `Cancel` on confirmations, and gates deletions with a modal. |
| `scroll-edge-fade` & `overflow-and-clipping` | `--edge-start`, `--edge-end`, `.floating-info-tip` | Applies `12px` / `192px` scroll-driven masks (bottom-only below sticky headers) and viewport-clamped `position: fixed` tooltips (`min(320px, calc(100vw - 16px))`). |

### Approved `nuu` Deviations

| Marker | Surface / Selector | Reason |
| --- | --- | --- |
| `nuu-deviation: NUM201` | `.chip`, `.chip.flag`, `.pill`, `.ghost` (`style.css`) | Compact inline badges and pills (`0 7px` to `4px 12px`) preserve 1-line desktop rows and 4 visible phone cards. |
| `nuu-deviation: NUM202` | `body`, `th`, `td`, `.card.pool` (`style.css`) | Dense operational type (`11`-`15` px, `22` px KPI) and cell paddings (`6`/`10`/`12` px) fit 11-column tables without wrapping slugs. |

### Interactive States

| State | Visual Treatment | Selector / Trigger |
| --- | --- | --- |
| Default | Surface `var(--panel)` or `var(--field)`, border `1px solid var(--line)` | All interactive controls |
| Hover | Subtle surface shift (`color-mix(in srgb, var(--text) 5%, transparent)`) | `.tabs a:hover`, `tr[data-chain-at]:hover`, `.setting-row:hover` |
| Focus-visible | `outline: 2px solid var(--accent-text)`, `outline-offset: 2px` (keyboard only) | `:focus-visible` under `:root[data-keyboard-nav="true"]` |
| Active / Current | `color: var(--text)` with `2px solid var(--accent)` underline or `background: color-mix(in srgb, var(--accent) 14%, transparent)` | `.tabs a.on`, `.settings-menu button.on` |
| Pressed toggle | `border-color: var(--accent)`, `color: var(--accent-text)` | `#live-toggle[aria-pressed="true"]` |
| Loading | `2px` delayed `header::after` edge bar and `aria-busy="true"` on active `section[data-page]` | `call()` when `spinner !== false` |
| Disabled | `opacity: .45`, `cursor: not-allowed` | `:disabled` buttons and inputs |
| Empty state | Muted helper copy (`color: var(--muted)`, `padding: 14px 16px`) inside `.panel` | `#requests-empty`, `#notifications-empty`, `#overview-requests` |

## 9. Responsive and Adaptive Design

`daedalus/dashboard/ui/style.css` and `app.js` use the `nuu` house breakpoints (`75em`, `60em`, and `40em` width, plus `30em` landscape height):

| Breakpoint | Shell Layout | Navigation | View Adjustments |
| --- | --- | --- | --- |
| Wide Desktop (`> 75em` / `> 1200px`) | Full-width `header` and centered `main` (`32px` min gutter) | Single-row header with `.tabs` and `#status` chips | 4-column `.kpis`, 2-column `Requests`, `Limits`, and `Settings` (`240px` `.rail`) |
| Narrow Desktop (`<= 75em` / `<= 1200px`) | Centered `main` (`32px` min gutter) | `#status` hides so `.tabs` stay on 1 row | Keeps 2-column desktop layouts above `60em` |
| Tablet (`<= 60em` / `<= 960px`) | `32px` horizontal `main` gutter | Header stacks `.tabs` into a scrollable strip | `2x2` `.kpis`, `Requests` folds to cards, `Limits` stacks `100%` wide, and `Settings` uses drill-down (`44px` touch floor) |
| Phone (`<= 40em` / `<= 640px`) | `16px` gutter and `76px` bottom clearance | Scrollable `.tabs` strip with `44px` targets | Horizontal `.pools` strip, `Models` / `Keys` / `Limits` fold to cards, `.model-stats` and `#balances` use 1 column, and timestamps use `nuu` compact units |
| Landscape Short Height (`max-height: 30em` / `<= 480px`) | Outer `main` scroll unlocked | Compact header and `16px` `.login` top offset | `Settings`, `Providers`, `Requests`, and `Limits` scroll in `main`, and `.modal` caps height at `calc(100dvh - 32px)` |

## 10. Anti-Patterns and Guardrails

1. **No redundant page-title headers**: Do not add an `<h1>` or `.page-head` title bar inside `section.page`. The sticky `.top` navigation bar already shows the active page (`aria-current="page"`).
2. **No hardcoded colors outside `:root`**: Every color in `style.css` and `app.js` references a CSS custom property. Light (`:root`) and dark (`:root[data-theme="dark"]`) schemes stay in sync.
3. **No unclickable log rows with hidden errors**: Every request row with fallback attempts or an upstream error sets `data-chain-at`, `tabindex="0"`, and `role="button"`. Keyboard and pointer users can open `#chain-modal`.
4. **No decorative charts or sparklines**: Use tabular numerals and horizontal `.bar` fills instead of multi-axis charts or circular gauges.
5. **No layout shifts or state resets on poll**: Style scrollbars with the `10px` rounded `--scrollbar-thumb` recipe on `var(--bg)` without arrow buttons. Skip DOM redraws on unchanged poll responses so scroll position and dialog state stay intact.

## 11. Design Revision Log

| Date | Summary | Affected Surfaces | Visual Reference |
| --- | --- | --- | --- |
| 2026-10-25 | Applied `nuu` `scrollbars`, `loading-bar`, `page-memory`, `live-dot`, `modal-manager` (top-right `x`, trigger focus return, delete confirmations), `offline-fonts`, and tooltip anti-clipping recipes. | `Overview`, `Requests`, `Models`, `Limits`, `Notifications`, `Settings` (`daedalus/dashboard/ui/index.html`, `style.css`, `app.js`, `en.js`) | [Dashboard Visual Reference](design-visuals/dashboard.md), [Component Visual Catalog](design-visuals/components.md) |
| 2026-10-25 | Adopted `nuu` house breakpoints (`75em`, `60em`, `40em`, `30em`) and `24h`/`12h` time formatting, fixed `.rail` overlap and `.rail-search` focus, folded `Requests` at `60em`, and stretched `Limits` full width at `<= 60em`. | `Overview`, `Requests`, `Models`, `Limits`, `Notifications`, `Settings` (`daedalus/dashboard/ui/style.css`, `app.js`) | [Dashboard Visual Reference](design-visuals/dashboard.md), [Component Visual Catalog](design-visuals/components.md) |
| 2026-10-25 | Auto-loaded older `Requests` rows on scroll, adopted `nuu` full and mobile-abbreviated relative timestamps, and added `Settings` content/key search highlights. | `Overview`, `Requests`, `Limits`, `Notifications`, `Settings` (`daedalus/dashboard/ui/style.css`, `app.js`, `en.js`) | [Dashboard Visual Reference](design-visuals/dashboard.md), [Component Visual Catalog](design-visuals/components.md) |
| 2026-10-25 | Set `--radius` to `10px`, card padding to `16px`/`8px`, and desktop `Settings` rows to `40px` (`44px` mobile). Removed the mobile `Limits` check-stack cap. | `Overview`, `Limits`, `Settings`, `Providers` (`daedalus/dashboard/ui/style.css`, `docs/design/`, `docs/images/`) | [Component Visual Catalog](design-visuals/components.md), [Foundation Style Reference](design-visuals/styles.md) |
| 2026-10-25 | Suppressed pointer focus rings, inset clipped `:focus-visible` rings, stacked `Requests` `.legend` at `<= 960px`, and harmonized `Settings` tiles. | `Overview`, `Requests`, `Models`, `Limits`, `Notifications`, `Settings` (`daedalus/dashboard/ui/style.css`, `app.js`, `docs/design/`) | [Dashboard Visual Reference](design-visuals/dashboard.md), [Component Visual Catalog](design-visuals/components.md) |
| 2026-10-24 | Removed redundant `.page-head` title bars across all views because the `.top` navigation bar already indicates the active view. Restored accessibility semantics and contrast tokens across light and dark themes. | `Overview`, `Requests`, `Models`, `Limits`, `Notifications`, `Settings` (`daedalus/dashboard/ui/index.html`, `style.css`, `app.js`) | [Dashboard Visual Reference](design-visuals/dashboard.md) |
| 2026-10-24 | Initialized the design record (`docs/design.md`), visual reference catalog (`docs/design-visuals/`), and annotated composite boards (`docs/images/`). | `docs/design.md`, `docs/design-visuals/*.md`, `docs/images/*.png` | [Component Visual Catalog](design-visuals/components.md), [Foundation Style Reference](design-visuals/styles.md) |
