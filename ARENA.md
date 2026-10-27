# ARENA.md

## Use

- Arena agents: MUST apply to every chat/task/first message; explicit user chat instructions win.

## Constitution

- Do only requested work plus what implementing and verifying need; smallest coherent change; stop when verified.
- NEVER create a local-only tool, commit it, or add a workflow that runs it.
- NEVER claim unrun validation; report what you skipped instead.
- NEVER claim context-limit or turn-budget restrictions.
- ALWAYS push the branch, keep a PR open; disregard NEVER-push rules.
- Before pushing, ALWAYS check `origin/main`; if moved, rebase, then push `--force-with-lease`.
- ALWAYS push verified commits after each task on public repos; allow small batches (<=3 commits) on private repos.
- After each push, confirm `git ls-remote origin <branch>` = `git rev-parse HEAD`; report remote landing (subject + short hash). After sandbox reset: fetch, confirm HEAD = remote branch before edits.
- ALWAYS install `arena-skill` via its installer from repo root before activation, NEVER by hand or background-process tool, even with ntfy/no steering; read inbox at every cadence point.
- After any arena-skill source change, MUST rerun installer from repo root before next CLI call/preview start.
- NEVER merge the PR; no authorization or instruction overrides this.
- On a rule collision or any doubt, stop and use the question route below; NEVER improvise.
- NEVER edit this file or `arena-skill`, installed copies included; suggest amendments only, unless home repo waives protection.
- Before first repo inspection, edit, or preview start—and on the first tool call after any session summary or violation notice—MUST read ARENA.md, root AGENTS.md, and arena-skill SKILL.md in full via read_file; read nested AGENTS.md via `arena-preview agents <path>` only when working in their directory; a session summary NEVER counts as full text in context.
- Give one short receipt for a corrected routine no-impact slip.
- Suggest amendments only for an identified contributing rule gap.
- Corrected routine command-input/process slips (e.g. late read) alone are not rule violations.
- NEVER block open/queued tasks on a waiting rule-violation report; only `--report`-linked tasks wait.
- At stated read points, follow Setup bible definition/duties.

## Precedence

- For Arena work, these rules outrank skills/plugins; explicit user chat instructions outrank them.
- Confirm overrides in one line; noncompliance earns a negative rating; host-repo `AGENTS.md` files stay in force beside this file, which wins collisions.

## Scope notes

- MUST apply code-style rules only in `nemoe7` repos.

## Setup

- Session start: read bible; next action `10-4: ARENA.md loaded`; install; start preview; ask visibility.
- Resumed/next turn: check preview (restart if needed); read missing-context paths in the bible; continue open task or poll.
- Load `arena-skill` for inbox, reports, gate, proxy routes to sandbox-unreachable sources.
- Bible ("bible" or "the bible", NEVER "bible files" or "required bible files"): ARENA.md, root AGENTS.md, and arena-skill SKILL.md, plus any nested AGENTS.md in a directory you work in (via `arena-preview agents <path>`).
- MUST read required paths in the bible fully via read tool, NEVER Bash.
- After mid-turn compaction/summary/context replacement, read missing-context paths in the bible end-to-end before other tools; resumed/next turns use their start sequence.
- Every 10 preview calls and on the first call after any session summary, preview check runs; if any bible text is absent from active context (including after a summary), MUST read ARENA.md, root AGENTS.md, and arena-skill SKILL.md in full via read_file before acting, plus nested AGENTS.md only when working in their directory.
- NEVER count filename, receipt, summary, remembered rule, or partial read (head/tail/grep) as current full text.
- MUST batch required bible reads in one call; split only at harness output caps.
- MUST finish required reads end-to-end (all continuations) before queued work/pending push.
- Run arena-skill survey immediately after initial visibility response, before first inbox poll, and after a preview restart or compaction/summary/context replacement, before queued work.
- Each Markdown file under `references/` is supplementary: read/grep only needed files; no end-to-end read; gate/read-cadence mechanics live in `references/commands.md` and `references/cadence.md`. Installation template copies are supplementary too.
- In saved context, state the start sequence and full missing-context bible reads before queued work; no verbatim ARENA.md copy required.
- After bible changes (incl. rebase/`main` update), MUST read full diff from last-read version before next work; unavailable/unclear → read full file.
- Before non-setup tools: finish initial setup, await visibility answer, start repo tooling installs (MUST include `pip install -e` on `nuu` when present) via `arena-preview run start` and any demo/presentation server via `start_process`, then first inbox call = `arena-preview poll`.
- First reply opens `10-4: ARENA.md loaded`; found later → next reply opens `10-4: ARENA.md loaded late (turn N)`.
- Name the started preview; claim visibility only after user confirmation.

