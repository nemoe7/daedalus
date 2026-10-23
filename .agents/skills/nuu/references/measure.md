# Measure a page

Read computed values, compare them with the sheet, and keep the numbers. Two widths are the minimum: 1280 px and 360 px.

## Read the values

Open the page at the width, then run this in the browser console. It prints one JSON report for the selectors named.

```js
const selectors = ['.page', '.row', '.button', '.title'];
const props = ['height', 'padding-top', 'padding-left', 'margin-bottom', 'gap', 'font-size', 'border-radius'];
console.log(JSON.stringify({
  width: innerWidth,
  elements: selectors.map(selector => {
    const element = document.querySelector(selector);
    if (!element) return { selector, missing: true };
    const style = getComputedStyle(element);
    return { selector, values: Object.fromEntries(props.map(name => [name, style[name]])) };
  }),
}));
```

Save the output as `report.json`, one file per width.

## Check it

```bash
python scripts/measure.py check report.json
```

A spacing value must sit on the 8 px scale. Another value may also take a size the sheet declares, such as the 10 px radius. Add `--scale 4` for a denser sheet, and `--sheet <path>` to read another sheet.

## Read a gap with the ruler

The ruler takes two guides in page pixels: `x,y` from the top-left corner. It prints the gap on each axis, the distance, and whether the gap sits on the scale.

```bash
python scripts/measure.py ruler --from 120,340 --to 264,340
```

Place the guides on the edges you care about: the two sides of a gap, a row's top and bottom, a glyph's box. A gap that lands off the scale is a finding, not a rounding error.

## What to keep

- The two report files and the two screenshots.
- The list of values that changed, with its reason, in `docs/design.md` under Change log.
