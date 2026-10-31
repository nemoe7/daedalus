# Recipe: Floating overlay scroll fade

Extend the scroll container past a floating overlay composer or bottom action bar to `100%` of the container end, with a continuous bottom mask (`--edge-fade-end: 96px` -> `transparent 100%`) so scrolling content fades smoothly behind and past the solid composer card.

![Floating Overlay Scroll Fade Dark-Mode Example](../examples/floating-overlay-scroll-fade.png)

## Rules

- Extend the scroll region (`margin-bottom: -84px` on the scroll wrapper, `padding-bottom: 88px` on the scroll container) past the bottom composer or floating action bar so the scroll container reaches the bottom of the viewport.
- Set `--edge-fade-end: 96px` and `--edge-stop-end: 0px` on the scroll container so the bottom fade begins `~28px` above the top edge of the composer and continues smoothly behind the composer all the way to `transparent 100%` at the container end.
- Keep the floating composer surface solid (`background-color: hsl(var(--surface-secondary))`, `position: relative`, `z-index: 3`) with bottom breathing room (`margin-bottom: 18px`) so scrolled content never bleeds through the text input while remaining softly visible in the bottom gap when overflowing.
- Match the scroll container's `padding-bottom` (`88px`) to the overlap depth plus breathing room so the final item scrolls completely above the floating bar when scrolled to `100%`.

## Reference CSS

```css
.log-scroll {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  margin-bottom: -84px;
}

#history {
  --edge-fade-end: 96px;
  --edge-stop-end: 0px;
  padding-bottom: 88px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.card.compose {
  position: relative;
  z-index: 3;
  margin-bottom: 18px;
  background-color: hsl(var(--surface-secondary));
}
```
