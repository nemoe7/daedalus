# Nuu Audit: <target>

- **Status:** `<pass | action-needed>`
- **Counts:** `<N>` automated findings, `<N>` visual findings, `<N>` house-style deviations
- **Unrun Checks:** `<check>`: `<reason skipped and when to run>` (omit line when none)

## Captures (Dark Theme, Light on Request Only)

- **Breakpoints (`1280` px, `960` px, `640` px):** dark-themed captures at each house breakpoint
- **Mobile (`360` px):** dark-themed capture with `hasTouch` and `isMobile`

## Automated Findings

| Location | Rule / Check | Width | Finding | Fix |
| --- | --- | --- | --- | --- |
| `selector` or `path:line` | `NUA301` or `measure` | `1280` / `360` | Violation description | Repair applied or needed |

## Visual Judgments

| Area | Observation | Disposition | Before | After | Action |
| --- | --- | --- | --- | --- | --- |
| `<component>` | Visual layout or hierarchy issue | `fix` or `keep` | Before | After | Adjustment or reason |

## Deviations

| Component / Property | Before | After | Reason |
| --- | --- | --- | --- |
| `<selector> (<property>)` | Before | After | Intentional product or layout reason |
