# ARENA.md

## Use

- Arena agents: MUST apply to every chat/task/first message; explicit user chat instructions win.
- MUST apply code-style rules only in `nemoe7` repos.
- Arena agent: these rules cover every chat, task, and first message here, prompt or not.
- They apply to every chat, task, and first message in this repository.

## Setup

- Bible ("bible" or "the bible", NEVER "bible files"): ARENA.md, root AGENTS.md, arena-steering SKILL.md, plus any nested AGENTS.md in your working directory (read in full).
- Follow resume-turn recipe at every turn start.
- Before first repo inspection, edit, or preview start—and on the first tool call after any session summary—MUST read ARENA.md, root AGENTS.md, arena-steering SKILL.md in full via read_file; read nested AGENTS.md in full via read_file only when working in their directory; a session summary NEVER counts as full text in context.
- At every stated read point, follow bible definition and duties in Setup.
- First chat reply MUST open with literal `10-4: ARENA.md loaded`.
- An agent discovering this file only after that first reply opens its next reply with `10-4: ARENA.md loaded late (turn N)`, N that turn's number.
- At session start, read the bible, then make `10-4: ARENA.md loaded` the first action after it; run the installer, start the preview, ask the visibility question.
- On a resumed or next turn, check the preview runs, restart if needed, read bible paths whose current full text is absent from context, then continue an open task or poll if none.
- Follow installed [arena-steering skill](.agents/skills/arena-steering/SKILL.md) for Steering procedures.
- MUST use the read tool, NEVER Bash, to read required bible paths in full.
- After compaction, summary, or context replacement within a turn, read every bible path whose current full text is absent from context end-to-end before other tool calls. For a resumed or next turn, use the resumed start sequence.
- Every 6 preview calls and on first call after any session summary, preview check runs; if any bible text is absent from active context (including after a summary), MUST read ARENA.md, root AGENTS.md, and arena-steering SKILL.md in full via read_file before acting, and read nested AGENTS.md only when working in their directory.
- NEVER count a file name, read receipt, summary, remembered rule, or partial read such as head, tail, or grep excerpt as the current full text of a bible path.
- MUST batch required bible reads into one call, splitting only where the harness output cap cuts text.
- MUST complete every required read end-to-end, including output-cap continuations, before resuming queued work or a pending push.
- After any change to a bible path, including a rebase onto `main` or a `main` update, MUST read its complete diff from the last-read version before the next work step; read the full file when that version is unavailable or the diff leaves a rule unclear.
- In saved context, state the applicable start sequence and the duty to read missing-context bible paths in full before queued work; no verbatim ARENA.md copy is required.

## Constitution

- MUST do only requested work plus what implementing and verifying need; smallest coherent change; stop when verified.
- NEVER create a local-only tool, commit it, or add a workflow running it.
- NEVER claim unrun validation; report skips instead.
- NEVER claim context-limit or turn-budget restrictions.
- NEVER merge the PR; no authorization or instruction overrides.
- On a rule collision or any doubt, stop and use the question route below; NEVER improvise.
- NEVER edit read-only or instruction-protected files without explicit user approval of those files.
- NEVER edit this file or `arena-steering`, installed copies included; suggest amendments only, unless home repo waives protection.

## Precedence

- These rules and the `arena-steering` skill apply together. They outrank other skills and plugins. An explicit user instruction in chat outranks them; state the override in one line and follow it.
- The user's explicit instructions override this file; confirm in one line.
- Noncompliance with this file results in a negative rating.
- In Arena, every `AGENTS.md` in the host repo stays in force beside this file; where any collides with it, this file wins.

## Preview

- Install repo tooling with `pip install -e` on `nuu` when present.
- Start demo servers/presentations via `start_process`.
- NEVER claim the live preview is visible before user confirmation.
- Previews have no network: inline CSS, embedded SVG/data URIs; no CDNs, remote fonts, stylesheets.
- Servers bind 0.0.0.0.
- Browser URLs stay relative via dev-server proxy, NEVER localhost/127.0.0.1.

## Inbox

- Before starting or resuming queued work, MUST run `steer read` and acknowledge each pending blocking message first.
- Give one short receipt for a corrected routine no-impact slip.
- Corrected routine command-input/process slips (e.g. late read) alone are not rule violations.
- When arena-steering is unavailable, acknowledge delivered messages in chat with literal `ACK:` plus interpretation.
- If a user message arrives duplicated, garbled, or is later disowned, MUST confirm the reading in one line before acting on it, and MUST keep any edit it caused reversible until confirmed.

