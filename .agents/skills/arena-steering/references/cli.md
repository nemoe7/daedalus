# Arena Steering CLI reference

Bare `steer` runs from the repo checkout. `steer --version` shows the installed version. This guide describes agent-operated syntax, output, and exit status. Hard rules live in `SKILL.md`.

## Messages and waiting

| Command | Output on success | Exit |
| --- | --- | --- |
| `steer serve` | `Steering server listening on http://0.0.0.0:8000` | 0 on clean stop, 1 on error |
| `steer clear` | Clear database state, preserving timezone, files, and server survey memory | 0 on clear, 1 on error |
| `steer read` | `Inbox HH:MM:SS: N pending` and pending items | 0 on read, 1 on error |
| `steer poll` | Task, run verdict, skip, or full-wait instruction | 0 on delivery or timeout, 1 on error, 2 on repeat violation |
| `steer ack ID 'ANSWER'` | `ok ID` and any added task detail | 0 on acknowledgement, 1 on error |
| `steer msg list [-p PAGE] [-s SIZE]` | Newest-first IDs and six-word previews | 0 on list, 1 on error |
| `steer msg ID` | Stored body, parents, attachments, and acknowledgements | 0 on inspection, 1 on error |

`steer clear` immediately deletes messages, tasks, reports, and request bookkeeping without a confirmation prompt. It keeps the saved timezone, uploads, backups, and server survey memory.

`steer read` marks delivered items seen, not acknowledged. A separate `steer ack` records each pending ID's response. `steer msg list` starts with five rows per page, and `-s` accepts 5–50.

Inbox headers use the saved IANA zone or UTC. Reads omit display-only status tags and raw ISO timestamps.

`steer poll` snapshots runnable tasks on entry. It does not recheck newly unblocked tasks or display-only state changes. Linked live runs print `running: NAME` once, and a verdict prints `run NAME done: exit N`. The follow-up line is `DO NOT END TURN. POLL AGAIN.` A runnable task prints `DO NOT END TURN, CONTINUE TASK: TITLE | ID | TYPE`.

A full wait prints `Poll waited fully. End turn.` A skip prints `User skipped poll. End turn.`

`steer ack` accepts multiple IDs before one shared answer. `-i TASK -t TITLE -z SIZE [-T TYPE]... [-S SCOPE] [-d DETAIL]...` creates and links a task in that call.

An attached message adds each file path as a home-relative task detail without another flag. Its linked task uses P3 unless an explicit priority overrides it.

## Tasks and ordered phases

| Command | Output on success | Exit |
| --- | --- | --- |
| `steer tasks [-f] [-p PAGE]` | `ID - TITLE` rows | 0 on list, 1 on error |
| `steer task [-d]` | First runnable task or `No current task.` | 0 on inspection, 1 on error |
| `steer task ID` | Status, phase, scope, links, priority, size, and details | 0 on inspection, 1 on error |
| `steer task new ID TITLE -z SIZE [-p PRIORITY] [-T TYPE]... [-S SCOPE] [-d DETAIL]... [-m MSG] [-r REPORT] [-k TASK] [-g GATE]` | New task record | 0 on creation, 1 on invalid input or cycle |
| `steer task mod ID deets TEXT` | Updated task record | 0 on update, 1 on error |
| `steer task mod ID deets rm NUMBER` | Updated task without that detail | 0 on update, 1 on error |
| `steer task mod ID links TYPE LINK [-A DETAIL] [--delete NUMBER]` | Updated links and details | 0 on update, 1 on error |
| `steer task mod ID links rm TYPE LINK` | Updated task without that link | 0 on update, 1 on error |
| `steer task mod ID FIELD VALUE` | Updated priority, size, status, title, type, or scope | 0 on update, 1 on error |
| `steer task mod ID types TYPE ...` | Updated ordered phases | 0 on update, 1 on error |
| `steer task finish ID [-c FULL_COMMIT]` | Advanced phase or finished task | 0 on finish, 1 on unmet evidence or error |
| `steer task unlink ID [TYPE] LINK` | Updated task without that link | 0 on update, 1 on error |
| `steer task rm ID` | `Removed task ID` | 0 on removal, 1 on error |
| `steer ren OLD_ID NEW_ID` | Renamed task or report | 0 on rename, 1 on conflict or error |

Task sizes are 1, 2, 3, 5, 8, 13, 21, or 34. Priority accepts P1–P5. Without `-p`, report-only tasks default to P3; all others default to P4.

Runnable tasks sort by priority, size, and insertion order. Links to unfinished dependencies or unanswered reports block applicable phases.

`task new -m` and `task mod links msg` add attachment paths without changing priority. `-T`, `-d`, `-m`, `-r`, `-k`, and `-g` accept repeated values.

