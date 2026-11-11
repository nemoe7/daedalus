# `nuu annotate` and `nuu board` Reference (`scripts/annotate.py`)

`nuu annotate` renders bounding boxes, directional arrows, pill labels, numbered callout pins, and crops onto a saved PNG (`-i`) or live URL (`-u`) in dark mode without overwriting `--input`. `nuu board` renders multi-panel comparison grids and composite design boards (`1..9, 0, 10, canvas`) styled directly from `[paths].stylesheet` in `nuu.toml`.

## `nuu annotate` Flags

| Flag | Shorthand | Default | Description |
| --- | --- | --- | --- |
| `--input <png>` | `-i` | — | Existing source PNG screenshot path |
| `--url <http-url>` | `-u` | — | Live URL to open in dark mode at `--viewport` |
| `--selector <css>` | `-s` | — | Repeatable shorthand drawing a `red` box around `<css>` in live (`-u`) mode |
| `--output <png>` | `-o` | required | Destination PNG path (never overwrites `--input`) |
| `--config <toml>` | `-c` | `./nuu.toml` | Configuration file (`[paths]`, `[fonts]`, `[annotate]`, `[audit].viewports`) |
| `--box <spec>` | `-b` | — | PNG (`-i`): `x,y,w,h[,color]`. Live (`-u`): `<css>[,color]` (repeatable) |
| `--label <spec>` | `-l` | — | PNG (`-i`): `x,y,text[,color]`. Live (`-u`): `<css>,text[,color]` (`8 px` above top-left, repeatable) |
| `--arrow <spec>` | `-a` | — | PNG (`-i`): `x1,y1,x2,y2[,color]`. Live (`-u`): `<from_css>,<to_css>[,color]` (repeatable) |
| `--callout <spec>` | `-n` | — | PNG (`-i`): `x,y,num[,label[,color]]`. Live (`-u`): `<css>,num[,label[,color]]` (repeatable) |
| `--crop <spec>` | `-C` | `[annotate].crop` (`"none"`) | `x,y,w,h` \| `<css>[,pad]` (live `-u`) \| `auto` \| `pad` \| `none` |
| `--scale <1..6>` | `-S` | `[annotate].scale` (`2`) | Integer label, stroke, and callout scale factor |
| `--viewport <WxH>` | `-v` | `[audit].viewports[0]` (`1920x957`) | Live browser viewport `<width>x<height>` |
| `--executable <bin>` | `-x` | `$CHROMIUM_BIN` | Explicit Chromium executable path |

Named `<color>` tokens: `red` (`#e11d48`, default), `blue` (`#2563eb`), `green` (`#16a34a`), `amber` (`#d97706`), `white` (`#ffffff`), `black` (`#111827`), or `#rgb`/`#rrggbb` hex.

## `nuu board` Presets and Flags

| `-T, --type` | Alias | Purpose |
| --- | --- | --- |
| `1` | `lay-of-the-land` | Overview wireframe of major areas connected by routed `orthogonal`, `scurve`, or `straight` arrows |
| `2` | `deep-dive` | Focused section or card wireframe showing stacked slots and primary/secondary actions |
| `3` | `example-views` | Side-by-side product states (`Default`, `Empty`, `Error`, `Populated`) |
| `4` | `component-states` | Horizontal control state matrix (`default`, `hover`, `:focus-visible`, `active`, `disabled`, `loading`) |
| `5` | `global-components` | Dark + light holistic component sheet plus an `In current use` subset |
| `6` | `style-guide` | Role color swatches, `9`-step type ramp, spacing/radii, and icon grid |
| `7` | `anatomy` | Numbered callout pins (`1..N`), slot/token table, and contrast-checked spacing redlines |
| `8` | `responsive` | `5`-viewport reflow strip (`1920x957`, `1080x2437`, `958x958`, `855x325`, `411x799`) with `68em/60em/30em/24em` tags |
| `9` | `a11y-focus` | Sequential keyboard `Tab` trail (`1..N`), `:focus-visible` `2 px` rings, `44x44 px` touch boxes, and contrast ratios |
| `0` / `10` | `panels` | Multi-panel `Before` vs `After` (`Pass` vs `Fail`) comparison grid (`-p [Label=]src[@x,y,w,h]`, `-C`, `-n`) |
| `canvas` | `custom` | Multi-artboard `x,y` canvas (`<= 16384 x 16384 px`) combining `1..0` frames, tables, and cross-frame arrows |

Set `$NUU_RENDERER=bitmap` to force pure-Python PNG rendering in `annotate -i` and `board -T panels` when Chromium is unavailable. Board specs use TOML; `[icons].file` and `table` select an HTML or JavaScript icon table. JSON icon tables are not accepted.

## `nuu.toml` v2 Configuration

```toml
[paths]
doc = "docs/design.md"
stylesheet = "references/style.css"

[fonts]
prefer_project_css = true
sans = ""
mono = ""
files = []

[annotate]
scale = 2
crop = "none"

[board]
type = "panels"
columns = 2
scale = 2
wireframe = false

[board.shots]
cart = "nuu-artifacts/cart.png"
pay = "nuu-artifacts/pay.png"

[[board.boards]]
name = "overview-1"
type = "lay-of-the-land"
title = "Checkout Overview"
lede = "Cart to receipt flow"
frames = [
  { id = "f1", shot = "cart", label = "1. Cart", sub = "1920x957", width = 640, cap = "Default state" },
  { id = "f2", shot = "pay", label = "2. Pay", sub = "1920x957", width = 640, cap = "Confirm order" },
]
arrows = [
  { from = "f1", to = "f2", route = "orthogonal", label = "Continue", color = "blue" },
]
```
