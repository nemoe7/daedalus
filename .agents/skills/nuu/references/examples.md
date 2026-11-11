# Examples

One dark-mode UI pair per clause, recipe, and audit rule. The left card (`Pass`) follows the house sheet and recipes. The right card (`Fail`) shows the anti-pattern.

The image files sit in `examples/` beside this page.

## 1. One glyph ladder, one grid

Pass: Lucide chevron on its `24x24` grid (`stroke-width="2"`) and a plain text label when no glyph exists. Fail: a filled non-provider wedge and a missing-glyph box (`ICN001`, `ICN002`).

![Clause 1](examples/one-glyph-one-path.png)

## 2. One icon size per surface and paired text line-height match

Pass: keep comparable surface icons one size (`16x16 px` default, `20x20 px` standalone hero) (`ICN101`), and match text-paired icons to the paired text `line-height` (`ICN102`). Fail: mixed `14`, `18`, and `24 px` icons on one row or an oversized glyph beside compact text.

![Clause 2](examples/one-icon-size-per-surface.png)

## 3. Colour is named by its job

Pass: semantic role tokens (`--bg`, `--surface`, `--accent`, `--text`). Fail: hardcoded hex literals inside component rules or status conveyed by color alone.

![Clause 3](examples/colour-by-role.png)

## 4. One role set feeds both schemes

Pass: `:root` + `@media (prefers-color-scheme: dark)` + `:root[data-theme]` with a 3-way theme selector (`System`, `Light`, `Dark`) and centered Lucide `monitor`, `sun`, and `moon` glyphs. Fail: a binary toggle that overrides the OS preference.

![Clause 4](examples/one-role-set-both-schemes.png)

## 5. Nine-step type ramp and bundled fonts

Pass: the nine sizes are `11, 12, 14, 16, 18, 22, 24, 28, and 32 px`, with weights `400, 500, 600, 700`. Start at `32/18/14 px` for title, body, and small text, or `24/16/12 px` for dense dashboards. Count the `11 px` uppercase `.eyebrow` toward the per-view size limit (`TYP001`). Fail: off-ramp `15 px` body, weight `800`, and proportional IDs.

![Clause 5](examples/short-type-ramp.png)

## 6. Four-pixel spacing scale and concentric radii

Pass: use the `4 px` scale, a `32 px` row, a `6 px` radius, and no shadow by default. Use `32x32 px` icon buttons with a `24x24 px` minimum. Make buttons within each group equal in size (`HRC102`). Fail: off-scale `13 px` gap, `9 px` radius, an excessive dark-theme shadow, `18x18 px` icon button, and ragged button widths in a group.

![Clause 6](examples/one-spacing-scale.png)

![Clause 6 Equal-Size Button Groups](examples/button-group-equal-size.png)

## 7. Compact named breakpoints

Pass: `68em` (`1088px`), `60em` (`960px`), and `30em` (`480px`) width plus `24em` (`384px`) max-height in `em` with `0 px` horizontal overflow (`SPC206`). Fail: arbitrary pixel breakpoints and clipped mobile tables.

![Clause 7](examples/three-named-breakpoints.png)

## 8. One motion curve

Pass: `cubic-bezier(0.22, 1, 0.36, 1)` over `100-150 ms` for hover/press and `160-220 ms` for panels with vendored GSAP and `prefers-reduced-motion: reduce`. Fail: a `450 ms` overshoot spring that ignores reduced motion (contrast needs separate verification).

![Clause 8](examples/one-motion-curve.png)

![Clause 8 Animated](examples/one-motion-curve.gif)

## 9. Every control and list state is drawn

Pass: default, hover, `:focus-visible` (`2 px` solid `var(--focus)` with `2 px` offset), active, disabled, and fixed-slot loading (`ACC202`, `ACC205`). Fail: `outline: none` and hover rules that grow control dimensions (`ACC201`, `ACC204`).

![Clause 9](examples/every-state-drawn.png)

## 10. Optimistic writes and page memory (`recipes/page-memory.md`)

Pass: immediate pending row rendered under a held slow-network route and mocked clock (`ACC207`), reconciled under the same ID, with tab/filter/draft state persisted in URL query parameters or `localStorage`. Fail: frozen form, duplicate row on retry, and lost view state on reload.

