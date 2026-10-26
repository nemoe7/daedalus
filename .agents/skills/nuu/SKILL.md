---
name: nuu
description: >
  Use when designing, building, refactoring, reviewing, or auditing a web page,
  component, stylesheet, layout, or client flow: icons, colour tokens, light and
  dark schemes, typography, spacing, responsive breakpoints, motion, control and
  list states, optimistic writes, docs/design.md, computed-style measurement,
  screenshot annotation, or accessibility and usability inspection.
---

# House UI and UX

One short specification for every page we ship, paired with a small command-line inspector (`scripts/nuu`). Each clause below has a visual pair in `references/examples.md`, a copyable declaration in `references/style.css`, and an automated or manual check in `references/nuu.md`.

## Use when

- A task creates, edits, or reviews HTML, CSS, SVG, or client-side UI code.
- A page needs a design system, palette, type pairing, layout, or `docs/design.md`.
- A pull request or release needs a rendered layout, contrast, keyboard, or flow audit.

## Do not use when

- The task is backend-only, CLI-only, or data-pipeline work with no user-facing surface.
- The user supplies an explicit external brand book that conflicts with a house token; in that case, record the override in `docs/design.md` and keep the remaining clauses.
- A page exists and the task changes one of these clauses.

## Clauses

1. Every icon comes from the ordered open-source icon provider ladder: `lucide` first, then `simple-icons` for brand marks, then `phosphor` only when neither `lucide` nor `simple-icons` carries the glyph. Keep one `24x24` grid and a `2 px` stroke (`stroke-linecap="round"`, `stroke-linejoin="round"`), and use a plain text label if none of the three providers has the glyph (`NUI001`, `NUI002`).
2. Every glyph on a surface takes the single size the sheet specifies (`16x16 px` default, or `20x20 px` for standalone hero icons) (`NUI003`).
3. Every colour is a CSS custom property named for its role (`--bg`, `--surface`, `--surface-2`, `--field`, `--line`, `--text`, `--muted`, `--accent`, `--accent-fill`, `--on-accent`, `--danger`, `--warn`, `--ok`, `--focus`). No component rule contains a hex, RGB, or HSL literal.
4. Build both themes from one role set (`:root` + `@media (prefers-color-scheme: dark)` + `:root[data-theme]`) and offer a 3-way theme control (`System`, `Light`, `Dark`) with centered Lucide `monitor`, `sun`, and `moon` glyphs.
5. Use the seven-step type ramp (`11, 12, 14, 16, 18, 22, and 28 px`), four weights (`400, 500, 600, 700`), `11 px` uppercase `.eyebrow` labels, and a tabular face for IDs, and MUST bundle and load custom fonts via [references/recipes/offline-fonts.md](references/recipes/offline-fonts.md) without reload jitter.
6. Every gap lands on the `4 px` scale (`4, 8, 12, 16, 24, 32, 48 px`), or the `8 px` scale when opted in. Rows are `32 px` (`40 px` on `8 px`), and panels take concentric radii (`0, 4, 6, 10, or 9999 px`; `6 px` default, `10 px` on `8 px`) with no shadow. Icon-only buttons are symmetric at `32x32 px` (`>= 24x24 px` floor).
7. Layouts change at the compact breakpoint subset (`68em` / `1088px`, `60em` / `960px`, and `30em` / `480px` width, plus `24em` / `384px` max-height for landscape touch viewports), written in `em`, with zero horizontal scroll (`NUB101`).
8. Every transition shares one curve (`cubic-bezier(0.22, 1, 0.36, 1)`) over `100-150 ms` for hover/press and `160 to 220 ms` for panels. ALWAYS use vendored [GSAP](https://github.com/greensock/GSAP) for animations without spring overshoot. Reduced motion turns animation off.
9. Each control shows default, hover, focus (`2 px` solid `var(--focus)` ring with `2 px` offset and `>= 3:1` contrast on `:focus-visible` only, `NUA304`, `NUA307`), active, disabled, and loading without changing element dimensions (`NUA309`, `NUL005`) via [references/recipes/live-dot.md](references/recipes/live-dot.md) for the loading state of a row, and via a fixed-slot spinner or pulsing dot in [references/recipes/loading-bar.md](references/recipes/loading-bar.md) for a button or compact control awaiting a fetch. Lists also show empty, failed, and offline. Hover exists only where a pointer does, and the focus outline appears only on keyboard navigation (`NUA307`).
10. Write operations show the pending row immediately and persist view state in URL query parameters or `localStorage` via [references/recipes/page-memory.md](references/recipes/page-memory.md).
11. Each repository carries its own `docs/design.md` (or the custom path configured at `[design].path` in `nuu.toml`), updated in the same change as any visual work (`NUL004`).
12. Read padding, margin, gap, and size per element from the rendered page, compare each with the sheet, and check the house viewports (`1920x957`, `1080x2437`, `958x958`, and `411x799`, plus `855x325` landscape).
13. When a page needs a new pattern, add the entry, the house viewports (`1920x957`, `1080x2437`, `958x958`, and `411x799`, plus `855x325`), and screenshots in one PR.
14. Inline suppression markers (`nonuu: RULE_ID`) silence false-positive findings on that line and appear in `lint`, `audit`, and `collect` output. Approved house-style deviations use `nuu-deviation: <reason>` in comments or `docs/design.md` (`nuu collect --deviations`). Agents MUST NEVER add `nuu-deviation:` without explicit user approval.

Component and interaction patterns live in `references/recipes/`: [modal-manager.md](references/recipes/modal-manager.md) (top-right `.close-button` and delete confirmation), [range-sliders.md](references/recipes/range-sliders.md) (step ticks and `<output>` bubbles), [relative-timestamps.md](references/recipes/relative-timestamps.md) (relative ages, full or abbreviated by width), [loading-bar.md](references/recipes/loading-bar.md) (200 ms delayed edge bar and fixed-slot controls), [scrollbars.md](references/recipes/scrollbars.md) (`10 px` rounded thumb without arrow buttons), and [overflow-and-clipping.md](references/recipes/overflow-and-clipping.md) (unclipped focus rings, shadows, and tooltips).

## Workflow

1. Read `docs/design.md` in the target repo (or `[design].path` from `nuu.toml`). If none exists, create one from `references/design-document.md`.
2. Read `references/style.css` and copy only the declarations the page needs. Keep the custom-property names; change their values in `:root` only when `docs/design.md` records a palette or scale.
3. Read `references/examples.md` and the matching recipe in `references/recipes/` when the change touches a clause or component pattern, so the correct and incorrect dark-mode forms are both in view.
4. Build or edit the page. Run `scripts/nuu audit -p <repo>` for static and measure checks. Run `scripts/nuu audit -p <repo> -u <http-url> -f <flows.json> -r <selector>` when a live server is available.
5. When reviewing screenshots in a pull request or report, annotate findings with `scripts/nuu annotate` (using `--crop <x,y,w,h|auto>` when focusing on a specific component) or assemble a numbered comparison board with `scripts/nuu board` (`--type panels` for before/after PNGs or `--type design --spec boards.json` for HTML/screenshot design boards; pass `-W` / `--wireframe` when structural bounding-box overlays are needed). Both commands automatically detect and embed the project's configured `@font-face` / `--font-sans` typography (`nuu.toml`, CSS, or HTML `<style>`).

## Reference files

- `AGENTS.md`: mandatory agent workflow rules for `nuu` (`--wireframe` / `-W`, `nuu walk`, `nuu board`, and `nuu.toml`).
- `references/nuu.toml`: default configuration (`Inter` / `Monaspace Neon` font stack, scale, and board defaults).
- `references/nuu.md`: CLI flags, exit codes, rule codes (`NUL001`–`NUA309`), and recipe index.
- `references/style.css`: house tokens, type ramp, `4 px` default scale (`8 px` opt-in via `:root[data-scale="8"]`), `.close-button`, and states.
- `references/examples.md`: dark-mode correct and incorrect UI pairs per clause, recipe, and audit rule.
- `references/design-document.md`: sections and a filled example for `docs/design.md`.
- `references/recipes/offline-fonts.md`: bundled and offline font loading without reload jitter.
- `references/recipes/live-dot.md`: `8x8 px` pulsing dot (`NUA309`, `NUL005`) that occupies the idle glyph slot without shifting row height.
- `references/recipes/loading-bar.md`: subtle `2px` edge loading bar (`200ms` delay, `aria-busy="true"`, compositor-only `scaleX`, static `prefers-reduced-motion` fallback) and fixed-slot inline control loading.
- `references/recipes/page-memory.md`: content-versioned in-memory view cache, optimistic writes, and URL/`localStorage` state persistence.
- `references/recipes/modal-manager.md`: shared `<dialog>` modal manager with `showModal()`, top-right `.close-button` (`-8 px` margin, focus/hover `--text` shift), and delete confirmation.
- `references/recipes/range-sliders.md`: range slider `<input type="range">` with `step` ticks, min/max labels, and live `<output>` bubble.
- `references/recipes/relative-timestamps.md`: singular, plural, and compact relative timestamp table (`just now`, `Xmin ago`, `yesterday`, `last week`, `last month`, `last year`).
- `references/recipes/scroll-edge-fade.md`: scroll-driven CSS `mask-image` edge fade (`@supports (animation-timeline: scroll(self))`).
- `references/recipes/floating-overlay-scroll-fade.md`: full-bleed scroll container fade behind floating header and composer overlays (`backdrop-filter: blur()`, `padding-inline`, and `@supports (animation-timeline: scroll(self))`).
- `references/recipes/scrollbars.md`: thin `10px` rounded-thumb scrollbar (`#302e2c`) on `var(--bg)` without arrow buttons or hover highlights unless keyboard navigation is active.
- `references/recipes/overflow-and-clipping.md`: anti-clipping and anti-overlap rules (`NUM209`, `NUM210`, `NUA308`) for flex/grid rows, focus rings, sticky headers, and viewport-clamped tooltips.
