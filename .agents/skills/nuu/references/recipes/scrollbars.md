# Recipe: Scrollbars

Style scrollbars with a thin `10px` rounded thumb (`#302e2c` in dark mode or `var(--scrollbar-thumb)`) on the surface background track (`var(--bg)`), `8px` thumb radius, and no top or bottom arrow buttons (`::-webkit-scrollbar-button { display: none; }`). Scope `scrollbar-width: thin` and `scrollbar-color` inside `@supports not selector(::-webkit-scrollbar)` so Chromium keeps the rounded WebKit thumb and hidden buttons while Firefox receives a matching thin track and thumb.

```css
:root {
  --scrollbar-thumb: #c8c8d0;
}

@media (prefers-color-scheme: dark) {
  :root {
    --scrollbar-thumb: #302e2c;
  }
}

:root[data-theme="light"] {
  --scrollbar-thumb: #c8c8d0;
}

:root[data-theme="dark"] {
  --scrollbar-thumb: #302e2c;
}

::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-track,
::-webkit-scrollbar-corner {
  background: var(--bg);
}

::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: 8px;
}

::-webkit-scrollbar-button {
  display: none;
  width: 0;
  height: 0;
}

@supports not selector(::-webkit-scrollbar) {
  * {
    scrollbar-width: thin;
    scrollbar-color: var(--scrollbar-thumb) var(--bg);
  }
}
```