![Clause 10](examples/write-shows-its-row.png)

## 11. Record the design system

Pass: update `[paths].doc` (`docs/design.md`) in the same change with its component table and linked boards (`DOC001`). Fail: leave a new component undocumented.

![Clause 11](examples/design-doc-record.png)

## 12. Measure across house viewports before landing

Pass: computed padding, gap, font size, and contrast verified at `1920x957`, `1080x2437`, `958x958`, `411x799`, and `855x325`. Fail: single desktop width checked by eye.

![Clause 12](examples/measure-before-landing.png)

## 13. A new pattern ships with its spec entry and viewport shots

Pass: `docs/design.md` entry, recipe, and all five house viewport screenshots in one PR. Fail: one-off page widget missing from `docs/design.md`.

![Clause 13](examples/new-pattern-entry-and-shot.png)

## 14. Inline suppression, deviation, and recipe markers

Pass: canonical `nonuu: SPC002` false-positive suppression, human-approved `nuu-deviation: <reason>` note, and `nuurecipe: modal-manager` tag surfaced by `nuu collect`. Fail: agent-invented `nuu-deviation:` without user approval or unknown `nuurecipe:` slug (`RCP001`).

![Clause 14](examples/inline-markers.png)

## 15. Outside list markers

Pass: use an outside marker and a `2ch` hanging indent. Fail: put markers inside the text area and misalign wrapped lines.

![Clause 15](examples/list-hanging-indent.png)

## 16. Heading and body line-height bands

Pass: headings (`h1` to `h6`) within `1.1` to `1.3x` font-size (`1.2x` default) and non-heading prose within `1.3` to `1.5x`. Fail: loose `1.65x` heading or cramped `1.05x` body copy (`TYP101`).

![Clause 16](examples/line-height-bands.png)

## 17. Heading-only negative letter-spacing

Pass: headings (`h1` to `h6`) tracked within `-0.005em` to `-0.03em` (`-0.015em` default) and non-heading prose at `0`. Fail: over-tightened `-0.05em` heading or tracked body copy (`TYP102`).

![Clause 17](examples/heading-letter-spacing.png)

## 18. Left-aligned multi-line prose

Pass: left-aligned (`text-align: start`) prose over `3` lines and short centered hero callouts of at most `3` lines. Fail: centered prose over `3` lines or `text-align: justify` (`TYP103`).

![Clause 18](examples/text-alignment.png)

## 19. Standalone prose container and measure width

Pass: multi-line prose and its standalone container sized within `50` to `75ch` (`65ch` default) so `SPC201` ink ratio stays full without exemptions. Fail: `105ch` full-bleed lines or narrow `30ch` standalone prose (`TYP201`).

![Clause 19](examples/body-text-width.png)

## 20. Card and dialog font-size hierarchy

Pass: at most `2` ramp font sizes per card or dialog and `3` per view, varying font-weight and `--muted` first. For landing pages and content sites, use up to `6` sizes per view when needed. Fail: `3` distinct font sizes inside one card (`HRC101`).

![Clause 20](examples/font-size-hierarchy.png)

## 21. Proximity spacing between and within groups

Pass: start inter-group gaps at `2x` intra-group gaps, with a `1.5x` minimum. Snap both to the Clause 6 scale (`4 px` intra, `16 px` inter). Fail: `12 px` label-to-input gap exceeding an `8 px` field-group gap (`SPC101`).

![Clause 21](examples/proximity-spacing.png)

## 22. Inline card alignment, fonts, and uppercase

Pass: align stacked card items on one inline edge with symmetric inline padding, house fonts, non-italic body blocks, and uppercase only on `.eyebrow` (`SPC102`, `TYP002`, `TYP003`). Fail: ragged inline insets, decorative fonts, italic body blocks, or uppercase prose.

![Clause 22](examples/core-principles.png)

## 23. One explicit primary action per card or dialog

Pass: at most one primary button per card or dialog, naming the concrete action (`Delete repo`) (`HRC001`). Fail: competing primary buttons or vague `Yes`/`No` labels.

![Clause 23](examples/core-principles.png)

## 24. Store unambiguous timestamps

