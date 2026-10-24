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

- **Surface treatment**: Flat, matte panels (`--panel`) sit on a low-glare canvas (`--bg`) with `1px solid var(--line)` borders and a `12px` radius (`--radius`). The UI uses no glassmorphism, no backdrop blur, and no drop shadows on page cards.
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

The console uses native system font stacks for zero network latency:

- **UI sans stack**: `14px/1.5 system-ui, "Segoe UI", Roboto, sans-serif`
- **Monospace stack (`--mono`)**: `ui-monospace, "Cascadia Code", "SF Mono", Menlo, Consolas, monospace`

| Element | Selector | Size | Weight | Line Height | Numeric Discipline |
| --- | --- | --- | --- | --- | --- |
| Brand mark | `.brand` | `18px` | `700` | `1.1` | Standard |
| Hero stat value | `.stat-num` | `22px` | `700` | `1.2` | `font-variant-numeric: tabular-nums` |
| Modal / section title | `.modal h3`, `.settings-pane h3` | `15px` | `600` | `1.35` | Standard |
| Panel title | `.panel-title`, `h2` | `14px` | `600` | `1.4` | Standard |
| Body and table cells | `body`, `td` | `14px` | `400` | `1.5` | `tabular-nums` on `.num` and `.mono` |
| Table header | `th` | `12px` | `500` | `1.4` | Standard |
| Micro label / badge | `.hint`, `.tag`, `.tier`, `.stat-label` | `12px` | `500` | `1.4` | `tabular-nums` on counters |

## 6. Layout, Spatial Architecture, and Focal Point Design

### Shell and Container

- **Root container (`#app`)**: `max-width: 1280px`, centered (`margin: 0 auto`), with `padding: 0 24px 48px` on desktop and `padding: 0 14px 32px` below `860px`.
- **Top bar (`.top`)**: Sticky header (`padding: 14px 0`, `border-bottom: 1px solid var(--line)`) containing `.brand` on the left, `.tabs` navigation in the center, and `#logout` on the right.
- **View switching**: Only `section.page.on` renders (`display: block`), while inactive views use `display: none`. The header tab (`aria-current="page"`) names the active view without a redundant `<h1>` title block.

### View Grids

| View | Route (`data-page`) | Desktop Grid (`>= 860px`) | Focal Point |
| --- | --- | --- | --- |
| Overview | `overview` | 4-column `.stats` strip (`gap: 12px`) above a 2-column `.split` (`1.05fr 1fr`, `gap: 16px`) | Top-left `.stat` card (`In flight now`) and `.health-head` status banner |
| Requests | `requests` | `.toolbar` filter bar above `.requests-layout` (`1fr 260px`, `gap: 14px`) | Live request table (`table.requests`) with sticky right `.code-legend` |
| Models | `models` | `.toolbar` filter bar above stacked provider panels (`.panel.provider-card`) | Search input (`#model-search`) and pool summary rows |
| Limits | `limits` | Single-column `.panel` (`#limits-list`) with 3-column quota rows (`minmax(180px, 1.2fr) 2fr auto`) | Lowest remaining quota progress bar |
| Notifications | `notifications` | Single-column `.panel` (`#notifications-list`) with action toolbar | Unread or recent provider and router event rows |
| Settings | `settings` | 2-column `.settings-shell` (`220px 1fr`, `gap: 20px`) with grouped left `.settings-menu` and right `.settings-pane` | Active settings category card (`#settings-pane`) |

## 7. Core Functional and Domain Components

The UI implementation in `daedalus/dashboard/ui/app.js` and `daedalus/dashboard/ui/index.html` builds all views from 8 shared component families. See [Component Visual Catalog](design-visuals/components.md) for annotated visual boards.

1. **Header Navigation (`.top`, `.tabs a`)**: 6 route links (`Overview` through `Settings`) with model (`#tab-models-count`) and notification (`#tab-notifications-count`) count badges.
2. **KPI Stat Strip (`.stats`, `.stat`)**: 4 equal-width cards on Overview showing `In flight now`, `Success rate`, `Lowest remaining limit`, and `Models in the catalog`.
3. **Pool and Quota Bars (`.pool-row`, `.limit-row`, `.bar`)**: Horizontal `6px` progress bars (`border-radius: 999px`, track `var(--bar)`, fill `var(--accent)` or `var(--bad)`) that show model share or remaining quota.
4. **Filter Toolbar (`.toolbar`)**: Flex row (`gap: 8px`) with text search, status `<select>`, time-range `<select>`, `#live-toggle`, and `#request-clear`.
5. **Requests Table and Code Legend (`table.requests`, `.code-legend`)**: 11-column log table paired with a `260px` reference card for routing transition codes (`ctx`, `hlt`, `lmt`, `err`).
6. **Settings Shell (`.settings-shell`, `.settings-menu`, `.setting-row`)**: 4 grouped category cards (`Routing / Affinity / Load Distribution / Limits / Optimization`, `Privacy / Catalog / Hooks`, `Personalization`, `Providers / API Keys / YAML / Maintenance`) that load the right-hand editor pane.
7. **Token Chip Editor (`.chips`, `.chip`, `.chip-add`)**: Inline keyword tag list for `routing.escalation` and `routing.switch` with 1-click removal (`×`) and modal addition (`+ Add`).
8. **Modal Dialogs (`<dialog id="modal">`, `<dialog id="chain-modal">`, `<dialog id="model-modal">`)**: Native `<dialog>` overlays (`border-radius: 12px`, `border: 1px solid var(--line)`) for setting values, fallback chains, and model details.

