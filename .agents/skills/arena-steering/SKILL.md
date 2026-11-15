---
name: arena-steering
description: Steer an Arena.ai agent mid-turn through a local preview inbox, dependency task queue, and fielded Markdown reports. Use in Arena Agent Mode when the user wants steering or a report, or when ARENA.md requires it. NEVER USE THIS SKILL OUTSIDE OF ARENA.AI.
license: MIT
compatibility: Arena.ai Agent Mode, Python 3.11+, persisted workspace and long-lived process tools.
metadata:
  version: "2.3.11"
---

# Arena Steering

Use one `steer` server and one state directory per session (`arena-state` by default).

Choose task, report, field, and gate IDs as semantic tokens describing each.

## Server and version

At every turn start, follow [the v2 resume-turn recipe](references/recipes/resume-turn.md).
NEVER stop or restart a running Steering preview server unless a health check fails or the user asks.
For any browser work, follow [the Chromium recipe](references/recipes/chromium.md) as standard local tooling.
For GitHub Actions logs, follow [the direct job-log recipe](references/recipes/job-logs.md).

After any arena-steering source change, MUST rerun skill installer from repo root before next CLI call or preview-server start.
Run `steer survey` immediately after initial visibility response and before first inbox poll.
Run survey after preview restart and after compaction bible read before queued work.
Read `references/` file only when a question needs it, except `references/cli.md` carries commands and `references/recipes/` procedures.
Installation template copies are supplementary.
Install repo tooling through installed Steering skill.
Start repo-tooling installs with `steer run start -t <task> <name> -- <command>` after visibility and before first `steer poll` or `steer read`.
Use `start_process` for Steering preview.
After starting demo servers, use `steer poll` first.

## Messages (`Inbox`)

```bash
steer read
steer poll
steer ack <id> '<Markdown>' [-i <new-task-id> -t "<title>" -z <size> [-p <priority>] [-T <type>]... [-S <scope>] -d "<detail>"...]
steer msg list [-p PAGE] [-s SIZE]
steer msg <id>
```