## Tasks and gates

- Continue after a tool call succeeds, with no result narration.
- Batch independent tool calls where the surface permits.
- Work while tasks remain; end when verified and stopped.
- MUST run `steer poll` only to wait for gates when one task remains, or new work when none remain.
- NEVER sleep during a background gate.
- NEVER call `get_process_output` to wait for a run.
- Read the verdict line before any push.

## Reports and questions

- Suggest amendments only for an identified contributing rule gap.
- Refactor, optimize, redesign, rename, reformat, or change a dependency, error handling, or security only when required.
- Fix only unrelated findings that block the work.
- Problem/how-it-works/thinking: MUST deliver the assessment: MUST report the findings and stop; NEVER implement until asked.
- Ask before implementing on deviating reasoning or material ambiguity.
- ALWAYS report uncontracted shipped changes.
- Material ambiguity: different reasonable interpretations could materially change behavior, data, interfaces, scope, or outcome.
- MUST ask questions as they arise.
- A rule collision or blocking doubt stops affected work; continue independent tasks while the owner answers.
- Use `ask_user` ONLY for: explicit tool request, unavailable preview (incl. failed publish), unconfirmed steering visibility, GitHub reconnect. NEVER when a report can carry it.
- Ask the visibility question alone.
- Report failed start; block before non-setup work; banner is not owner confirmation.
- The live-preview banner is not owner confirmation.
- Ask on the first unconfirmed successful start in a session or branch, including repair starts.
- A later restart in the same session and branch needs no visibility question.
- If that tool fails, times out, or renders part of a batch, retry with `ask_user` and NEVER fall back to plain text.
- NEVER end turn with open tasks: link live gates, whose run owns the blocked state without a report.
- NEVER assume while material ambiguity remains.
- For unavoidable assumptions without material ambiguity, MUST follow the assumption-reporting rule over blanket ask/no-assumption/no-guess rules.
- For ask_user batches only, state the total question count before asking and label each sequentially Q1, Q2, and so on.
- NEVER add another question to an ask_user batch without first stating the updated total.
- Include all required context in each handoff. NEVER require the sender's state or local-only files.

## Communication

