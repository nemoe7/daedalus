# Recipe: Range sliders

Pair every `<input type="range">` with visible `min` and `max` endpoint labels, major-division tick marks capped at 11 visible ticks, and a live `<output>` value bubble synchronized on `input`. Keep `step` for value selection.

![Range Sliders Dark-Mode Example](../examples/range-sliders.png)

## Rules

- Declare explicit `min`, `max`, and `step` attributes on `<input type="range">`. Render at most 11 major-division ticks, keep `step` for value selection, and preserve `min`/`max` endpoint labels (`<datalist>` or `.range-ticks`).
- Render tabular `min` and `max` endpoint labels and a live `<output for="...">` value bubble that updates on `input` without shifting adjacent controls.
- Keep the thumb hit target at least `24x24 px` with a `2 px` solid `var(--focus)` outline and `2 px` offset on `:focus-visible` (`ACC202`, `ACC203`).

## Markup and styles

```html
<div class="range-field">
  <div class="range-header">
    <label for="concurrency-range">Concurrency limit</label>
    <output id="concurrency-out" for="concurrency-range" class="id">6</output>
  </div>
  <div class="range-track-wrap">
    <input
      id="concurrency-range"
      type="range"
      min="0"
      max="10"
      step="1"
      value="6"
      list="concurrency-ticks"
    />
    <datalist id="concurrency-ticks">
      <option value="0" label="0"></option>
      <option value="1"></option>
      <option value="2"></option>
      <option value="3"></option>
      <option value="4"></option>
      <option value="5"></option>
      <option value="6"></option>
      <option value="7"></option>
      <option value="8"></option>
      <option value="9"></option>
      <option value="10" label="10"></option>
    </datalist>
  </div>
  <div class="range-endpoints id" aria-hidden="true">
    <span>0</span>
    <span>10</span>
  </div>
</div>
```

```css
.range-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.range-header,
.range-endpoints {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--size-12);
}

.range-track-wrap input[type="range"] {
  width: 100%;
  min-height: 24px;
  margin: 0;
  accent-color: var(--accent);
}
```

```js
export function bindRangeOutput(input, output) {
  const sync = () => {
    const min = Number(input.min || 0);
    const max = Number(input.max || 100);
    const val = Number(input.value || 0);
    const pct = max > min ? ((val - min) / (max - min)) * 100 : 0;
    output.value = String(val);
    output.style.setProperty('--range-pct', `${pct}%`);
  };
  input.addEventListener('input', sync);
  sync();
}
```