## 8. Motion, Interaction, and States

### Motion Tokens

- `--fast: .12s`
- `--soft: .2s`
- `--ease: cubic-bezier(.2, .7, .3, 1)`

Transitions apply only to `background-color`, `border-color`, `color`, `opacity`, and `transform`. Layout properties (`width`, `height`, `padding`, `margin`) never animate.

### Interactive States

| State | Visual Treatment | Selector / Trigger |
| --- | --- | --- |
| Default | Surface `var(--panel)` or `var(--field)`, border `1px solid var(--line)` | All interactive controls |
| Hover | Subtle surface shift (`color-mix(in srgb, var(--text) 5%, transparent)`) | `.tabs a:hover`, `tr[data-chain-at]:hover`, `.setting-row:hover` |
| Focus-visible | `outline: 2px solid var(--accent)`, `outline-offset: 2px` | `:focus-visible` across links, buttons, inputs, and clickable rows |
| Active / Current | `color: var(--text)` with `2px solid var(--accent)` underline or `background: color-mix(in srgb, var(--accent) 14%, transparent)` | `.tabs a.on`, `.settings-menu button.on` |
| Pressed toggle | `border-color: var(--accent)`, `color: var(--accent-text)` | `#live-toggle[aria-pressed="true"]` |
| Disabled | `opacity: .45`, `cursor: not-allowed` | `:disabled` buttons and inputs |
| Empty state | Muted helper copy (`color: var(--muted)`, `padding: 14px 16px`) inside `.panel` | `#requests-empty`, `#notifications-empty`, `#overview-requests` |

## 9. Responsive and Adaptive Design

`daedalus/dashboard/ui/style.css` adapts the console across 2 breakpoints:

| Viewport Range | Shell Layout | Navigation | View Adjustments |
| --- | --- | --- | --- |
| Desktop (`> 860px`) | `#app` `max-width: 1280px`, `padding: 0 24px 48px` | Single-row `.top` bar with inline `.tabs` | 4-column `.stats`, 2-column `.split`, 2-column `.requests-layout`, 2-column `.settings-shell` |
| Tablet and Mobile (`<= 860px`) | `#app` `padding: 0 14px 32px` | `.top` wraps `.tabs` into a scrollable strip (`order: 3`, `overflow-x: auto`) | 2x2 `.stats`, 1-column `.split` and `.requests-layout`, 5-column `table.requests`, and drill-down `.settings-shell` |
| Narrow Mobile (`<= 520px`) | `#app` `padding: 0 12px 28px` | Scrollable `.tabs` strip with compact padding | `.limit-row` stacks label, bar, and counter vertically |

## 10. Anti-Patterns and Guardrails

1. **No redundant page-title headers**: Do not add an `<h1>` or `.page-head` title bar inside `section.page`. The sticky `.top` navigation bar already shows the active page (`aria-current="page"`).
2. **No hardcoded colors outside `:root`**: Every color in `style.css` and `app.js` references a CSS custom property. Light (`:root`) and dark (`:root[data-theme="dark"]`) schemes stay in sync.
3. **No unclickable log rows with hidden errors**: Every request row with fallback attempts or an upstream error sets `data-chain-at`, `tabindex="0"`, and `role="button"`. Keyboard and pointer users can open `#chain-modal`.
4. **No decorative charts or sparklines**: Use tabular numerals and horizontal `.bar` fills instead of multi-axis charts or circular gauges.
5. **No custom scrollbars or layout shifts on poll**: Periodic `refreshFast` and `refreshSlow` polls update DOM text and attributes in place without resetting scroll position or dialog state.

## 11. Design Revision Log

| Date | Summary | Affected Surfaces | Visual Reference |
| --- | --- | --- | --- |
| 2026-10-24 | Removed redundant `.page-head` title bars across all views because the `.top` navigation bar already indicates the active view. Restored accessibility semantics and contrast tokens across light and dark themes. | `Overview`, `Requests`, `Models`, `Limits`, `Notifications`, `Settings` (`daedalus/dashboard/ui/index.html`, `style.css`, `app.js`) | [Dashboard Visual Reference](design-visuals/dashboard.md) |
| 2026-10-24 | Initialized the design record (`docs/design.md`), visual reference catalog (`docs/design-visuals/`), and annotated composite boards (`docs/images/`). | `docs/design.md`, `docs/design-visuals/*.md`, `docs/images/*.png` | [Component Visual Catalog](design-visuals/components.md), [Foundation Style Reference](design-visuals/styles.md) |
