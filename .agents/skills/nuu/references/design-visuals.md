# Visual-Reference Boards (`nuu board` + `design-visuals/`)

Composite visual-reference boards sit in `docs/design-visuals/` (or `-o <dir>`) and are generated from real browser captures (`nuu walk <walk.py>`) and `[paths].stylesheet` tokens via `nuu board -c nuu.toml -s boards.toml -o docs/design-visuals`. Neither `annotate.py` nor `boards.toml` hardcodes shell hex colors; every surface, border, text, and accent token comes from `[paths].stylesheet`.

## Preset Numbering (`-T 1..9, 0, canvas`)

| Board | `-T` / Alias | Default filename | Section in `docs/design.md` |
| --- | --- | --- | --- |
| Board 1 | `1` / `lay-of-the-land` | `design-visuals/overview-1.png` | `1. Pitch and Scope` (routed `orthogonal` / `scurve` flow arrows) |
| Board 2 | `2` / `deep-dive` | `design-visuals/<area>-2.png` | `5. Major Areas` (focused section/card wireframe and actions) |
| Board 3 | `3` / `example-views` | `design-visuals/<area>-3.png` | `5. Major Areas` (`Default`, `Empty`, `Error`, `Populated` views) |
| Board 4 | `4` / `component-states` | `design-visuals/<area>-4.png` | `5. Major Areas` (`default`, `hover`, `:focus-visible`, `active`, `disabled`, `loading`) |
| Board 5 | `5` / `global-components` | `design-visuals/components-5.png` | `4. Global Components` (dark + light holistic vs `In current use`) |
| Board 6 | `6` / `style-guide` | `design-visuals/styles-6.png` | `3. Foundations and Tokens` (roles, `9`-step ramp, spacing/radii, icons) |
| Board 7 | `7` / `anatomy` | `design-visuals/anatomy-7.png` | `4. Global Components` (`1..N` callout pins, slot table, `measure.py` redlines) |
| Board 8 | `8` / `responsive` | `design-visuals/responsive-8.png` | `5. Major Areas` (`5` house viewports + `68em/60em/30em/24em` tags) |
| Board 9 | `9` / `a11y-focus` | `design-visuals/a11y-9.png` | `6. Evidence, Deviations, and Decisions` (`Tab` trail, `2 px` focus rings, `44x44 px` touch, contrast) |
| Board 0 | `0` (`10`) / `panels` | `design-visuals/diff-0.png` | `6. Evidence` & PR reviews (`Before` vs `After` / `Pass` vs `Fail` grid) |
| Canvas | `canvas` / `custom` | `design-visuals/canvas.png` | Multi-artboard `x,y` sheet (`<= 16384 x 16384 px`) combining `1..0` frames and arrows |

## Multi-Board TOML Specification (`boards.toml` or `nuu.toml`)

```toml
[board.shots]
inbox = "nuu-artifacts/inbox.png"
empty = "nuu-artifacts/inbox-empty.png"
error = "nuu-artifacts/inbox-error.png"
inspector = "nuu-artifacts/inspector.png"

[[board.boards]]
name = "overview-1"
type = "lay-of-the-land"
title = "Ops Inbox Overview"
lede = "Triage queue to inspector resolution"
frames = [
  { id = "f1", shot = "inbox", label = "1. Inbox", sub = "1920x957", width = 640, cap = "Filtered queue" },
  { id = "f2", shot = "inspector", label = "2. Inspector", sub = "1920x957", width = 640, cap = "Resolve or delete" },
]
arrows = [
  { from = "f1", to = "f2", route = "orthogonal", label = "Select row", color = "blue" },
]

[[board.boards]]
name = "inbox-3"
type = "example-views"
title = "Inbox States"
frames = [
  { id = "s1", shot = "inbox", label = "Default" },
  { id = "s2", shot = "empty", label = "Empty" },
  { id = "s3", shot = "error", label = "Error" },
]
```

## Local Markdown Index Template (`docs/design-visuals/inbox.md`)

```markdown
# <Area> Visual Reference

Status: proposed | Captures: not captured | Audit: not run
Authority: [Design document](../design.md)

- **Overview (`Board 1`):** ![Overview](overview-1.png)
- **Deep dive (`Board 2`):** ![Deep dive](<area>-2.png)
- **States (`Board 3`):** ![Example views](<area>-3.png)
- **Control matrix (`Board 4`):** ![Component states](<area>-4.png)
- **Responsive (`Board 8`):** ![Five house viewports](responsive-8.png)
- **Accessibility (`Board 9`):** ![Keyboard focus and contrast](a11y-9.png)

| Image | Board preset | Source | License | Date | Verification |
| --- | --- | --- | --- | --- | --- |
| `<file>.png` | `<1..9, 0, canvas>` | `nuu walk` + `nuu board` | `<project license>` | `<YYYY-MM-DD>` | `<not run or audit pass>` |
```