Pass: store UTC or an explicit numeric offset and localize the display (`HRC201`, `HRC202`). Fail: expose raw ISO-8601 or unlocalized `UTC` timestamps in UI text.

![Clause 24](examples/utc-display.png)

## 25. Plan around the user goal

Pass: identify the goal, main flow, and states before visual polish, using realistic domain copy with zero `Lorem ipsum` (`HRC203`). Fail: unrouted chrome stuffed with placeholder copy.

![Clause 25](examples/user-flow-to-ui.png)

## 26. Dark surfaces

Pass: distinguish a raised surface with a subtle surface color while preserving text contrast. Fail: use a bright decorative border that dominates the content.

![Clause 26](examples/dark-surfaces.png)

## 27. Text over images

Pass: use a local treatment when needed and check the actual image. Fail: place light text on a light image without sufficient contrast.

![Clause 27](examples/image-overlay.png)

## 28. Balance and focus

Pass: make the primary action clear. Fail: give competing actions equal primary emphasis.

![Clause 28](examples/balance-and-focus.png)

## 29. Brand and role color

Pass: pair a role color with a clear status label and check contrast against its background. Fail: convey status through color alone.

![Clause 29](examples/brand-role-colors.png)

## 30. Text-button padding

Pass: start with `16 px` horizontal and `8 px` vertical padding on the 4 px scale. Fail: crowd the label with `4 px` horizontal padding.

![Clause 30](examples/button-padding.png)

## 31. Relevant imagery

Pass: use imagery that supports the content. Fail: use decoration without a task purpose.

![Clause 31](examples/relevant-imagery.png)

## 32. Check real heading text

Pass: inspect the real content in the selected typeface and loosen tracking when needed. Fail: tighten text until letters collide.

![Clause 32](examples/real-heading-tracking.png)

## 33. Inspect live overlay edges and surfaces

Pass: at all five house viewports, open the dialog and tooltip, verify visible headings, prose, and controls stay inside their clipping and viewport bounds, and paint a readable tooltip surface. Fail: inspect only the closed page, hide a heading under the top edge, or leave a transparent tooltip over mixed content. Reachable text in a scrollable modal body is not clipped.

![Clause 33: Dialog Overlay Clipping](examples/dialog-overlay-clipping.png)

## Recipe and Audit-Rule Examples

- **Offline bundled fonts (`recipes/offline-fonts.md`):**
  ![Offline Fonts](examples/offline-fonts.png)
- **Live dot (`recipes/live-dot.md`):**
  ![Live Dot](examples/live-dot.png)
- **Fixed-slot loading (`recipes/loading-bar.md`):**
  ![Loading Bar](examples/loading-bar.png)
- **Optimistic writes and page memory (`recipes/page-memory.md`):**
  ![Page Memory](examples/write-shows-its-row.png)
- **Modal `.close-button` and confirmation (`recipes/modal-manager.md`):**
  ![Modal Close Button](examples/modal-close-button.png)
  ![Modal Autofocus](examples/modal-autofocus.png)
  ![Modal Close States](examples/modal-close-states.png)
- **Range sliders (`recipes/range-sliders.md`):**
  ![Range Sliders](examples/range-sliders.png)
- **Relative timestamps (`recipes/relative-timestamps.md`):**
  ![Relative Timestamps](examples/relative-timestamps.png)
- **Scroll edge fade (`recipes/scroll-edge-fade.md`):**
  ![Scroll Edge Fade](examples/scroll-edge-fade.png)
- **Floating overlay scroll fade (`recipes/floating-overlay-scroll-fade.md`):**
  ![Floating Overlay Scroll Fade](examples/floating-overlay-scroll-fade.png)
- **Scrollbars (`recipes/scrollbars.md`):**
  ![Scrollbars](examples/scrollbars.png)
- **Overflow and anti-clipping (`recipes/overflow-and-clipping.md`):**
  ![Overflow and Clipping](examples/overflow-and-clipping.png)
- **Table to cards (`recipes/table-to-cards.md`):**
  ![Table to Cards](examples/table-to-cards.png)
- **Measure and density rules (`SPC001`–`HRC102`):**
  ![SPC001-SPC002-TYP001](examples/rule-spc001-typ001.png)
  ![SPC201-SPC203-HRC102](examples/rule-spc201-hrc102.png)
