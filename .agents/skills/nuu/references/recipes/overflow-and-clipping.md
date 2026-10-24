# Recipe: Overflow and anti-clipping

Prevent non-scrolling container clipping (`NUM209`), unintended sibling box overlap (`NUM210`), and clipped focus rings, shadows, or tooltips (`NUA308`).

## Rules

- Do not set `overflow: hidden` or `overflow: clip` on non-scrolling wrappers, toolbars, or cards unless clipping is required; let flex and grid children wrap (`flex-wrap: wrap`, `min-width: 0`) instead of clipping (`NUM209`).
- Keep sibling controls and cards in normal flex or grid flow so bounding boxes never collide at `1280` or `360` px (`NUM210`).
- Keep `:focus-visible` at `outline-offset: 0` or reserve inner container padding at least `outline-width + outline-offset` on every side so an ancestor `overflow` or `mask-image` boundary never shaves the focus ring (`NUA308`).
- Never apply a top-edge `mask-image` fade across a container that holds a `position: sticky; top: 0` toolbar; mask only the scrolling list below the sticky bar or use a bottom-only mask on the outer panel.
- Render tooltips in the top layer or with `position: fixed` clamped to viewport bounds (`8px` inset) so ancestor scroll and `overflow` containers never clip them (`NUA308`).

```css
/* Reserve room for focus rings inside clipped or masked containers */
.toolbar-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1);
  padding: 2px;
  min-width: 0;
}

/* Fixed viewport-clamped tooltip that escapes ancestor overflow clipping */
[role="tooltip"] {
  position: fixed;
  z-index: 30;
  max-width: min(320px, calc(100vw - 16px));
  pointer-events: none;
}
```