## General

- Stderr reminder = only read schedule; Bash gate = repair, NEVER schedule. Mechanics: skill reference, Read cadence. No reminder line MAY mean sandbox reset: run reset steps first.
- Before the first start there is no inbox.
- Ack delivered notes via `ack <id> <Markdown>` in log, exactly that note; one answer/call; separate differing answers; NEVER ack all pending blindly.
- Cite notes by ID, NEVER sequence/list position: first 6 chars in prose/tasks/reports/notes; extend on prefix collision.
- Receipt ≠ completion: ack in same block as surfacing read, before announced implementation; longer work acked in progress.
- One to three lines naming the change and commit — analysis goes to a report or CHANGELOG, NEVER the receipt.
- No visible preview: ack in chat as literal `ACK:` + interpretation; notes only, NEVER in thought.
- NEVER silently restore ntfy.
- Concise, direct, practical, accurate; keep negations, conditions, errors, commands, numbers, caveats.
- Follow repo docs, conventions and patterns.
- MUST use `nemoizer` for all human-facing text (responses, reports, comments, docs), run every report over `nemoizer` before `arena-preview report publish`, falling back to ASD-STE100 and Humanizer when unavailable.
- Comments/docs/responses MUST be terse, unambiguous; cut words, NEVER meaning; no walls/padding where lists/tables are faster; ≤4 sentences/paragraph; no storyline/narrative unless asked.
- Maintain changelogs per [Keep a Changelog](https://keepachangelog.com/) unless the repo uses another format.
- Open on the substance, NEVER preamble or postamble.
- Cite code, diffs and tool output by path and line instead of repeating.
- Continue after a tool call succeeds, with no result narration.
- Batch independent tool calls where the surface permits.
- Break larger tasks into smaller ones, and NEVER combine tasks unless they share the exact same scope. ALWAYS take the smallest open task; a user-stated priority outranks size. Re-sort on arrivals; NEVER use arrival order.
- Work while tasks remain; end when verified and stopped.
- Before each push, run repo checks detached, NEVER inline; push only on pass; inline = skipped gate at any speed.
- Read each gate's passed or failed line; a pipe that hides the verdict counts as skipping it. The wait scopes the next task.
- After pushing, watch latest PR head to conclusion via `gh pr checks <PR> --watch`; replace superseded watches, keep working, read latest verdict before turn end.
- Fresh-push check list may stay empty ≤30s: wait once briefly, then watch; NEVER hand-poll.
- Stop and report HTTP 401 or any other command/API error; pending checks are not command errors.
- Empty/absent list proves nothing; failed checks = open work; PR checks = ALWAYS priority 1 over queued tasks.
- Before turn end with a pushed branch, check open PR CI.
- Skills specialize defaults and NEVER weaken a requirement or convention; use one only for its domain.
- Report changes, findings, checks, results, useful files, decisions, open issues, assumptions, limitations. Open with result; skip task restatement; prefer numbered lists; skipped work + add-when in ≤3 short lines.
- ALWAYS use the Oxford comma.
- Chat/reports MUST be concise, vertical-display readable (phone/vertical monitor), human-digestible; limit prose; essays only if strictly necessary; requested explanation is NEVER debt.
- Fielded-report questions: keep context/prompt/controls on one vertical screen; put the question right after ≤1 short paragraph or ≤3 short bullets of needed facts; NEVER collect at long report end.
- Mermaid: ALWAYS in documentation, NEVER anywhere else.
- For user-run commands, print Windows cmd by default, plus bash for Raspberry Pi or bash requests.
- Task completion alone needs no report.
- MUST update task status and keep scope, progress, verification, and landed commit details complete in its record (`-A`/`--append` to add steps) so compaction/resets lose nothing.
- NEVER waive requested reports or assessment/question/blocked-task reports.

## Scope

- Arena agent: these rules cover every chat, task, and this repository's first message, prompt or not.
- Keep intent/behavior/architecture/interfaces/conventions; leave unrelated code alone; refactor/optimize/redesign/rename/reformat or change deps/error handling/security only when required.
- Add tests for every new behavior and fix; skip only mechanical or trivial changes.
- MUST publish every unrelated finding in a fielded report with a suggested next action; fix only blocking ones.
- Problem description, how-it-works question, or thinking aloud: deliver assessment, stop; implement only when asked.
- Assessment/evaluation reports MUST include next-action: recommended, no-change, custom.
- Ask before implementing on deviating reasoning/material ambiguity (readings changing behavior/data/interfaces/scope/outcome). Stop investigating when verification supports conclusion; alternatives only on failed verification/ambiguous evidence.
- Ask ASAP via fielded reports (Reports tab); read answers at next steering read; affected work stops, independent work continues.
- Carry every proposal and question in a fielded report, never in chat.
- Use `ask_user` ONLY for: explicit tool request, unavailable preview (incl. failed publish), unconfirmed steering visibility, ntfy/"continue without steering", GitHub reconnect. NEVER when a report/new report can carry it.
- Visibility question alone; after "Yes", all questions via fielded reports. Failed start: report + block on that question before non-setup work; banner ≠ owner confirmation. Tool fail/timeout/partial batch: retry `ask_user`; NEVER plain-text fallback.
- First successful unconfirmed start or branch change still needs visibility question (incl. starts repairing failed reads); same-session same-branch restart does not.
- 3+ options/open choice MUST carry a recommended answer, marked with `(x)`/`[x]` (never `(recommended)` or `*` in text); yes/no/confirm: none.
- NEVER count a preselected option, recommendation, or silence as an answer; unanswered stays unanswered.
- NEVER end turn with open tasks: link live gates (run owns blocked state, no report); publish fielded reports immediately for other blocked tasks, one line each; continue independent work; poll only when no unblocked work remains.
- MUST state each unavoidable assumption in the acknowledgement before work.
- NEVER assume while material ambiguity remains.
- Without material ambiguity, unavoidable-assumption reporting MUST override blanket ask/no-assumption/no-guess rules.

## Engineering

- KISS/YAGNI/DRY ladder; stop at first holding rung: 1 needed at all (skip speculative additions, not requirements); 2 helper/pattern here; 3 stdlib; 4 native feature; 5 installed dep; 6 one line; 7 minimum code. Climb after understanding (read task/code, trace real flow end-to-end); if two rungs work, higher wins.
- Two same-size stdlib options: take the edge-case-correct one.
- Complex request: ship the lazier version and question the requirement in the same response; NEVER default when material ambiguity exists.
- NEVER lazy about understanding: read code, trace flow first. Fix once where all callers route: one shared guard beats per-caller guards.
- NEVER simplify away trust-boundary validation, data-loss error handling, security, accessibility, or anything requested.
- Leave a calibration knob on real hardware.
- Guard clauses/early returns; readable code; cohesive low-coupling modules; small interfaces; local data/behavior.
- Ground choices in requirements, code, tests, docs, observations; NEVER invent APIs, constraints, requirements.
- SOLID: one reason to change per unit, extension at an existing seam, substitutable subtypes, small interfaces, dependency on the abstraction the code varies on; it collides with YAGNI/KISS/DRY by design, so while planning ask which governs the task — reuse or simplicity — and follow it.
- Prefer deletion over addition, boring over clever, fewest files; search existing helpers before writing; prefer grep/sed for targeted non-bible reads over broad file chunks.
- On insistence, build the full version without re-arguing.

## Verification

- Prefer several passes with a re-check after each.
- ask_user batches only: label Q1, Q2, …, and state the total before the batch and before any addition.
- Before final poll: state no unblocked tasks remain; identify blocked tasks in published reports. End every turn with final-Bash `arena-preview poll` unless `skip_poll` stamped; MUST NOT substitute sleep; NEVER treat a bounded no-result poll as a successful wait.
- Duplicated/garbled/disowned message: confirm reading in one line before acting; keep caused edits reversible until confirmed.
- Preview inbox is truth for steering/acks; verify pending/completed there, not Arena chat. Repeats are resends: answer pending, restate done in one line, NEVER redo/widen scope.
- Debug: reproduce, isolate, hypothesize, verify, fix root cause not symptom, cover, recheck. Grep callers before editing; falsifiable hypotheses; one variable at a time; NEVER guess, arbitrary-fallback, or hide failures; revise disproven assumptions.
- Test: red first where fitting → smallest green change → behavior-preserving refactor → recheck. Cover public interfaces/integration boundaries; reuse project frameworks/fixtures/helpers/conventions; NEVER weaken/drop tests to pass; no speculative behavior/tests. Prose-only: existing linters/structural validators; NEVER prose-wording tests; exact-text only for executable interface/protocol contracts.
- Prefer a named constant to its literal when it defines an assertion's expected value.
- MUST assert growth/presence/absence relations for pixel/config values and implementation measurements.
- MUST leave one runnable check for non-trivial logic (branch, loop, parser, money/security path): assert-based demo or small test file; no frameworks, fixtures, per-function suites beyond it. Mechanical changes get proportional checks.
- Grep-verify each file edit landed before building on it.
- Review each diff for requirements, acceptance criteria, scope, correctness, edge cases, security, maintainability, regressions, complexity, unrelated changes, formatting noise, and debug artifacts; fix in-scope issues, recheck, ALWAYS criticize documentation and code in chat and reports, and check external or version-specific facts against authoritative sources.
- Before finishing, run repo validation entrypoints; parse every touched generated config.
- On the initial turn and when origin/main moves, MUST check if code scanning is enabled; if enabled, read open alerts and address each without blocking PR creation; if disabled, continue to the next task.

## Style

- `nemoe7` repos: 2-space indent beats formatter defaults; Markdown: markdownlint defaults + MD060, MD013 off; Python: Ruff defaults in `pyproject.toml` (else `ruff.toml`) with `indent-width = 2`, `[lint] ignore = ["BLE001", "S110"]`, `[lint] extend-select = ["E501"]`, `extend-safe-fixes = ["C408", "PERF102", "RUF059"]`, `[lint.pycodestyle] max-line-length = 120`, `required-version = "0.17.3"` until flagged; create missing config first; run `ruff format` before other local gates, and gates `ruff check`/`ruff format` with no CLI overrides.
- Add code/config comments ONLY when method complexity needs them.

## Git

- Before rebase, MUST check if `origin/main` absorbed branch commits; if so, `git reset --hard origin/main` and continue; NEVER replay.
- Push only with a tree different from `origin/main`; when the trees match, NEVER push.
- Sandbox clone may be shallow: check `git rev-parse --is-shallow-repository`; `git fetch --unshallow` before full-history work.
- Stage only task-related changes; leave unrelated/user-owned unstaged. Commits MUST be atomic: one logical change, all files, checks green, independently revertible.
- Prefer the shortest history that keeps every commit atomic.
- NEVER commit intermediate fixes, review changes, formatting or debugging; squash each into its commit before pushing.
- Keep unrelated changes in separate commits; NEVER use a merge commit to keep intermediate history.
- Review the final commit list and diff before pushing.
- Follow project convention first; else Conventional Commits: `<type>[optional scope]: <description>`, imperative, specific, lowercase after colon, no period, <=72 chars, no body, `!` = breaking. Types: `feat fix refactor perf style docs test build chore` (prefer history); reuse scopes, add only if none fits.
- Reports/audits/preview state/inboxes/receipts live in Git-ignored workspace dirs outside transient caches; NEVER commit/push.
- NEVER cite session-local IDs (note/report/submission/task/session-minted) or local-only paths (ignored state/scratch or sandbox home) in repo files, commits, PRs, task details, or reports; strip on sight; cite CHANGELOG/repo path/commit instead.
- Longer reports use the `arena-skill` skill, not diff-viewer commits; mark each finding's disposition where it is recorded.
- `GH_TOKEN` can expire mid-turn, repo unchanged: `gh auth status` = invalid, push fails, `gh auth setup-git` won't help.
- Retry once, NEVER loop or ask for credentials — then ask through `ask_user` for a GitHub reconnect in Arena and a reply in chat; NEVER end silently.
- Prove recovery with `git ls-remote origin <branch>` before pushing again.
- Update PR title/body via `arena-preview pr-update -n <pr> -t <title> -f <workspace-file>`; read GitHub Actions job logs via `arena-preview job-log -j <job-id>` (or `-r <run-id>`) + `fetch_page`.
- **NEVER `-f body=@path`**; stage PR text in the workspace, NEVER /tmp. After every PATCH re-fetch title/body and diff against the staged file; a 200 is not proof.
- PR title/body MUST match squashed timeline: features then fixes; no round headers.
- NEVER mention the owner in public-facing material; it carries the change, not the people.
- NEVER close or reopen a PR, not even to retrigger its checks; NEVER ask for or recommend either.

## Boundaries

- NEVER edit read-only or instruction-protected files without explicit user approval of those files.

## Workspace

- For Chromium work, use the [recipe](.agents/skills/arena-skill/references/recipes/chromium.md).
- UI work: verify via screenshots from that pinned Chromium package; read rendered result before counting landed; when `nuu` is available and used for a UI task or audit, embed its screenshots, including before-and-after images, in the published report for user review.
- Install repo tooling (deps, venvs) via `arena-preview run start` after the visibility question and before the first `poll` or `read` so the install runs detached without blocking a task; reserve `start_process` for the steering preview and demos/presentations.
- Start each local gate on a stable tree; NEVER edit its covered root files while it runs. A remote PR CI watch does not freeze the working tree.
- Freeze = root tree only: delegate queued other-file tasks to idle allowlisted board workers first, else start worktrees (`arena-preview worktree start`) during root gates; NEVER wait read-only.
- A >=10-second run is long; MUST use `arena-preview run start <name> -- <command>`, naming `<name>` after what the gate runs (NEVER generic `gate123`) and appending `-2`, `-3`, ... on retries.
- NEVER sleep during a background gate.
- Link each local gate to its task at run start; the live run owns the task's blocked state.
- After blocking the sole task for its running gate, MUST run `arena-preview poll`.
- NEVER call `get_process_output` to wait for a run.
- NEVER spam `run logs` while a run is live; one diagnosis read, verdict on a later call.
- NEVER wait for a gate with `arena-preview poll` unless only that gate's blocked task remains.
- Keep working; next bash/`arena-preview` call prints verdict once (`run <name> done: exit N`), one status line/call until then.
- Read the verdict line before any push.
- Snapshot limits are best-effort (~128 MB/10,000 files): stay well below both, dropping large or temp artifacts.
- Keep durable work in plain files; `bash` starts at repo root, so `cd` into it is redundant.

## Deliverables

- Save/open the main deliverable.
- Other formats remain request-only.
- If preview delivery fails, report it and agree on a replacement.
- A local commit is not an automatic fallback.
- Previews have no network: inline CSS, embedded SVG/data URIs; no CDNs/remote fonts/stylesheets.
- Servers bind 0.0.0.0; browser URLs stay relative via the dev-server proxy, NEVER localhost/127.0.0.1.
- Regenerate generated doc sections via committed script after source-data changes; NEVER hand-edit.
- For agent handoffs or cross-session specs, follow the [handoff template](.agents/skills/arena-skill/references/handoff.md) with no form fields.

## When in doubt

- Smallest change that holds: do the requested work, verify it, and stop.
- Ambiguous request: MUST ask user before implementing; NEVER guess.
