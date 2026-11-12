---
name: arena-steering
description: Steer an Arena.ai agent mid-turn through a local preview inbox, dependency task queue, and fielded Markdown reports. Use in Arena Agent Mode when the user wants steering or a report, or when ARENA.md requires it. NEVER USE THIS SKILL OUTSIDE OF ARENA.AI.
license: MIT
compatibility: Arena.ai Agent Mode, Python 3.11+, persisted workspace and long-lived process tools.
metadata:
  version: "2.3.10"
---

# Arena Steering

Use one `steer` server and one state directory per session (`arena-state` by default).

- Choose task, report, field, and gate IDs as semantic tokens that describe what each represents.

## Server and version

At every turn start, follow [the v2 resume-turn recipe](references/recipes/resume-turn.md).
NEVER stop or restart a running Steering preview server unless a health check fails or the user asks.
For any browser work, follow [the Chromium recipe](references/recipes/chromium.md) as standard local tooling.
For GitHub Actions logs, follow [the direct job-log recipe](references/recipes/job-logs.md).

- After any arena-steering source change, rerun the skill installer from the repository root before the next CLI call or preview start.
- Run `steer survey` after the initial visibility response and before the first inbox poll, and after a preview restart or a compaction bible read before queued work.
- Read a `references/` file only when a question needs it, except that `references/cli.md` carries commands and `references/recipes/` procedures; template copies are supplementary.
- Install repo tooling through the installed skill, starting installs with `steer run start -t <task> <name> -- <command>` after visibility and before the first `steer poll` or `steer read`.
- Use `start_process` for the Steering preview, then use `steer poll` first after starting demo servers.

## Messages (`Inbox`)

```bash
steer read
steer poll
steer ack <id> '<Markdown>' [-i <new-task-id> -t "<title>" -z <size> [-p <priority>] [-T <type>]... [-S <scope>] -d "<detail>"...]
steer msg list [-p PAGE] [-s SIZE]
steer msg <id>
```

- Every message ID matches `^[0-9a-f]{6}$`.
- `blocking=true` messages advance `sent -> seen -> acked`; `blocking=false` messages advance `sent -> seen` on `read` or `poll`.
- ALWAYS read attached images and text in full with `functions.read_file` before acknowledging their note.
- Report missing attachment bytes instead of inferring content from a listed path.
- Use `steer ack -i -t -z -d` to create and link a task while acknowledging one message or report answer.
- MUST create that task before starting any work the message asks for.
- Link messages only when they change a task's scope or acceptance criteria.
- Leave unrelated clarifications unlinked.
- Run `steer read` when stderr reports pending items; act on other stderr reminders as directed, and run `steer report rm --stale` when it directs stale-report removal.
- Before the first start there is no inbox, and a missing state file then is no failed read.
- ALWAYS give each delivered message its substantive answer in its own `ack <id> <Markdown>` before any chat; one answer per call, never a blind ack of all pending.
- A receipt means received, not implemented: acknowledge in the same tool block as the read, before the implementation it announces, and receipt long work as in progress.
- Keep the receipt to one, two, or three lines naming the change and its commit once it exists.
- The preview inbox is truth for Steering instructions and receipts: verify pending and completed work there, not in Arena chat.
- Treat a repeat as a resend: answer what is pending, restate completed work in one line, and NEVER redo or widen scope.

## Tasks (`Tasks`)

```bash
steer tasks [-f, --full]
steer task <id>
steer task new <id> "<title>" -z <size> [-T <type>]... [-p <priority>] [-S <scope>] [-d "<detail>"] [-m <msg-id>] [-r <report-id>] [-k <task-id>]
steer task finish <id> [-c <full-commit>]
steer task mod <id> deets <text> -z <size>
steer task mod <id> deets rm <1-based-index> -z <size>
steer task mod <id> attach add <path> -z <size>
steer task mod <id> attach rm <1-based-index> -z <size>
steer task mod <id> links [msg|report|task] <id> [-A "<detail>"] [--delete <number>] [-z <size>]
steer task mod <id> links rm msg|report|task <id>
steer task mod <id> status|title|type|scope <value>
steer task unlink <id> [msg|report|task] <link-id>
steer task rm <id>
```

