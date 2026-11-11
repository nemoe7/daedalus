# UI Audit Report Contract (`nuu audit`)

`nuu audit` emits a `7`-section Markdown report on `stdout` (or `-o <report.md>`) and writes viewport PNG captures into `-d` (`./nuu-artifacts` default). Without `-u`, `nuu audit` runs static checks, lists skipped browser checks under `## Unrun Checks`, and exits `3` when static `"error"` count is `0` (`1` when static `"error"` findings exist).

## CLI Output Template (`7` Sections)

```markdown
# UI Audit

## Status
findings (exit 1)

## Counts
- Blocking errors: 2
- Review findings: 1
- Suppressions (`nonuu:`): 0
- Deviations (`nuu-deviation:`): 1
- Recipes (`nuurecipe:`): 2

## Unrun Checks
- None (static + 5 house viewports checked)

## Automated Findings
1. `SPC002` (error) `1920x957 dark`: `.card` has `border-radius: 12px` (expected `0, 4, 6, 10, 9999px`).

## Visual Judgments
- [Agent: inspect captures and fill Clause 20-32 visual judgments or write `None`.]

## Deviations
- `docs/design.md:14`: `nuu-deviation: Brand primary #ff5500`

## Captures
- `1920x957-dark-no-preference.png`
- `411x799-dark-no-preference.png`
```

## PR and Screenshot Review Boards

When presenting open findings or Clause 20–32 visual judgments in a pull request or human review report, pair each open item with a side-by-side (`columns = 2`) `Before` vs `After` `nuu board` image cropped (`-C`) and annotated (`nuu annotate`) around the affected component:

| Location | Width | Before | After |
| --- | --- | --- | --- |
| `selector` or `path:line` | `1920x957` / `411x799` | Violation description | Repair applied or needed |

![Finding 1 Before vs After](<finding-1-before-after-board.png> =720x)

| Area | Before | After | Action / Disposition |
| --- | --- | --- | --- |
| `<component>` | Visual layout or hierarchy issue | Replacement visual state | `fix`: adjustment / `keep`: reason |

![Visual Judgment 1 Before vs After](<visual-1-before-after-board.png> =720x)

## Captures

Omit this section in human review summaries unless full-viewport captures are needed. Default `report = true` house viewports in `nuu.toml`:

- **Desktop maximized (`1920x957`):** dark, light, and `reduced-motion` captures at the desktop maximized viewport.
- **Phone portrait (`411x799`):** dark, light, and `reduced-motion` captures with `touch = true` and `mobile = true`.
