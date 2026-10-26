# Recipe: Live dot

Use an `8x8 px` pulsing dot (`.dot`, with `.dot-sm` `6 px` and `.dot-lg` `10 px`) for live indicators (`NUA309`, `NUL005`). The dot sits in the `16x16 px` slot the idle glyph uses, so a state change moves nothing, and the row text names the state since the dot is `aria-hidden`.

![Live Dot Dark-Mode Example](../examples/live-dot.png)

```css
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.dot-sm { width: 6px; height: 6px; }
.dot-lg { width: 10px; height: 10px; }
.ico-svg { display: inline-grid; place-items: center; width: 16px; height: 16px; flex: none; }
.ico-svg > .dot, .dot.live, .pill.warn .dot { animation: pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: .35; transform: scale(.85); } 50% { opacity: 1; transform: scale(1); } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
}
```

```js
// A live state takes the 16x16 px glyph slot of a status row so it keeps the row height
const liveDot = () => el("span", { class: "ico-svg", "aria-hidden": "true" }, el("i", { class: "dot" }));
```
