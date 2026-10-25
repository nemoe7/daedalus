# Recipe: Live dot

One amber dot means one live state, and every amber dot pulses (`NUA309`, `NUL005`). The same 1.4 s pulse drives the page LED. The dot sits in the slot the idle glyph uses, so a state change moves nothing. The row text names the state, since the dot is `aria-hidden`.

```css
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.ico-svg > .dot { margin: auto; }
.ico-svg > .dot, .dot.live, .pill.warn .dot { animation: pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: .35; transform: scale(.85); } 50% { opacity: 1; transform: scale(1); } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
}
```

```js
// a live state takes the glyph slot of a status row, so it keeps the row height
const liveDot = () => el("span", { class: "ico-svg", "aria-hidden": "true" }, el("i", { class: "dot" }));
```
