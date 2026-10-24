# Recipe: Floating-overlay scroll fade

Stretch a scroll container across the viewport width and overlap its edge behind an opaque floating bar such as a composer card. For standalone scroll containers without floating overlays, use `references/recipes/scroll-edge-fade.md`.

- Set `width: 100vw` and negative horizontal and bottom margins on the scroll wrapper so the scrollbar hugs the right viewport edge.
- Never pull a scroll container behind header text that has no background; keep `margin-top: 0` and a `12px` top fade on unbacked edges.
- Center message rows with horizontal padding (`max(5vw, calc((100% - 900px) / 2))`) and bottom padding matching the composer overlap.
- Give the floating composer `position: relative; z-index: 2;` and use `--edge-fade-end: 36px` on the scroller.

```css
:root { --edge-fade: 12px; --edge-ramp: 192px; }
@property --edge-start { syntax: "<length>"; inherits: false; initial-value: 0px; }
@property --edge-end { syntax: "<length>"; inherits: false; initial-value: 0px; }
.floating-composer {
  position: relative;
  z-index: 2;
}
.scroll-wrapper {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  width: 100vw;
  margin: 0 calc(50% - 50vw) -36px;
}
.scroll-log {
  --edge-fade-end: 36px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 12px max(5vw, calc((100% - 900px) / 2)) 40px;
}
@media (prefers-reduced-motion: no-preference) {
  @supports (animation-timeline: scroll(self)) {
    .scroll-log {
      mask-image: linear-gradient(to bottom, transparent, black var(--edge-start), black calc(100% - var(--edge-end)), transparent);
      animation: edge-start linear both, edge-end linear both;
      animation-timeline: scroll(self), scroll(self);
      animation-range: 0px var(--edge-ramp), calc(100% - var(--edge-ramp)) 100%;
    }
    @keyframes edge-start { from { --edge-start: 0px; } to { --edge-start: var(--edge-fade); } }
    @keyframes edge-end { from { --edge-end: var(--edge-fade-end, var(--edge-fade)); } to { --edge-end: 0px; } }
  }
}
```
