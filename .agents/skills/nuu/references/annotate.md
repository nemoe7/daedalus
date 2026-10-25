# `annotate.py` Reference

`scripts/annotate.py` (or `nuu annotate`) renders bounding boxes, directional arrows, text labels, and numbered callout badges onto a PNG screenshot without overwriting the source image.

| Flag | Shorthand | Description |
| --- | --- | --- |
| `--input <png>` | `-i` | Existing source PNG screenshot path |
| `--output <png>` | `-o` | New destination PNG path (must not exist or match `--input`) |
| `--box <x,y,w,h[,color]>` | `-b` | Bounding box rectangle in image pixels (repeatable) |
| `--label <x,y,text[,color]>` | `-l` | Text banner at `x,y` (repeatable) |
| `--arrow <x1,y1,x2,y2[,color]>` | `-a` | Directional arrow from `x1,y1` to `x2,y2` (repeatable) |
| `--callout <x,y,n[,label[,color]]>` | `-c` | Numbered badge at `x,y` with optional label (repeatable) |
| `--crop <x,y,w,h>` | `-C` | Crop the annotated output PNG to `x,y,w,h` in image pixels |
| `--spec <json>` | `-s` | JSON file with `boxes`, `labels`, `arrows`, `callouts`, `crop`, and optional `scale` |
| `--scale <1-6>` | `-S` | Integer font and badge scale factor (default `2`) |
| `--json` | `-j` | Emit machine-readable JSON output |

## `nuu.toml` Configuration

`annotate`, `board`, `doctor`, and browser wireframe captures auto-discover `nuu.toml` in `--project` (or via `NUU_CONFIG`):

```toml
[fonts]
sans = '"Basel Grotesk", system-ui, sans-serif'
mono = '"Basel Grotesk Mono", ui-monospace, monospace'
files = ["assets/Basel_Grotesk_Regular.woff2"]

[annotate]
scale = 2
renderer = "auto"

[board]
columns = 2
scale = 2
wireframe = false

[audit]
widths = [360, 768, 1280]
wireframe = false
```