- Estimate each task's Fibonacci size against a known reference task, combining effort, complexity, and uncertainty, not time or cost.
- Create ordered task phases by repeating `-T` with `report`, `code`, `docs`, `ops`, or `misc`.
- For approved report work that changes a feature or documentation, MUST order a `report` phase first, followed by each required `code` or `docs` phase.
- Finish phases on a clean tree using `steer task finish ID [-c FULL_COMMIT]`. Reports need a linked published report or answer, and code/docs need a unique pushed full commit.
- Tasks sit in a mutable queue where dependents automatically follow their task links (`links.task_ids[]`).
- Follow the task order returned by Steering.
- Complete the next unblocked task returned by `steer poll` before unrelated task or report work.
- If steer poll directs work, MUST continue and poll again; NEVER end on that warning.
- Reassess task size with `-z` on every task-detail change, and keep each detail within 120 characters.
- Block phases on unfinished dependencies or running gates. Non-report phases also wait on unanswered linked reports, except completed report-phase evidence.
- Use `steer run start` for supported long-running jobs and task gates; keep persistent services on the platform `start_process` path.
- When creating a task, omit `-p` unless the user states a priority.
- Keep task details to concise current facts, not a chronological action log.
- Update task details when their recorded facts change.
- Task completion alone requires no report.
- Update task status and keep scope, progress, verification, and landed commit details complete with `-A` or `--append`.
- Only a task linked through `--report` waits on that report.
- Treat a run of 10 seconds or more as long: start it with `steer run start -t <task> <name> -- <command>`, name it descriptively after what it runs, and append `-2`, `-3`, ... to retries.
- Link each local gate to its task at run start, and let the live run own its task's blocked state.
- After blocking the sole task for its running gate, MUST run `steer poll`; NEVER run `steer poll` as a gate wait otherwise.
- After one diagnosis, resume unblocked work and use a worktree if the gate freezes its files. Recheck logs only after meaningful progress, never just to wait.
- Continue independent work and poll when no unblocked work remains.
- Before the final poll, MUST state that no unblocked tasks remain and identify any blocked tasks in their published reports.
- ALWAYS end every turn with `steer poll` on the final Bash call unless the listing carries a `skip_poll` stamp, calling Bash with `{"command":"steer poll","timeout":1800}`; NEVER wrap it in a shell timeout, substitute sleep, or treat a bounded no-result poll as a successful wait.

## Reports (`Reports`)

```bash
steer reports [-s, --stale]
steer report <id>
steer report pub <source.md> -i <id> -t "<title>"
steer report pin ID
steer report unpin ID
steer report rm <id|--stale>
```

- Publish all reports through this skill in the shared preview; agent handoffs and cross-session specs follow the [handoff template](references/handoff.md) as unfielded reports.
- Every report field requires a statement `label` before its options (`- ( )`, `- (x)`, `- [ ]`, `- [x]`) or after `label: ___`.
- Use concise, task-specific labels: decision subjects for questions and chosen outcomes for options.
- NEVER use question text or generic labels (`Decision`, `Answer`, `Option`, `Approve`).
  - Wrong: `Decision: Answer`
  - Right: `The method we should use: Share one helper class between tabs.`
- Read [references/report-labels.md](references/report-labels.md) for worked examples across field types.
- Write report option text as an imperative action, not a first-person statement or permission request.
- Field IDs default to kebab-case of `label` with trailing `-` stripped, or come from an optional `<!-- field-id -->` comment above the field.
- Radio (`choice`) and checkbox (`checkbox`) fields automatically render a built-in `"Something else..."` free-form input in the web UI.
- After the first answer, a report body change requires `steer report rm <id>` before republishing the same ID. Removal keeps old answers in the inbox, detaches task links, and preserves task details.
- Apply user-approved formatting-only changes to the working source without republishing an answered report or requesting another approval.
- Carry every proposal and question in a fielded report, NEVER in chat, and use a new report for a follow-up to one that already has answers.
- Do not waive requested reports or reports required for assessments, questions, or blocked tasks.
- Publish every unrelated finding in a fielded report with a suggested next action.
- Keep each fielded-report question, its context, and its controls on one vertical screen: put it after at most one short paragraph or three short bullets of needed facts, and NEVER at the end of a long report.
- Mark a recommended answer for every question with options using `(x)` or `[x]`, NEVER `(recommended)` or `*` in option text.
- NEVER count silence as an answer, and treat an unanswered question as unanswered.
- Include a next-action field in every assessment or evaluation report, with a recommended option and a no-change option.

Before publishing reports, check `steer tasks` for related upcoming tasks and name their titles and scopes without IDs.
