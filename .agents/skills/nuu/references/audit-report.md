# Nuu Audit: <target>

- **Command:** `nuu audit -p <root> -u <url> -w 1280 360 -P house -r <ready-selector>`
- **Widths:** `1280` px (desktop), `360` px (mobile with `hasTouch` and `isMobile`)
- **Counts:** `<N>` automated findings, `<N>` visual findings, `<N>` house-style deviations
- **Status:** `<pass | action-needed>`

## Visual Captures

- **Desktop (`1280` px):** `![Desktop 1280px](<desktop-capture-or-board.png>)`
- **Mobile (`360` px):** `![Mobile 360px](<mobile-capture-or-board.png>)`

## Automated Findings

| Location | Rule / Check | Width | Finding | Fix |
| --- | --- | --- | --- | --- |
| `selector` or `path:line` | `NUA301` or `measure` | `1280` / `360` | Violation description | Repair applied or needed |

## Visual Judgment Findings

| Area | Width | Observation | Disposition | Action |
| --- | --- | --- | --- | --- |
| `<component>` | `360` | Visual layout or hierarchy issue | `fix` or `keep` | Adjustment or reason |

## House-Style Deviations

| Component / Property | Actual | Sheet | Reason |
| --- | --- | --- | --- |
| `<selector> (<property>)` | `<value>` | `<sheet-value>` | Intentional product or layout reason |

## Unrun Checks

- `<check>`: `<reason skipped and when to run>` (or `None` when all checks ran).