Every message ID matches `^[0-9a-f]{6}$`.
`blocking=true` messages advance `sent -> seen -> acked`; `blocking=false` advance `sent -> seen` on `read` or `poll`.
ALWAYS read attached images and text in full with `functions.read_file` before acknowledging their note.
Report missing attachment bytes instead of inferring content from listed path.
Use `steer ack -i -t -z -d` to create and link task while acknowledging one message or report answer.
MUST create that task before starting any work the message asks for.
Link messages only when they change a task's scope or acceptance criteria.
Leave unrelated clarifications unlinked.
Run `steer read` when stderr reports pending items.
Act on other stderr reminders as directed.
When stderr directs stale-report removal, run `steer report rm --stale`.
Before first start there is no inbox to poll, and missing state file then is no failed read.
ALWAYS provide substantive answer to each delivered message in its own `ack <id> <Markdown>` call before any chat follow-up.
One call carries one answer text, so acknowledge messages separately when answers differ.
NEVER blindly acknowledge all pending messages.
Treat receipt as received, not implemented.
Acknowledge message in same tool block as read that surfaced it.
Acknowledge message before starting any implementation that acknowledgement announces.
Receipt work that outlives tool block as in progress rather than waiting until it finishes.
Keep receipt to one, two, or three lines naming change and its commit once change exists.
Use preview inbox as source of truth for Steering instructions and acknowledgement receipts.
Verify pending and completed work in preview inbox instead of inferring from Arena chat output.
Treat repeated or identical message as resend rather than new instruction.
Answer whatever is still pending in a resend.
Restate completed work in one line when a message is resent.
NEVER treat resend as authorization to redo finished work or widen scope.

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
steer task unlink <id> [msg|report|task] <id>
steer task rm <id>
```

Estimate each task's Fibonacci size against known reference task, combining effort, complexity, and uncertainty, not time or cost.
Create ordered task phases by repeating `-T` with `report`, `code`, `docs`, `ops`, or `misc`.
For approved report work changing feature or documentation, MUST order `report` phase first, followed by each required `code` or `docs` phase.
Finish phases on clean tree using `steer task finish ID [-c FULL_COMMIT]`. Reports need linked published report or answer, and code/docs need unique pushed full commit.
Tasks sit in mutable queue where dependents automatically follow their task links (`links.task_ids[]`).
Follow task order returned by Steering.
Complete next unblocked task returned by `steer poll` before unrelated task or report work.
If steer poll directs work, MUST continue that work and poll again. NEVER end on that warning.
Reassess task size with `-z` on every task-detail change, and keep each detail within 120 characters.
Block phases on unfinished dependencies or running gates. Non-report phases also wait on unanswered linked reports, except completed report-phase evidence.
Use `steer run start` for supported long-running jobs and task gates; keep persistent services on platform `start_process` path.
When creating task, omit `-p` unless user states priority.
Keep task details to concise current facts about task, not chronological action log.
Update task details when facts they record change.
Task completion alone requires no report.
Update task status and keep scope, progress, verification, and landed commit details complete with `-A` or `--append`.
Only task linked to report through `--report` waits on that report.
Treat run taking 10 seconds or more as long run.
Start long runs with `steer run start -t <task> <name> -- <command>`.
Give each run a descriptive name.
Append `-2`, `-3`, ... to run names for retries after failure.
Link each local gate to its task when starting run.
Let live run own its task's blocked state.
After blocking sole task for its running gate, MUST run `steer poll`.
NEVER run `steer poll` as gate wait unless sole remaining task is blocked for that gate.
Continue independent work and poll when no unblocked work remains.
Before final poll, MUST state that no unblocked tasks remain and identify any blocked tasks in their published reports.
ALWAYS end every turn with `steer poll` on final Bash call, unless listing carries `skip_poll` stamp.
Call `functions.bash` with `{"command":"steer poll","timeout":1800}` for final poll.
NEVER wrap final poll in shell timeout.
MUST NOT substitute sleep for final poll.
NEVER treat bounded no-result poll as successful wait.

## Reports (`Reports`)

```bash
steer reports [-s, --stale]
steer report <id>
steer report pub <source.md> -i <id> -t "<title>"
steer report pin ID
steer report unpin ID
steer report rm <id|--stale>
```

Publish agent handoffs and cross-session specifications as unfielded Steering reports.
For agent handoffs and cross-session specifications, follow [handoff template](references/handoff.md).
Every report field requires statement `label` before its options (`- ( )`, `- (x)`, `- [ ]`, `- [x]`) or after `label: ___`.
Use concise, task-specific labels: decision subjects for questions and chosen outcomes for options.
NEVER use question text or generic words such as Decision, Answer, Option, or Approve as labels.
Wrong: Decision: Answer
Right: The method we should use: Share one helper class between tabs.
Read [report-label examples](references/report-labels.md) for worked examples across field types.
Write report option text as imperative action, not first-person statement or permission request.
Field IDs default to kebab-case of `label` with trailing `-` stripped, or come from optional `<!-- field-id -->` comment above field.
Radio (`choice`) and checkbox (`checkbox`) fields automatically render built-in `"Something else..."` free-form input in web UI.
After first answer, report body change requires `steer report rm <id>` before republishing same ID. Removal keeps old answers in inbox, detaches task links, and preserves task details.
Apply user-approved formatting-only changes to working source without republishing answered report or requesting another approval.
Publish all reports through `arena-steering` skill in shared preview.
Carry every proposal and question in fielded report, NEVER in chat.
Use new report for follow-up to report that already has answers.
NEVER waive requested reports or reports required for assessments, questions, or blocked tasks.
Publish every unrelated finding in fielded report with suggested next action.
Keep each fielded-report question, its context, and its controls on one vertical screen.
Put question after at most one short paragraph or three short bullets of facts needed to answer it.
NEVER collect questions at end of long report.
Mark recommended answer for every question with options using `(x)` or `[x]`.
NEVER write `(recommended)` or `*` in option text.
NEVER count silence as answer.
Treat unanswered question as unanswered.
Include next-action field in every assessment or evaluation report.
Give next-action field a recommended option and a no-change option.

Before publishing reports, check `steer tasks` for related upcoming tasks and name their titles and scopes without IDs.
