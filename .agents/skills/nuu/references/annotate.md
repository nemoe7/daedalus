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
| `--spec <json>` | `-s` | JSON file with `boxes`, `labels`, `arrows`, `callouts`, and optional `scale` |
| `--scale <1-6>` | `-S` | Integer font and badge scale factor (default `2`) |
| `--json` | `-j` | Emit machine-readable JSON output |
