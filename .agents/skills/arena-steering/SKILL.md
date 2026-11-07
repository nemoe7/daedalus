---
name: arena-steering
description: Steer an Arena.ai agent mid-turn through a local preview inbox, dependency task queue, and fielded Markdown reports. Use in Arena Agent Mode when the user wants steering or a report, or when ARENA.md requires it. NEVER USE THIS SKILL OUTSIDE OF ARENA.AI.
license: MIT
compatibility: Arena.ai Agent Mode, Python 3.11+, persisted workspace and long-lived process tools.
metadata:
  version: "2.2.2"
---

# Arena Steering

Use one `steer` server and one state directory per session (`arena-state` by default).

- Choose task, report, field, and gate IDs as semantic tokens that describe what each represents.

## Server and version

At every turn start, follow [the v2 resume-turn recipe](references/recipes/resume-turn.md).
NEVER stop or restart a running Steering preview server unless a health check fails or the user asks.
For any browser work, follow [the Chromium recipe](references/recipes/chromium.md) as standard local tooling.
For GitHub Actions logs, follow [the direct job-log recipe](references/recipes/job-logs.md).

## Messages (`Inbox`)

```bash
steer read
steer poll
steer ack <id> '<Markdown>' [-i <task-id> -t "<title>" -T <type> -S <scope> -d "<detail>"]
steer msg list [-p PAGE] [-s SIZE]
steer msg <id>
```

- Every message ID matches `^[0-9a-f]{6}$`.
- `blocking=true` messages advance `sent -> seen -> acked`; `blocking=false` messages advance `sent -> seen` on `read` or `poll`.
- ALWAYS read attached images and text in full with `functions.read_file` before acknowledging their note.
- Report missing attachment bytes instead of inferring content from a listed path.

## Tasks (`Tasks`)

```bash
steer tasks [-f, --full]
steer task <id>
steer task new <id> "<title>" -z <size> [-T <type>]... [-p <priority>] [-S <scope>] [-d "<detail>"] [-m <msg-id>] [-r <report-id>] [-k <task-id>]
steer task finish <id> [-c <full-commit>]
steer task mod <id> deets <text>
steer task mod <id> deets rm <1-based-index>
steer task mod <id> links [msg|report|task] <id> [-A "<detail>"] [--delete <number>]
steer task mod <id> links rm msg|report|task <id>
steer task mod <id> status|title|type|scope <value>
steer task unlink <id> [msg|report|task] <link-id>
steer task rm <id>
```

- Estimate each task's Fibonacci size against a known reference task, combining effort, complexity, and uncertainty, not time or cost.
- Create ordered task phases by repeating `-T` with `report`, `code`, `docs`, `ops`, or `misc`.
- Finish phases on a clean tree using `steer task finish ID [-c FULL_COMMIT]`. Reports need a linked published report or answer, and code/docs need a unique pushed full commit.
- Tasks sit in a mutable queue where dependents automatically follow their task links (`links.task_ids[]`).
- Block phases on unfinished dependencies or running gates. Non-report phases also wait on unanswered linked reports, except completed report-phase evidence.
- When creating a task, omit `-p` unless the user states a priority.

## Reports (`Reports`)

```bash
steer reports [-s, --stale]
steer report <id>
steer report pub <source.md> -i <id> -t "<title>"
steer report pin ID
steer report unpin ID
steer report rm <id|--stale>
```

- Publish agent handoffs and cross-session specifications as unfielded Steering reports.
- Every report field requires a statement `label` before its options (`- ( )`, `- (x)`, `- [ ]`, `- [x]`) or after `label: ___`.
- Use the full question as each report field label, and give each option a specific, self-identifying label.
- Write report option text as an imperative action, not a first-person statement or permission request.
- Field IDs default to kebab-case of `label` with trailing `-` stripped, or come from an optional `<!-- field-id -->` comment above the field.
- Radio (`choice`) and checkbox (`checkbox`) fields automatically render a built-in `"Something else..."` free-form input in the web UI.
- After the first answer, a report body change requires `steer report rm <id>` before republishing the same ID. Removal keeps old answers in the inbox, detaches task links, and preserves task details.

Before publishing reports, check `steer tasks` for related upcoming tasks and name their titles and scopes without IDs.
