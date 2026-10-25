# Recipe: Loading bar — Render a subtle 2px edge loading bar after a 200ms delay with `aria-busy="true"` on the loading region, compositor-only `transform`/`opacity` motion, a fixed-slot spinner or pulsing dot for inline controls awaiting a fetch, and a static `prefers-reduced-motion` fallback; use a loud bar only when requested.

## Rules

- Default to a subtle `2px` edge loading bar anchored along the bottom of the top bar or panel toolbar; for a button or compact control awaiting a fetch, render either a `16px` compositor-rotated spinner or a `6px` pulsing dot inside the existing glyph slot with `aria-busy="true"` and locked dimensions (`NUA309`, `NUL005`); use an in-flow loud `<progress>` bar only when requested.
- Delay visual appearance by `200ms` (`150–300ms`) so fast operations complete without flashing a transient indicator.
- Set `aria-busy="true"` on the region whose content is being updated (`section`, `article`, or `main`) while loading and reset to `aria-busy="false"` in `finally`; never put `role="progressbar"` on a decorative edge pseudo-element.
- Grow the bar from left to right with `transform-origin: left center` and `transform: scaleX(...)` instead of a pulsing sweep. Animate only compositor properties (`transform` and `opacity`), never `width`, `left`, or `margin`, so layout never reflows during loading.
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
  background: var(--focus);
  opacity: 0;
  pointer-events: none;
  transform-origin: left center;
  transform: scaleX(0);
  animation: nuu-edge-loading 1.6s 200ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes nuu-edge-loading {
  0% {
    opacity: 0.85;
    transform: scaleX(0.05);
  }
  40% {
    opacity: 0.85;
    transform: scaleX(0.55);
  }
  100% {
    opacity: 0.85;
    transform: scaleX(0.92);
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

## Inline button or compact control loading (spinner or pulsing dot)

```css
.btn .ico-slot {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  flex: none;
}

.btn[aria-busy="true"] .spinner {
  width: 16px;
  height: 16px;
  animation: nuu-spin 0.8s linear infinite;
}

.btn[aria-busy="true"] .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes nuu-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .btn[aria-busy="true"] .spinner,
  .btn[aria-busy="true"] .dot {
    animation: none;
  }
}
```

```js
async function runButtonFetch(button, task) {
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    return await task();
  } finally {
    button.setAttribute('aria-busy', 'false');
    button.disabled = false;
  }
}
```

## Loud loading bar (opt-in on request only)

- Use a native `<progress class="loading-bar-loud">` element inside the active card or dialog when the user explicitly asks for a prominent progress bar or when a multi-step job reports determinate progress (`value` and `max`).
- Pair determinate progress with an accessible label (`aria-label` or `<label>`) and update `value` at coarse milestones so assistive technology is not flooded.
