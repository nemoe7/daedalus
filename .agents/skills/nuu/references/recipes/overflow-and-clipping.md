# Recipe: Overflow and anti-clipping

Prevent non-scrolling container clipping (`NUM209`), unintended sibling box overlap (`NUM210`), and clipped focus rings, shadows, or tooltips (`NUA308`).

![Overflow and Anti-Clipping Dark-Mode Example](../examples/overflow-and-clipping.png)

## Rules

- Do not set `overflow: hidden` or `overflow: clip` on non-scrolling wrappers, toolbars, or cards unless clipping is required; let flex and grid children wrap (`flex-wrap: wrap`, `min-width: 0`) instead of clipping (`NUM209`).
- Keep sibling controls and cards in normal flex or grid flow so bounding boxes never collide across the house viewports (`1920x957`, `1080x2437`, `958x958`, `411x799`, and `855x325`) (`NUM210`).
- Reserve inner container padding or `scroll-padding` of at least `outline-width + outline-offset` (`4 px` for a `2 px` ring with `2 px` offset) on every side so an ancestor `overflow` or `mask-image` boundary never shaves the top, bottom, left, or right of a `:focus-visible` ring (`NUA308`).
- Never apply a top-edge `mask-image` fade across a container that holds a `position: sticky; top: 0` toolbar; mask only the scrolling list below the sticky bar or use a bottom-only mask on the outer panel.
- Render tooltips in the top layer or with `position: fixed` clamped to viewport bounds (`8px` inset) so ancestor scroll and `overflow` containers never clip them (`NUA308`).

```css
/* Reserve room for 2px solid focus rings + 2px offset inside clipped or masked containers */
.toolbar-row,
.scroll-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1);
  scroll-padding: var(--space-1);
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