Repeated `-T` options define ordered phases, such as `-T report -T code`. A report phase needs a linked published report or a received answer.

Docs and code phases need a clean tree and a unique full Git commit hash. Operations phases need a clean tree without a hash. `steer task mod ID status finished [FULL_COMMIT]` uses the same finish validation.

## Reports

| Command | Output on success | Exit |
| --- | --- | --- |
| `steer reports [-s] [-p PAGE]` | `Reports: N` and status, field, answer, and title rows | 0 on list, 1 on error |
| `steer report ID` | Status, fields, and answer records | 0 on inspection, 1 on error |
| `steer report pub FILE -i ID -t TITLE` | `Published ID, N fields. Verified.` | 0 on verified publish, 1 on error |
| `steer report pin ID` | `Pinned ID.` | 0 on pin, 1 on error |
| `steer report unpin ID` | `Unpinned ID.` | 0 on unpin, 1 on error |
| `steer report rm ID [ID ...]` | `Dismissed reports: ...` | 0 on removal, 1 on error |
| `steer report rm --stale` | `Dismissed reports: ...` | 0 on removal, 1 on error |

The source is an ignored workspace file, not a commit artifact. Fielded questions use `<!-- question {"id":"...","label":"..."} -->` and labelled options. Mark at least one option in each radio or checkbox field with `(x)` or `[x]`.

For example:

```markdown
<!-- question {"id":"reuse","label":"How should the validation logic be reused?"} -->
How should the validation logic be reused?
- (x) Extract a shared validation helper. <!-- option {"label":"Extract a shared validation helper"} -->
- ( ) Duplicate the validation logic. <!-- option {"label":"Duplicate the validation logic"} -->

<!-- question {"id":"clause","label":"Should the rule forbid direct pushes?"} -->
Should the rule forbid direct pushes?
- (x) Approve forbidding direct pushes. <!-- option {"label":"Approve forbidding direct pushes"} -->
- ( ) Reject forbidding direct pushes. <!-- option {"label":"Reject forbidding direct pushes"} -->
```

`(x)` marks a default, not an answer. After the first answer, `steer report pub` rejects body edits under the same ID. Removal detaches task links but retains task details and answer history.

## Survey and installed skills

| Command | Output on success | Exit |
| --- | --- | --- |
| `steer survey` | Pending state, checked time, questions with image path, answers, and JSON template | 0 on inspection, 1 on error |
| `steer survey --template` | JSON template | 0 on template, 1 on error |
| `steer survey -f FILE` | `Submitted N survey answer(s) at UTC_TIME` | 0 on submission, 1 on error |
| `steer survey 'JSON_ARRAY'` | `Submitted N survey answer(s) at UTC_TIME` | 0 on submission, 1 on error |
| `steer skills` | Skill name, source, path, and description | 0 on list, 1 on error |

A live server receives submissions. Without it, the CLI evaluates survey input locally. `unknown` with confidence zero represents a wholly unknown answer.

## Detached runs and GitHub helpers

| Command | Output on success | Exit |
| --- | --- | --- |
| `steer run start -t TASK NAME -- COMMAND ...` | `Started run NAME (pid N)` and linked gate | 0 on launch, 1 on error |
| `steer run start -a PREVIOUS NAME -- COMMAND ...` | Started dependent run | 0 on launch, 1 on error |
| `steer run logs NAME [-n LINES]` | `run NAME running` or `run NAME exit N` with log tail | 0 on known run, 1 on lookup error |
| `steer run pr-checks [-n PR]` | Starts latest-head PR watch | 0 on launch, 1 on error |
| `steer pr-update -n PR -t TITLE -f BODY_FILE` | Verified PR update | 0 on parity, 1 on error |
| `steer job-log -j JOB_ID` | Fetchable signed job-log URL | 0 on success, 1 on error |
| `steer job-log -r RUN_ID` | Run page URL | 0 on success, 1 on error |
| `steer worktree <add|list|remove|prune|collect> ...` | Worktree result or collected patch | 0 on success, 1 on error |

A launched run has not passed. Its `run logs` exit line gives the verdict. An absent PR check list reports `no checks reported`, not a pass.

Job-log URLs are signed and temporary. `worktree add NAME [-b BRANCH]` uses `.worktrees/NAME`. The remaining worktree actions list, remove, prune, or collect work under this checkout.

## Exit status

Successful CLI commands normally exit 0. Invalid input, unknown IDs, missing state, and API failures exit 1 with `Steer error: ...` on stderr.

Repeat `steer poll` violations exit 2. `steer run logs` can exit 0 when its underlying run failed.
