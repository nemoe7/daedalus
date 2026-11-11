# Measure a Page (`nuu measure`)

`nuu measure` (`scripts/measure.py`) renders live DevTools box-model tints, Figma distance guides, and PowerToys pixel drop-rays onto a dark-mode PNG and prints a Markdown measurement and conformance table on `stdout`. Sheet, density, and overlap checks across all five house viewports run unconditionally inside `nuu audit -u <url>` (replacing the retired `check`, `density`, `ruler`, and `--sheet` surfaces).

## Flags and Defaults

| Flag | Shorthand | Default | Description |
| --- | --- | --- | --- |
| `--url <http-url>` | `-u` | — | Live page URL to open in dark mode at `--viewport` |
| `--input <png>` | `-i` | — | Existing PNG screenshot for offline pixel drop-rays |
| `--output <png>` | `-o` | required | Output redline PNG path (never overwrites `--input`) |
| `--config <toml>` | `-c` | `./nuu.toml` | Configuration file (`[paths].stylesheet`, `[measure]`, `[audit].viewports`) |
| `--selector <css>` | `-s` | — | Target CSS selector for live box-model tint and computed-style inspection |
| `--drop <x,y[,xy\|x\|y]>` | `-d` | — | Repeatable pixel drop-ray seed on `-u` or `-i` (`xy` default) |
| `--tolerance <0..255>` | `-T` | `[measure].tolerance` (`30`) | Per-channel RGBA delta threshold stopping outward `-d` rays |
| `--to <css\|x,y>` | — | parent edges | Pair selector or `x,y` coordinate for Figma distance guides (or ruler endpoint with `--from`) |
| `--from <x,y>` | — | — | Starting `x,y` pixel coordinate when checking a raw coordinate span with `--to x,y` |
| `--scale <4\|8>` | `-k` | `[measure].scale` (`4`) | Spacing grid step in pixels (`4` or `8`) |
| `--viewport <WxH>` | `-v` | `[audit].viewports[0]` (`1920x957`) | Browser viewport `<width>x<height>`, inheriting `mobile` and `touch` from matching `[audit].viewports` |
| `--executable <bin>` | `-x` | `$CHROMIUM_BIN` | Explicit Chromium executable path |

## Live Selector Box Tints and Figma Guides

Open a live URL, settle fonts and network activity, tint the target element's box model (`content` `rgba(111,168,220,0.45)`, `padding` `rgba(147,196,125,0.45)`, `border` `rgba(255,217,102,0.55)`, `margin` `rgba(246,178,107,0.45)`), and draw distance guides to `--to` (or to the parent edges when `--to` is omitted):

```bash
nuu measure -c nuu.toml -u http://127.0.0.1:8000 -s ".card" --to ".button" -o nuu-artifacts/card-measure.png
```

Computed padding, margin, gap, height, border radius, font size, line height, and letter spacing are checked against `-k` and `[paths].stylesheet` in `nuu.toml`.

## PowerToys Pixel Drop-Rays (`-d`)

Cast `1 px` rays left/right (`x`), up/down (`y`), or both (`xy`) from `(x, y)` on a live capture (`-u`) or saved PNG (`-i`) until `max(|dr|, |dg|, |db|, |da|) > tolerance` (`30`):

```bash
nuu measure -i nuu-artifacts/1920x957-dark-no-preference.png -d 167,122 -d 420,88,y -T 30 -o nuu-artifacts/drop-rays.png
```

Each ray renders a `1 px` shaft with `7 px` perpendicular end-caps (`2 px` stroke), a `W x H px` pill badge, and an `SPC001` finding whenever a measured span lands off the selected scale.

## Redline Contrast Fallback

`measure.py` reads optional `[measure].redline` from `nuu.toml` (`""` defaults to `#ff3366`). Whenever pixel contrast along any ray or guide drops below `3.0:1`, ink automatically falls back through `([measure].redline or #ff3366) -> #00e5ff -> #111827` with a `1 px` contrasting halo so redlines stay legible over coral, rose, and dark surfaces alike.
