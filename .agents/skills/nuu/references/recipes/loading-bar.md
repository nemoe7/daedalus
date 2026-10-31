# Recipe: Loading bar and fixed-slot control loading

Render a subtle `2px` edge loading bar after a `200ms` delay with `aria-busy="true"` on the loading region, compositor-only `transform`/`opacity` motion, and a fixed `16x16 px` indicator slot (`8x8 px` `.dot` or `14x14 px` `.btn-spinner`) centered with the label inside inline controls so switching between idle and loading causes `0 px` width or height shift (`ACC205`, `ACC204`).

![Loading Bar Dark-Mode Example](../examples/loading-bar.png)

## Rules

- Keep a fixed `16x16 px` indicator slot (`8x8 px` `.dot` or `14x14 px` `.btn-spinner`) centered with the label inside controls so switching between idle and loading (`aria-busy="true"`) causes `0 px` width or height shift (`ACC205`, `ACC204`).
- Default to a subtle `2px` edge loading bar anchored along the bottom of the top bar or panel toolbar; use an in-flow loud `<progress>` bar only when requested.
- Delay visual appearance by `200ms` (`150–300ms`) so fast operations complete without flashing a transient indicator.
- Set `aria-busy="true"` on the region whose content is being updated (`section`, `article`, or `main`) while loading and reset to `aria-busy="false"` in `finally`; never put `role="progressbar"` on a decorative edge pseudo-element.
- Grow the bar from left to right with `transform-origin: left center` and `transform: scaleX(...)` on `var(--accent)`. Snap to `scaleX(1)` and fade `opacity` to `0` over `220ms` (`data-loading="done"`) when finished. Animate only compositor properties (`transform` and `opacity`), never `width`, `left`, or `margin`, so layout never reflows during loading.
- Under `@media (prefers-reduced-motion: reduce)`, replace growth animation with a static full-width subtle bar (`width: 100%`, `opacity: 0.45`, `animation: none`, `transform: none`).

## Subtle edge loading bar (default)

```css
.topbar {
  position: relative;
}

.topbar:has(~ main [data-loading="true"]:not([hidden]))::after {
  content: "";
  position: absolute;
  left: 0;
  bottom: -1px;
  width: 100%;
  height: 2px;
  background: var(--accent);
  opacity: 0;
  pointer-events: none;
  transform-origin: left center;
  transform: scaleX(0);
  animation: nuu-edge-loading 2.4s 200ms var(--ease) forwards;
}

.topbar:has(~ main [data-loading="done"]:not([hidden]))::after {
  content: "";
  position: absolute;
  left: 0;
  bottom: -1px;
  width: 100%;
  height: 2px;
  background: var(--accent);
  pointer-events: none;
  transform-origin: left center;
  animation: nuu-edge-loading-done 220ms var(--ease) forwards;
}

@keyframes nuu-edge-loading {
  0% {
    opacity: 0.92;
    transform: scaleX(0.05);
  }
  15% {
    opacity: 0.92;
    transform: scaleX(0.35);
  }
  45% {
    opacity: 0.92;
    transform: scaleX(0.65);
  }
  100% {
    opacity: 0.92;
    transform: scaleX(0.92);
  }
}

@keyframes nuu-edge-loading-done {
  0% {
    opacity: 0.92;
    transform: scaleX(0.92);
  }
  45% {
    opacity: 0.92;
    transform: scaleX(1);
  }
  100% {
    opacity: 0;
    transform: scaleX(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .topbar:has(~ main [data-loading="true"]:not([hidden]))::after {
    width: 100%;
    opacity: 0.45;
    animation: none;
    transform: none;
  }
}
```

```js
async function loadPanel(panel, task) {
  panel.dataset.loading = 'true';
  panel.setAttribute('aria-busy', 'true');
  try {
    return await task();
  } finally {
    panel.dataset.loading = 'done';
    panel.setAttribute('aria-busy', 'false');
    setTimeout(() => {
      if (panel.dataset.loading === 'done') {
        delete panel.dataset.loading;
      }
    }, 220);
  }
}
```

## Inline button or compact control loading (fixed `16x16 px` slot)

```css
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}

.btn .ico-slot {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  flex: none;
}

.btn[aria-busy="true"] .btn-spinner {
  width: 14px;
  height: 14px;
  animation: nuu-spin 0.8s linear infinite;
}

.btn[aria-busy="true"] .dot {
  width: 8px;
  height: 8px;
  border-radius: 9999px;
  background: currentColor;
  animation: pulse 1.4s var(--ease) infinite;
}

@keyframes nuu-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .btn[aria-busy="true"] .btn-spinner,
  .btn[aria-busy="true"] .dot {
    animation: none;
  }
}
```
