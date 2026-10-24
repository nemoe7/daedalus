# Nemoizer Audit: <target>

- **Command:** `python3 skills/nemoizer/scripts/nemoize.py --audit <target>`
- **Counts:** `<N>` blocking, `<N>` manual review, `<N>` `nonemo` markers (`<N>` files)
- **Status:** `<pass | action-needed>`

## Blocking Findings

| Location | Rule | Finding | Fix |
| --- | --- | --- | --- |
| `path:line` | `NMO001` | `transmit -> send` | Replace `transmit` with `send` |

## Manual-Review Findings

| Location | Rule | Finding | Disposition | Reason |
| --- | --- | --- | --- | --- |
| `path:line` | `STE901` | `passive voice` | `keep` or `fix` | Active voice is clear or rewrite clause |

## Suppression Markers (`nonemo`)

| Location | Rule | Scope | Text | Disposition |
| --- | --- | --- | --- | --- |
| `path:line` | `NMO100` | `line` or `file` | Marker line text | `keep` or `remove` |
