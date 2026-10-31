# Measure a page

Read computed values, compare them with the sheet, and keep the numbers. Check the house widths (`1920`, `1080`, `958`, `411`, and `855 px`).

| Subcommand | Purpose | Arguments and Shorthands |
| --- | --- | --- |
| `check` | Compare computed-style report JSON with the sheet | `<report>`, `-s, --sheet <path>`, `-k, --scale <px>`, `-d, --density` |
| `density` | Check row/card ink ratio, sibling gaps, and form inputs that leave row space unused | `<report>` |
| `ruler` | Measure pixel gap between two `x,y` guides | `-f, --from <x,y>`, `-t, --to <x,y>`, `-s, --sheet <path>`, `-k, --scale <px>` |

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

`TYP101` and `TYP201` are review findings by default. Configure them under `[checks.severity]` in `nuu.toml` as `"error"` to make them blocking.

A spacing value sits on the `4 px` scale by default. Another value may also take a size the sheet declares, such as the `6 px` radius. Add `--scale 8` for the `8 px` sheet, and `--sheet <path>` to read another sheet.

## Read a gap with the ruler

The ruler takes two guides in page pixels: `x,y` from the top-left corner. It prints the gap on each axis, the distance, and whether the gap sits on the scale.

```bash
python scripts/measure.py ruler --from 120,340 --to 264,340
```

Place the guides at the edges of a gap, row, or glyph box. A gap that lands off the scale is a finding, not a rounding error.

## What to keep

- The two report files and the two screenshots.
- The list of values that changed, with its reason, in `docs/design.md` under Change log.