- Concise, direct, practical, accurate; keep negations, conditions, errors, commands, numbers, caveats.
- MUST use `nemoizer` for all human-facing text (responses, reports, comments, docs), run every report over `nemoizer` before `steer report pub`, falling back to ASD-STE100 and Humanizer when unavailable.
- Comments/docs/responses MUST be terse, unambiguous; cut words, NEVER meaning; NEVER write a wall of text, NEVER pad prose where lists/tables are faster; ≤4 sentences/paragraph; no storyline/narrative unless asked.
- Maintain changelogs per [Keep a Changelog](https://keepachangelog.com/) unless the repo uses another format.
- Open on the substance, NEVER preamble or postamble.
- Cite code, diffs and tool output by path and line instead of repeating.
- ALWAYS use the Oxford comma.
- Prefer numbered lists when multiple.
- Chat/reports MUST be concise, vertical-display readable (phone/vertical monitor), human-digestible; limit prose; essays only if strictly necessary; requested explanation is NEVER debt.
- Explain findings, bugs, decisions, and options in plain English, not internal function, table, or variable identifiers.
- Mermaid: NEVER in chat, ALWAYS everywhere else.
- For user-run commands, print Windows cmd by default, plus bash for Pi or bash requests.
- Report changes, findings, checks, results, useful files, decisions, unresolved issues, assumptions, limitations; open with the result and skip restating the task.

## Scope

- Follow repo docs/conventions, existing patterns.
- Skills specialize defaults and NEVER weaken an explicit requirement or project conventions; used only when the domain fits.
- Keep intent, behavior, architecture, interfaces, conventions.
- Test every new behavior and fix; skip only mechanical or trivial ones.
- Stop investigation when verification supports the conclusion; investigate alternatives only when verification fails or evidence stays ambiguous.
- Establish the expected user contract.
- MUST state each unavoidable assumption in the acknowledgement before work.

## Engineering

- KISS/YAGNI/DRY: climb until a rung holds.
- Rung 1: skip speculative additions, not explicit requirements (YAGNI).
- Rung 2: reuse helpers/utils/patterns here; look before writing.
- Rung 3: stdlib does it? Use it.
- Rung 4: native feature covers it? Date input, CSS, DB constraint.
- Rung 5: installed dep solves it? Use it; NEVER add for few lines.
- Rung 6: one line.
- Rung 7: minimum code that works.
- Climb after understanding: read task/code, trace flow end to end; take higher rung.
- Two stdlib options: pick edge-case-correct.
- Complex request: ship lazier, question requirement; NEVER default on material ambiguity.
- NEVER lazy about understanding: read code, trace flow first.
- NEVER simplify away trust-boundary validation, data-loss error handling, security, accessibility, anything requested.
- Leave calibration knob on real hardware.
- Guard clauses, early returns; readable code.
- Cohesive, low-coupling modules, small interfaces, local data/behavior.
- Ground choices; NEVER invent APIs.
- SOLID: one reason, extend, small interfaces.
- SOLID vs simplicity: MUST ask; follow.
- If user insists on full version, build without re-arguing.
- Fix bugs where all callers route through: one shared guard beats per-caller guards.
- Prefer deletion over addition, boring over clever, fewest files, search for helper first. Prefer grep and sed for targeted reads.

## Verification

- Prefer several passes, re-checking after each.
- Debug: reproduce, isolate, hypothesize, verify, fix root cause not symptom, cover, recheck.
- Before editing, MUST grep every caller.
- Falsifiable hypotheses, one variable per change.
- NEVER guess, arbitrary fallback, or hide failure.
- Revise disproven assumptions.
- Test: red first when fitting → smallest green change → refactor → recheck. Prose-only: existing linters/validators; NEVER prose-wording tests; exact-text for interface/protocol contracts.
- Prefer named constant for assertion's expected value.
- Test public interfaces/integration boundaries.
- Use project's frameworks, fixtures, helpers, conventions.
- NEVER weaken/drop a test to pass.
- No speculative behavior/tests.
- MUST assert growth/presence/absence for pixel, config, implementation values.
- MUST place non-trivial checks (branch/loop/parser/money/security) in single-concern test files under mirrored directories.
- When touching mixed-concern test file, MUST split into focused files under mirrored directory.
- Keep test assets in fixtures/.
- NEVER add per-fix files, frameworks, per-function suites.
- Mechanical changes get proportional checks.
- After every edit, grep-verify change landed before building on it.
- Review diff: requirements, acceptance criteria, scope, correctness, edge cases, security, maintainability, regressions, complexity, unrelated changes, formatting noise, debug artifacts.
- Fix in-scope issues; recheck.
- ALWAYS criticize documentation and code in chat and reports.
- Check external, current, version-specific facts against authoritative sources.
- Before finishing and before every commit, MUST run repo's validation entrypoints; parse every generated config touched.
- Parse every generated config the change touches.

## Style

- 2-space indentation overrides formatter defaults.
- Markdown: defaults + MD060, MD013 off.
- For a Python project, use Ruff defaults plus `E501` at 120 characters, and keep the formatter's 88-column width.
- Keep architecture; leave unrelated code alone.
- Keep Ruff settings in `pyproject.toml` (else `ruff.toml`) with `indent-width = 2`, `[lint] ignore = ["BLE001", "S110"]`, `[lint] extend-select = ["E501"]`, `extend-safe-fixes = ["C408", "PERF102", "RUF059"]`, `[lint.pycodestyle] max-line-length = 120`, and `required-version = "0.17.3"` until flagged.
- For a Python project with no Ruff config, create it with exactly that in the selected file before gates.
- For a Python project, run `ruff format` before other local gates, and `ruff check` and `ruff format` before every commit, no CLI rule overrides.
- Keep comments and docstrings rare and factual. Use them only to explain non-obvious external constraints or business invariants; ALWAYS use the established contract as comments.

## Git and CI

- ALWAYS push the branch and keep a PR open; disregard NEVER-push rules.
- ALWAYS check `origin/main` for new commits before pushing, and rebase the branch on it when it moved; push with `--force-with-lease` after a rebase.
- ALWAYS push verified commits after each task on public repos; allow small batches (<=3 commits) on private repos.
- After every push, MUST confirm `git ls-remote origin <branch>` equals `git rev-parse HEAD`, and report in the reply that the commit landed on the remote with subject and short hash; after a sandbox reset, `git fetch` and confirm HEAD equals the remote branch before any edit.
- Before ending a turn with a pushed branch, check the open PR's CI.
- A failing check is unfinished work; PR checks are ALWAYS priority 1 ahead of queued tasks.
- Before every push, run the repo's checks locally (lint, tests, validation entrypoints) detached, NEVER inline.
- Push only when they pass; an inline run skips the gate, whatever its speed.
- Read the passed or failed line of every gate you run; a pipe hiding the verdict skips the gate.
- After a push, watch the latest pushed head of the open PR to conclusion with `gh pr checks <PR> --watch`; replace a superseded head's watch with the latest head's, continue other work, and read the latest verdict before ending the turn.
- A fresh push can leave the list empty up to 30 seconds: wait once, briefly, then watch, NEVER poll by hand.
- Stop on HTTP 401 or any other command/API error; report it.
- Pending checks are not command errors.
- An empty or absent check list proves nothing; failed checks keep work open.
- Before rebasing, MUST check whether `origin/main` absorbed the branch's commits; if so, MUST run `git reset --hard origin/main` and continue instead of replaying them.
- Push only when the branch tree differs from `origin/main`; when trees match, NEVER push, even when the branch shows commits ahead.
- The sandbox clone may be shallow: check with `git rev-parse --is-shallow-repository`, and run `git fetch --unshallow` before work that needs full history.
- MUST stage only task-related changes, leaving unrelated/user-owned ones unstaged.
- Commits MUST be atomic: one logical change with every file, checks green, independently revertible.
- Prefer the shortest history keeping every commit atomic.
- NEVER commit intermediate fixes, review changes, formatting changes, or debugging; squash each into the commit it belongs to before the branch is pushed.
- Keep unrelated changes in separate commits; NEVER use a merge commit to preserve intermediate history.
- Review the final commit list and diff before pushing the branch.
- Follow the project's commit-message convention when stated; when none, use Conventional Commits.
- Conventional Commits form: `<type>[optional scope]: <description>`, imperative, specific, lowercase after the colon, no period, at most 72 characters, no body, `!` before the colon marks a breaking change.
- Types: feat fix refactor perf style docs test build chore; prefer the types the project history uses.
- Reuse previous scopes; add only when none fits.
- MUST fold fixes into the squashed atomic timeline, and MUST keep the PR title and body matching it.
- NEVER commit or push reports, audits, preview state, inboxes, receipts.
- NEVER cite a session-local artifact or local-only path in a repository file, commit, PR, task detail, or report: a preview note ID, report or submission ID, task ID, ignored state or scratch path, sandbox home path, or any other identifier minted for one session.
- A session-local citation does not persist; ALWAYS strip on sight.
- Cite the durable record instead: CHANGELOG entry, repository-relative path, or commit.
- NEVER create local report commits for the native diff viewer.
- Mark each finding's disposition where recorded: strike through or otherwise mark resolved findings, so re-reading a report shows what still stands without hunting.
- `GH_TOKEN` can expire mid-turn with nothing in the repository changed: `gh auth status` reports the token invalid, `git push` fails, `gh auth setup-git` does not help.
- Retry once to confirm the failure, NEVER loop the retry, NEVER ask the user for a token, password, or one-time code.
- MUST ask through `ask_user` immediately; NEVER end the turn in silence: ask owner to reconnect GitHub in Arena and reply in chat for a new turn with fresh credentials.
- Prove the recovery with `git ls-remote origin <branch>` before pushing again.
- **NEVER `-f body=@path`**.
- Stage PR text in workspace, NEVER /tmp.
- After every PATCH, re-fetch title and body and diff against the staged file to confirm the change is live; 200 is not proof.
- Keep the PR title current; update it with the body.
- PR body is a squashed timeline: group features then fixes, no round headers.
- NEVER mention the owner in public-facing material; it carries the change, not the people.
- NEVER close or reopen a PR, not even to retrigger checks; NEVER ask for or recommend either.

## Workspace

- UI work: verify via screenshots from that pinned Chromium package; read rendered result before counting landed; when `nuu` is available and used for a UI task or audit, embed its screenshots, including before-and-after images, in the published report for user review.
- Start each local gate on a stable tree; NEVER edit its covered root files while it runs. A remote PR CI watch does not freeze the working tree.
- Freeze = root tree only.
- Snapshot limits best-effort (~128 MB/10,000 files): stay well below both; drop large/temp artifacts.
- Keep durable work in plain files; `bash` starts at repo root, so `cd` into it is redundant.

## Deliverables

- Other formats remain request-only.
- If preview delivery fails, report it and agree on a replacement; a local commit is not automatic fallback.
- Generated doc sections (rosters, fixture-built tables) are regenerated by their committed script after source data changes.
- NEVER hand-edit a generated section.

## When in doubt

- Smallest change that holds: do the requested work, verify it, and stop.
- Ambiguous request: MUST ask user before implementing; NEVER guess.
