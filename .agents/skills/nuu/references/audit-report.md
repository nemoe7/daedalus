# Nuu Audit: <target>

- **Status:** `<pass | action-needed>`
- **Counts:** `<N>` automated findings, `<N>` visual findings, `<N>` house-style deviations
- **Unrun Checks:** `<check>`: `<reason skipped and when to run>` (omit line when none)

## Automated Findings

### 1. `<Rule ID>` — `<Rule Statement>`

| Location | Rule ID & Statement | Width | Finding | Fix |
| --- | --- | --- | --- | --- |
| `selector` or `path:line` | `NUA301`: `<rule statement>` | `1280` / `360` | Violation description | Repair applied or needed |

![Finding 1](<cropped-annotated-finding-1.png> =480x)

## Visual Judgments

### 1. `<Component>` — `<Visual Observation>`

| Area | Observation | Disposition | Before | After | Action |
| --- | --- | --- | --- | --- | --- |
| `<component>` | Visual layout or hierarchy issue | `fix` or `keep` | Before | After | Adjustment or reason |

![Visual Judgment 1](<cropped-annotated-visual-1.png> =480x)

## Deviations

| Component, Property, or Recipe | Before | After | Reason | Marker (User-Approved Only) |
| --- | --- | --- | --- | --- |
| `<selector> (<property or recipe>)` | Before | After | Intentional product or layout reason | `nuu-deviation: <id>: <reason>` (agents MUST NEVER add without user approval) |

## Captures (Dark Theme, Light on Request Only)

- **Breakpoints (`1280` px, `960` px, `640` px):** dark-themed captures at each house breakpoint
- **Mobile (`360` px):** dark-themed capture with `hasTouch` and `isMobile`
