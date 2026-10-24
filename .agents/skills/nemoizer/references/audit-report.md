# Nemoizer Audit: <target>

- **Status:** `<pass | action-needed>`
- **Counts:** `<N>` blocking, `<N>` manual review, `<N>` `nonemo` markers (`<N>` files)

## Blocking Findings

| Location | Rule | Before | After |
| --- | --- | --- | --- |
| `path:line` | `NMO001`: avoided word | Before | After |

## Manual-Review Findings

| Location | Rule | Disposition | Before | After | Reason |
| --- | --- | --- | --- | --- | --- |
| `path:line` | `STE901`: passive voice | `keep` or `fix` | Before | After | Active voice is clear or rewrite clause |

## Suppression Markers (`nonemo`)

| Location | Rule | Scope | Text | Disposition |
| --- | --- | --- | --- | --- |
| `path:line` | `NMO100` | `line` or `file` | Marker line text | `keep` or `remove` |
