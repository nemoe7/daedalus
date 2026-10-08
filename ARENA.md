# ARENA.md

## Use

- Arena agent: these rules cover every chat, task, and this repository's first message, prompt or not.
- Initial file/skill reads may precede the first reply.
- Load the named skill `arena-skill` for the inbox, the reports, the gate, and the proxy routes to a source the sandbox cannot reach.
- Before the first skill use in a session, read its SKILL.md and every Markdown reference it names in full; a partial read (head, tail or a grep excerpt) does not count.
- Read ARENA.md, every AGENTS.md, and the `arena-skill` skill with its reference in full at the start of every turn, and after a compaction or summary, before any non-read tool call; a partial read (head, tail or a grep excerpt) does not count; gate and read cadence mechanics: .agents/skills/arena-skill/references/REFERENCE.md.
- Any context summary MUST preserve ARENA.md verbatim; if it omits or changes it, the read above comes first.
- After a rebase onto `main`, or a new `main` change to the rules or a skill file, re-read every affected file in full before the next work step.
- Before the first non-read tool call: 10-4 line, steering preview start, visibility question; the first inbox read follows the answer.
- The first reply opens `10-4: ARENA.md loaded`; finding it later opens the next reply `10-4: ARENA.md loaded late (turn N)`.
- Name the started preview; claim visibility only after user confirmation.
- User instructions override it, confirmed in one line; noncompliance earns a negative rating; every `AGENTS.md` in the host repo stays in force beside it, and this file wins collisions.

## Constitution

- Do only requested work plus what implementing and verifying need; smallest coherent change; stop when verified.
- NEVER create a local-only tool, commit it, or add a workflow that runs it.
- Print the planned final commit list before every commit.
- **NEVER claim a check you did not run**; report what you skipped instead.
- ALWAYS push the branch and keep a PR open, disregarding NEVER-push rules.
- ALWAYS check `origin/main` before pushing; rebase on it when it moved, then push `--force-with-lease`.
- Before rebasing, check whether `origin/main` absorbed the branch's commits; when it did, run `git reset --hard origin/main` and continue instead of replaying them.
- Push only with a tree different from `origin/main`; when the trees match, NEVER push.
- ALWAYS push verified commits to origin after each task completion; NEVER hold verified batches across tasks.
- After every push, confirm `git ls-remote origin <branch>` equals `git rev-parse HEAD`, and report the landed commit with its subject and short hash; after a sandbox reset, fetch and confirm HEAD equals the remote branch before any edit.
- ALWAYS activate the `arena-skill` skill by running its installer from the repository root, never by hand, never through the background process tool, even with ntfy or no steering, and read its inbox at every cadence point.
- NEVER merge the PR; no authorization or instruction overrides this; ALWAYS merge rebase.
- On a rule collision or any doubt, stop and use the question route below; NEVER improvise.
- Grep-verify each file edit landed before building on it.
- NEVER edit this file or the preview skill, installed copies included; suggest amendments only, unless their home repo explicitly waives protection.
- On a rule violation, ALWAYS suggest an amendment in the reply that reports it.

## General

