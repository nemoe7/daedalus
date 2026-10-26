# Recipe: Scrollbars

Style scrollbars with a thin `10px` rounded thumb (`#302e2c`) on the surface track (`var(--bg)`), `8px` radius, and no arrow buttons (`::-webkit-scrollbar-button { display: none; }`). Omit hover highlights unless keyboard navigation is active (`:root[data-keyboard-nav="true"]`). Scope `scrollbar-width: thin` and `scrollbar-color` inside `@supports not selector(::-webkit-scrollbar)` so Chromium keeps the rounded WebKit thumb and hidden buttons while Firefox receives a matching thin track and thumb.

![Scrollbars Dark-Mode Example](../examples/scrollbars.png)

```css
:root {
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

:root[data-keyboard-nav="true"] ::-webkit-scrollbar-thumb:hover,
:root[data-keyboard-nav="true"] ::-webkit-scrollbar-thumb:active {
  background: var(--focus);
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
