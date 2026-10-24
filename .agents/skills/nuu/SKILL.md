---
name: nuu
description: Build and revise web pages in a settled house style. Use when a task adds or changes a page, dialog, table, form, theme, empty or error state, or any UI that needs icons, colour roles, light and dark schemes, type, spacing, breakpoints, motion, states, optimistic writes, a design document, or measured values at 1280 and 360 px.
license: MIT
---

# Nemo UI/UX (nuu)

## Shared workflow

- Install this skill's package with `pip install -e <skill>` or `uv pip install -e <skill>` (`uv tool install -e <skill>`) and run `nuu` directly; preserve an existing design record (`docs/design.md`), or use `references/style.css` (`-P house`).
- Search the catalog (`nuu search`) and verify assets (`nuu assets`), then run static checks (`nuu lint`, `nuu extract`) before rendered checks (`nuu inspect`, `flow`, `a11y`, `audit`, `live`) at project widths plus `1280` and `360` px.
- Retain screenshots, measurements (`measure.py`), flow results, and annotated composite boards (`nuu annotate`), separating automated findings from visual judgment, naming each UI deviation from `nuu` with its reason, and naming unrun checks.
- Keep live edits read-only unless authorized (`-Z`), repair only the requested scope, and update `docs/design.md` with linked visual references in the same change.
- Format audit and verification reports with [references/audit-report.md](references/audit-report.md), omit empty table sections, and state 0 in Counts.

## House profile

Apply this skill to a page, a component, or an interface change. The sheet is the reference for values, and the design document records the calls that a repository already made.

## When to use

- A task adds or changes a page, dialog, table, form, theme, or state.
- A review asks whether the interface matches the house style.
- A new pattern needs a decision that later pages must repeat.
- A page exists and the task changes one of these clauses.

## Clauses

1. Take the glyph a control needs from the icon set, keep its licence beside it, and inline that same path. A name with no glyph shows its word.

2. Every glyph on a surface takes the single size the sheet names.

3. A component asks the sheet for a role. Nobody writes a colour literal into a component.

4. The roles resolve in light and in dark. The theme control names System, Light, and Dark, so the page can follow the machine.

5. Four sizes from the sheet, three weights, one eyebrow size, and a tabular face for IDs, and MUST bundle and load custom fonts via [references/recipes/offline-fonts.md](references/recipes/offline-fonts.md) without reload jitter.

6. Every gap lands on the 8 px scale. Rows are 40 px, panels take a 10 px radius and no shadow, icon-only buttons are symmetric, and a touch target is at least 44 px.

7. The sheet names 75em, 60em, and 40em for width, plus 30em for short-height landscape. Choose each breakpoint where the content breaks. A dimension outside the list gets no behaviour of its own.

8. Every transition shares one curve over 160 to 220 ms; for animations, vendor [GSAP](https://github.com/greensock/GSAP) into the project as the go-to library. Reduced motion turns animation off.

9. Each control shows default, hover, focus, active, disabled, and loading. Lists also show empty, failed, and offline. Hover exists only where a pointer does.

10. The pending row appears before the reply. A failure retries once with the same ID, so the list holds one row.

11. Record settled calls in the authoritative design record.

12. Read padding, margin, gap, and size per element from the rendered page, compare each with the sheet, and check 1280 and 360.

13. When a page needs a pattern the sheet lacks, the change adds the entry, both widths, and the screenshots, in one pull request.

14. Enable touch input (`hasTouch` and `isMobile`) when auditing or inspecting mobile viewports (`width <= 768` px or `height <= 480` px) so `@media (pointer: coarse)` rules apply.

15. Place a Lucide `x` exit button at the top right of a container, and place other action buttons at the bottom right.

## Go-to libraries

- For draggable and reorderable lists, vendor only the required part of [SortableJS](https://github.com/SortableJS/Sortable) with its MIT license instead of writing custom drag-and-drop handlers.
- For animations and scroll-driven motion, vendor [GSAP](https://github.com/greensock/GSAP) into the project as the go-to library and respect `prefers-reduced-motion: reduce`.

## Order of work

1. Read the authoritative design record, or create docs/design.md from references/design-document.md if none exists.
2. Copy the declarations the page needs from `references/style.css`. Keep the role names, and change only the values that the repository settles.
3. Read `references/examples.md` when the change touches a clause, so the correct and incorrect forms are both in view.
4. Make only authorized page changes, then measure both widths and fix every value the check names.
5. Update the authoritative design record in the same change as a settled design decision.

## Measuring

`scripts/measure.py check` reads a JSON report of computed values and names every value that leaves the sheet. A value passes when it sits on the scale, when a type property takes a size from the ramp, or when the sheet names that size for that property, such as the 10 px radius.

`scripts/measure.py ruler` reads a gap between two guides on the page, the way a screen ruler does, and answers whether each axis sits on the scale.

```bash
python scripts/measure.py check report.json
python scripts/measure.py ruler --from 120,340 --to 264,340
```

`references/measure.md` holds the browser snippet that writes the report, and the list of what to keep.

## Files

- `references/style.css`: the sheet as code. Roles, type, space, states, and motion.
- `references/design-document.md`: the eleven sections of `docs/design.md`, with a filled example.
- `references/examples.md`: one correct and incorrect pair per clause, drawn from the sheet.
- `references/measure.md`: the report shape, the snippet, and the ruler.
- `scripts/measure.py`: the check and the ruler.

- `scripts/nuu`: CLI entry point (`references/nuu.md`), alongside `scripts/measure.py` (`references/measure.md`), `scripts/annotate.py` (`references/annotate.md`), and `scripts/browser.cjs`.
- `data/vendor/`: licensed catalog data and source hashes.
- `references/recipes/offline-fonts.md`: bundled and offline zero-jitter font recipe (`preload` with `crossorigin`, `font-display: block`, inline Base64 WOFF2, `FontFace` binary buffers, and metric-matched fallbacks).
- `references/recipes/scroll-edge-fade.md`: scroll-driven CSS edge-fade mask recipe (`@supports (animation-timeline: scroll(self))`).
- `references/audit-report.md`: standalone template for nuu audits and visual verification reports.