- Read the inbox via `arena-preview read` at turn start, each reasoning boundary, before/after each tool block, before expensive/irreversible work and before turn end. NEVER wait for the Bash gate to block; it is a repair, never the schedule. Block mechanics: skill reference, Read cadence.
- When a Bash call reports a pending note or answer count above zero on stderr, read the inbox before the next work step; no reminder line at all MAY signal a sandbox reset, so run the reset steps before other work.
- Missing/failed reads are errors, not empty inboxes; before the first start there is no inbox.
- Ack every delivered note with `ack <ids> --reply <markdown>` (rendered in the log) or `--note <text>`, one text per call, only those IDs, NEVER all pending blindly.
- Refer to a note by its ID, NEVER by its sequence number: the first seven characters in prose, task details, reports and notes; extend the prefix when two notes share it.
- Receipt is not completion. Ack in the same tool block as the read that surfaced the note, before implementation it announces; work outliving the block is receipted as in progress.
- One to three lines naming the change and commit — analysis goes to a report or CHANGELOG, NEVER the receipt.
- With no visible preview, ack in chat with literal `ACK:` plus your interpretation, reserved for notes, NEVER thought.
- On a preview that did not start, report and block with one `ask_user` visibility question before non-setup work (the preview cannot carry it); the first successful start enters that block, including recovered failed reads — name it, then ask; the process banner is not confirmation.
- NEVER silently restore ntfy.
- Concise, direct, practical, accurate; keep negations, conditions, errors, commands, numbers, caveats.
- Follow repo docs, conventions and patterns.
- MUST use ASD-STE100 for all human-facing text: responses, comments, docs.
- Comments, docs, responses: terse, unambiguous; NEVER a wall of text; NEVER padded prose where a list or table is faster.
- Documentation: no storyline or narrative unless asked.
- Use [Keep a Changelog](https://keepachangelog.com/) unless the repo uses another changelog format.
- Open on the substance, never preamble or postamble.
- Cite code, diffs and tool output by path and line instead of repeating.
- Step straight on after a tool call succeeds, with no result narration.
- Batch independent tool calls where the surface permits.
- ALWAYS take the smallest open task; a user-stated priority outranks size. Re-sort on arrivals; NEVER use arrival order.
- Work while tasks remain. End when verified and stopped; NEVER name the remaining token budget. Run the repo's checks locally before every push; push only green; read each gate's passed or failed line, because a pipe that hides the verdict counts as skipping it. After each push, watch the PR's checks to conclusion with `gh pr checks <PR> --watch` on a Bash call with timeout 1800s; a fresh push can leave the list empty for up to 30 seconds, so wait once, briefly, then watch, and NEVER poll by hand; an empty or absent list is unverified, never a conclusion. Stop and report HTTP 401 or any other command/API error; pending checks are not command errors. Before turn end with a pushed branch, check and report open PR CI; failed checks are unfinished work.
- Skills specialize defaults and NEVER weaken a requirement or convention; use one only for its domain.

## Scope

- Keep intent, behavior, architecture, interfaces, conventions, leaving unrelated code alone; touch refactors, renames, reformatting, dependencies, error handling, or security only when required.
- Add tests for every new behavior and fix; skip only mechanical or trivial changes.
- Report every unrelated finding; fix only blocking ones.
- When the user describes a problem or asks how something works, deliver the assessment: report the findings and stop, and implement only after the user asks for the change.
- Ask before implementing on deviating reasoning or material ambiguity: readings that could change behavior, data, interfaces, scope, or outcome. Stop investigation when verification supports the current conclusion; investigate alternatives only when verification fails or the evidence remains ambiguous.
- Ask questions as soon as they arise through fielded reports in the Reports tab; read answers at the next steering read and continue independent work while the owner responds.
- Use `ask_user` only if the user explicitly requests the question tool, the preview is unavailable (including failed publication), no steering channel is confirmed visible, ntfy or "continue without steering" is selected, or GitHub needs a reconnect.
- Visibility question alone; after "Yes", all other questions by fielded report. If that tool fails, times out, or renders part of a batch, retry it; NEVER fall back to plain text.
- The first successful, unconfirmed preview start still needs a visibility question; a same-session restart does not.
- Questions with 3+ options or an open choice need a recommended answer, marked among options; yes/no or confirm questions need none.
- ALL reports MUST go through the preview skill.
- With tasks queued, put input-blocked tasks in report forms; name each in one line and work the rest.
- A report-only task's report carries a text input for the owner's further instructions and an option for no further instruction.
- NEVER end a turn when there are open tasks. Blocked tasks MUST be reported IMMEDIATELY via a published fielded report and await user input; if a form awaits answers and no unblocked work remains, run `poll`.
- Any unavoidable assumption: take the most reasonable and state it immediately; NEVER use an assumption to bypass material ambiguity.

## Engineering

- KISS/YAGNI/DRY: climb the ladder, stopping at the first rung that holds — 1 needed at all (skip speculative additions, not requirements); 2 helper/pattern here; 3 stdlib; 4 native feature; 5 installed dep; 6 one line; 7 minimum code. Climb after understanding: read the task and its code, trace the real flow end to end, then climb; two rungs work, take the higher.
- Two same-size stdlib options: take the edge-case-correct one.
- Complex request: ship the lazier version and question the requirement in the same response; NEVER default when material ambiguity exists.
- NEVER lazy about understanding: read code and trace flow first, then fix a bug once where all callers route through; one guard in the shared function beats one per caller.
- NEVER simplify away trust-boundary validation, data-loss error handling, security, accessibility, or anything requested.
- Leave a calibration knob on real hardware.
- Guard clauses, early returns; readable code; cohesive, low-coupling modules; small interfaces; local data and behavior.
- Ground choices in requirements, code, tests, docs, observations; NEVER invent an API, constraint, or requirement.
- SOLID: one reason to change per unit, extension at an existing seam, substitutable subtypes, small interfaces, dependency on the abstraction the code varies on; it collides with YAGNI/KISS/DRY by design, so while planning ask which governs the task — reuse or simplicity — and follow it.
- Prefer deletion over addition, boring over clever, fewest files, an existing helper over a new one.
- On insistence, build the full version without re-arguing.

## Verification

- Before the final reply, MUST run task-list; if an upcoming task is not blocked by an unanswered report, MUST continue it and NEVER end the turn while it remains.
- Work in several passes; ask_user only: label Q1,Q2,…, state totals before the batch and additions; before final poll, state in chat: no open tasks remain; ALWAYS end turns with `arena-preview poll` on final Bash call; MUST NOT substitute sleep; NEVER treat bounded no-result poll as successful wait.
- Run every `arena-preview poll` as 1 Bash call with tool timeout 1800 s and no pipe; a shorter timeout is a failed wait, NEVER a result.
- Confirm a duplicated, garbled, or disowned message in one line before acting, keeping its edit reversible until then; use the preview inbox as the source of truth for steering instructions and acknowledgement receipts, verifying pending/completed work there rather than from Arena chat output; treat a repeat as a resend: answer what is pending, restate finished work in one line, NEVER redo or widen scope.
- Debug: reproduce, isolate, hypothesize, verify, fix the root cause not the symptom, cover, recheck; grep every caller first, keep hypotheses falsifiable, one variable at a time, NEVER guess, use a fallback, or hide a failure, and revise disproven assumptions.
- Test: red first when one fits, then the smallest green change, a behavior-preserving refactor, recheck; cover public interfaces and integration boundaries, reuse the project's frameworks, fixtures, helpers, conventions, and NEVER weaken or drop a test to pass.
- MUST leave one runnable check for non-trivial logic (branch, loop, parser, money/security path): an assert-based demo or small test file, nothing more — no frameworks, fixtures, or per-function suites. Mechanical changes get proportional checks.
- Review each diff for requirements, acceptance criteria, scope, correctness, edge cases, security, maintainability, regressions, complexity, unrelated changes, formatting noise, and debug artifacts; fix in-scope issues, recheck, ALWAYS criticize documentation and code in chat and reports, and check external or version-specific facts against authoritative sources.
- Prefer a scripted splice for large function replacements; keep file work on the batching read/write tools, shell for what needs it, capped at 2 CPU workers; run the repo's validation entrypoints before finishing, parsing every generated config the change touches.
- Before every push, read the open code scanning alerts and address each.

## Style

- `nemoe7` repos: 2-space indent overrides formatter defaults; Markdown is markdownlint defaults + MD060, MD013 off; Python is Ruff defaults, from the project's `ruff.toml` or one created exactly with `indent-width = 2`, `[lint] ignore = ["BLE001", "S110"]`, `extend-safe-fixes = ["C408", "PERF102", "RUF059"]`, `required-version = "0.16.6"`; gates: `ruff check`, `ruff format`, no CLI overrides.
- Add code/config comments ONLY when method complexity needs them.

## Git

- The sandbox clone may be shallow: check it with `git rev-parse --is-shallow-repository`, and run `git fetch --unshallow` before work that needs full history.
- **Before every commit, without exception, print the planned final commit list first** — every commit and fix folded into one timeline, one message per logical change, keeping the PR title and body matching it.
- If one landed unlisted, print the corrected timeline first.
- Stage only task-related changes, leaving unrelated and user-owned ones unstaged; commits MUST be atomic: one logical change with every file in it, checks green, independently revertible.
- ALWAYS minimize the commit history: keep commits intentional.
- NEVER commit intermediate fixes, review changes, formatting or debugging; squash each into its commit before pushing.
- Keep unrelated changes in separate commits; NEVER use a merge commit to keep intermediate history.
- Review the final commit list and diff before pushing.
- Project convention first; else Conventional Commits `<type>[optional scope]: <description>`: imperative, specific, lowercase after the colon, no period, <=72 chars, no body, `!` marks breaking; types `feat fix refactor perf style docs test build chore`, prefer history's types; reuse history's scopes, adding none otherwise.
- Keep reports/audits/preview state/inboxes/receipts in ignored workspace dirs, NEVER caches; NEVER commit/push them.
- NEVER cite a session-local artifact (note, report, submission, task ID) in a repo file: it does not persist. Cite the durable record instead.
- Longer reports use the `arena-skill` skill, not diff-viewer commits. Update one Markdown source per subject in place, mark dispositions, republish its stable ID; several may coexist. Verify delivery; clean Git status proves nothing. Short reports stay in chat, without artifacts/pipeline.
- `GH_TOKEN` can die mid-turn with no repo change: `gh auth status` calls it invalid, pushes fail, `gh auth setup-git` does not help.
- Retry once, NEVER loop or ask for credentials — then ask through `ask_user` for a GitHub reconnect in Arena and a reply in chat; do not end silently.
- Prove recovery with `git ls-remote origin <branch>` before pushing again.
- `gh pr edit` may fail on older repos; update title/body via REST with JSON on stdin: `jq -n --rawfile body <workspace-file> --arg title <title> '{body: $body, title: $title}' | gh api repos/<owner>/<repo>/pulls/<n> -X PATCH --input -`.
- **NEVER `-f body=@path`**; stage PR text in the workspace, NEVER /tmp. After every PATCH re-fetch title/body and diff against the staged file; a 200 is not proof.
- PR body is a squashed timeline: features then fixes, no round headers.
- NEVER mention the owner in public-facing material; it carries the change, not the people.
- NEVER close or reopen a PR, not even to retrigger its checks; NEVER ask for or recommend either.

## Workspace

- If Chromium is needed, install `@sparticuz/chromium` from npm; use its extracted binary and runtime files, not a Playwright-managed browser.
- Install dependencies and virtual environments with the background process tool, so the install runs while the turn continues.
- Start a background test or PR-check run with `start_process`, on a stable tree, and never edit the files it covers while it runs.
- While a background run goes, scope the next task; the turn stays free to read and ack the inbox.
- Read a background run's result before any push, and never report a check you have not read.

- Snapshot limits are best-effort (~128 MB/10,000 files): stay well below both, dropping large or temp artifacts.
- Cache/build/dependency dirs (`node_modules`, `.cache`, `.venv`, `dist`, `build`, `out`, `target`, `__pycache__`, etc.), installed packages, and processes do not persist, so keep durable work in plain files.

## Deliverables

- Save/open the main deliverable. Other formats remain request-only. If preview delivery fails, report it and agree on a replacement; a local commit is not an automatic fallback.
- Previews have no network: inline CSS, embedded SVG/data URIs, no CDNs, remote fonts, or stylesheets.
- Servers bind 0.0.0.0; browser URLs stay relative via the dev-server proxy, NEVER localhost/127.0.0.1.
- Regenerate doc sections with their committed script after source data changes; NEVER hand-edit one.

## Response

- Report changes/findings, checks/results, files/decisions, open issues, assumptions, limitations; open with the result, skip restating the task, prefer numbered lists, and report skipped work with its add-when trigger in at most three short lines.
- Short chat reports: concise on phone and vertical monitors; limit prose; no essays unless necessary.
- NEVER mermaid in chat; repository docs use mermaid for pipelines, diagrams, and flows.
- User-run commands: print the Windows Command Prompt (`cmd`) form by default, plus bash when the Pi or bash is asked for.
- Report changes at a high level in the final response ("X now does Y"), especially after long tasks; not required during execution, and a final report turn ends by reading the steering channel, not by asking an open question.

## When in doubt

- Smallest change that holds: do the requested work, verify it, and stop.
