# Recipe: Loading bar — Render a subtle 2px edge loading bar after a 200ms delay with `aria-busy="true"` on the loading region, compositor-only `transform`/`opacity` motion, and a static `prefers-reduced-motion` fallback; use a loud bar only when requested.

## Rules

- Default to a subtle `2px` edge loading bar anchored along the bottom of the top bar or panel toolbar; use an in-flow loud `<progress>` bar only when requested.
- Delay visual appearance by `200ms` (`150–300ms`) so fast operations complete without flashing a transient indicator.
- Set `aria-busy="true"` on the region whose content is being updated (`section`, `article`, or `main`) while loading and reset to `aria-busy="false"` in `finally`; never put `role="progressbar"` on a decorative edge pseudo-element.
- Animate only compositor properties (`transform` and `opacity`), never `width`, `left`, or `margin`, so layout never reflows during loading.
- Under `@media (prefers-reduced-motion: reduce)`, replace the looping sweep with a static full-width subtle bar (`width: 100%; opacity: 0.45; animation: none;`).

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
  width: 36%;
  height: 2px;
  background: var(--focus);
  opacity: 0;
  pointer-events: none;
  animation: nuu-edge-loading 1.2s 200ms ease-in-out infinite;
}

@keyframes nuu-edge-loading {
  0% {
    opacity: 0.35;
    transform: translateX(0) scaleX(0.4);
  }
  50% {
    opacity: 0.85;
    transform: translateX(90%) scaleX(1);
  }
  100% {
    opacity: 0.35;
    transform: translateX(180%) scaleX(0.4);
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
    delete panel.dataset.loading;
    panel.setAttribute('aria-busy', 'false');
  }
}
```

## Loud loading bar (opt-in on request only)

- Use a native `<progress class="loading-bar-loud">` element inside the active card or dialog when the user explicitly asks for a prominent progress bar or when a multi-step job reports determinate progress (`value` and `max`).
- Pair determinate progress with an accessible label (`aria-label` or `<label>`) and update `value` at coarse milestones so assistive technology is not flooded.
