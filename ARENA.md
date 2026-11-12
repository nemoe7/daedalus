# ARENA.md

## Use

- Arena agents: MUST apply to every chat/task/first message; explicit user chat instructions win.
- MUST apply code-style rules only in `nemoe7` repos.
- Arena agent: these rules cover every chat, task, and this repository's first message, prompt or not.

## Setup

- Bible ("bible" or "the bible", NEVER "bible files" or "required bible files"): ARENA.md, root AGENTS.md, and arena-steering SKILL.md, plus any nested AGENTS.md in a directory you work in (read in full via read_file).
- Follow the [resume-turn recipe](.agents/skills/arena-steering/references/recipes/resume-turn.md) at every turn start.
- Before first repo inspection, edit, or preview start—and on the first tool call after any session summary—MUST read ARENA.md, root AGENTS.md, and arena-steering SKILL.md in full via read_file; read nested AGENTS.md in full via read_file only when working in their directory; a session summary NEVER counts as full text in context.
- At stated read points, follow Setup bible definition/duties.
- Session start: read bible; next action `10-4: ARENA.md loaded`; install; start preview; ask visibility.
- Resumed/next turn: check preview (restart if needed); read missing-context paths in the bible; continue open task or poll.
- Follow the installed [arena-steering skill](.agents/skills/arena-steering/SKILL.md) for Steering procedures.
- MUST read required paths in the bible fully via read tool, NEVER Bash.
- After mid-turn compaction/summary/context replacement, read missing-context paths in the bible end-to-end before other tools; resumed/next turns use their start sequence.
- Every 6 preview calls and on the first call after any session summary, preview check runs; if any bible text is absent from active context (including after a summary), MUST read ARENA.md, root AGENTS.md, and arena-steering SKILL.md in full via read_file before acting, plus nested AGENTS.md only when working in their directory.
- NEVER count filename, receipt, summary, remembered rule, or partial read (head/tail/grep) as current full text.
- MUST batch required bible reads in one call; split only at harness output caps.
- MUST finish required reads end-to-end (all continuations) before queued work/pending push.
- In saved context, state the start sequence and full missing-context bible reads before queued work; no verbatim ARENA.md copy required.
- After bible changes (incl. rebase/`main` update), MUST read full diff from last-read version before next work; unavailable/unclear → read full file.
- First reply opens `10-4: ARENA.md loaded`; found later → next reply opens `10-4: ARENA.md loaded late (turn N)`.

## Constitution

- Do only requested work plus what implementing and verifying need; smallest coherent change; stop when verified.
- NEVER create a local-only tool, commit it, or add a workflow that runs it.
- NEVER claim unrun validation; report what you skipped instead.
- NEVER claim context-limit or turn-budget restrictions.
- NEVER merge the PR; no authorization or instruction overrides this.
- On a rule collision or any doubt, stop and use the question route below; NEVER improvise.
- NEVER edit read-only or instruction-protected files without explicit user approval of those files.
- NEVER edit this file or `arena-steering`, installed copies included; suggest amendments only, unless home repo waives protection.

## Precedence

- For Arena work, these rules and the `arena-steering` skill apply together, and both outrank other skills and plugins; explicit user chat instructions outrank them.
- Confirm overrides in one line; noncompliance earns a negative rating; host-repo `AGENTS.md` files stay in force beside this file, which wins collisions.

## Preview

- Before non-setup tools, install repo tooling with `pip install -e` on `nuu` when present, and await visibility.
- Start demo servers and presentations via `start_process`.
- Claim visibility only after user confirmation.
- Previews have no network: inline CSS, embedded SVG/data URIs; no CDNs/remote fonts/stylesheets.
- Servers bind 0.0.0.0; browser URLs stay relative via the dev-server proxy, NEVER localhost/127.0.0.1.

## Inbox

- Before starting or resuming queued work, MUST run `steer read` and acknowledge each pending blocking message before continuing.
- Give one short receipt for a corrected routine no-impact slip.
- Corrected routine command-input/process slips (e.g. late read) alone are not rule violations.
- Duplicated/garbled/disowned message: confirm reading in one line before acting; keep caused edits reversible until confirmed.

## Tasks and gates

- Continue after a tool call succeeds, with no result narration.
- Batch independent tool calls where the surface permits.
- Work while tasks remain; end when verified and stopped.
- MUST run `steer poll` only to wait for gates when one task remains, or for new work when no tasks remain.
- NEVER sleep during a background gate.
- NEVER call `get_process_output` to wait for a run.
- Read the verdict line before any push.

## Reports and questions

- Suggest amendments only for an identified contributing rule gap.
- Fix only unrelated findings that block the work.
- Problem description, how-it-works question, or thinking aloud: deliver assessment, stop; implement only when asked.
- Ask before implementing on deviating reasoning or material ambiguity.
- ALWAYS report uncontracted shipped changes.
- Material ambiguity means different reasonable interpretations could materially change behavior, data, interfaces, scope, or outcome.
- Ask ASAP via fielded reports (Reports tab); read answers at next steering read; affected work stops, independent work continues.
- Use `ask_user` ONLY for: explicit tool request, unavailable preview (incl. failed publish), unconfirmed steering visibility, GitHub reconnect. NEVER when a report can carry it.
- Ask the visibility question alone.
- Report a failed start and block on it before non-setup work; the banner is not owner confirmation.
- Tool fail/timeout/partial batch: retry `ask_user`; NEVER plain-text fallback.
- Ask on the first unconfirmed start in a session or branch, repairs included; a same-session same-branch restart needs none.
- NEVER end turn with open tasks: link live gates, whose run owns the blocked state without a report.
- NEVER assume while material ambiguity remains.
- Without material ambiguity, unavoidable-assumption reporting MUST override blanket ask/no-assumption/no-guess rules.
- ask_user batches only: label Q1, Q2, …, and state the total before the batch and before any addition.
- Include all required context in each handoff. NEVER require access to the sender's state or local-only files.

## Communication

- Concise, direct, practical, accurate; keep negations, conditions, errors, commands, numbers, caveats.
- MUST use `nemoizer` for all human-facing text (responses, reports, comments, docs), run every report over `nemoizer` before `steer report pub`, falling back to ASD-STE100 and Humanizer when unavailable.
- Comments/docs/responses MUST be terse, unambiguous; cut words, NEVER meaning; no walls/padding where lists/tables are faster; ≤4 sentences/paragraph; no storyline/narrative unless asked.
- Maintain changelogs per [Keep a Changelog](https://keepachangelog.com/) unless the repo uses another format.
- Open on the substance, NEVER preamble or postamble.
- Cite code, diffs and tool output by path and line instead of repeating.
- ALWAYS use the Oxford comma.
- Chat/reports MUST be concise, vertical-display readable (phone/vertical monitor), human-digestible; limit prose; essays only if strictly necessary; requested explanation is NEVER debt.
- Explain findings, bugs, decisions, and options in plain English, not internal function, table, or variable identifiers.
- Mermaid: NEVER in chat, ALWAYS everywhere else.
- For user-run commands, print Windows cmd by default, plus bash for Raspberry Pi or bash requests.
- Report changes, findings, checks, results, useful files, decisions, open issues, assumptions, limitations. Open with result; skip task restatement; prefer numbered lists; skipped work + add-when in ≤3 short lines.

## Scope

- Follow repo docs, conventions and patterns.
- Skills specialize defaults and NEVER weaken a requirement or convention; use one only for its domain.
- Keep intent/behavior/architecture/interfaces/conventions; leave unrelated code alone; refactor/optimize/redesign/rename/reformat or change deps/error handling/security only when required.
- Add tests for every new behavior and fix; skip only mechanical or trivial changes.
- Stop investigation when verification supports the current conclusion; investigate alternatives only when verification fails or the evidence remains ambiguous.
- Establish the expected contract with the user.
- MUST state each unavoidable assumption in the acknowledgement before work.

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
- On insistence, build the full version without re-arguing.
- Prefer deletion over addition, boring over clever, fewest files; search existing helpers before writing; prefer grep/sed for targeted non-bible reads over broad file chunks.

## Verification

- Prefer several passes with a re-check after each.
- Debug: reproduce, isolate, hypothesize, verify, fix root cause not symptom, cover, recheck. Grep callers before editing; falsifiable hypotheses; one variable at a time; NEVER guess, arbitrary-fallback, or hide failures; revise disproven assumptions.
- Test: red first where fitting → smallest green change → behavior-preserving refactor → recheck. Cover public interfaces/integration boundaries; reuse project frameworks/fixtures/helpers/conventions; NEVER weaken/drop tests to pass; no speculative behavior/tests. Prose-only: existing linters/structural validators; NEVER prose-wording tests; exact-text only for executable interface/protocol contracts.
- Prefer a named constant to its literal when it defines an assertion's expected value.
- MUST assert growth/presence/absence relations for pixel/config values and implementation measurements.
- MUST add each non-trivial runnable check (branch/loop/parser/money/security) to its mirrored module test; create only if absent, keep assets in fixtures/, NEVER per-fix files, frameworks, or per-function suites. Mechanical changes get proportional checks.
- Grep-verify each file edit landed before building on it.
- Review each diff for requirements, acceptance criteria, scope, correctness, edge cases, security, maintainability, regressions, complexity, unrelated changes, formatting noise, and debug artifacts; fix in-scope issues, recheck, ALWAYS criticize documentation and code in chat and reports, and check external or version-specific facts against authoritative sources.
- Before finishing, run repo validation entrypoints; parse every touched generated config.

## Style

- `nemoe7` repos: 2-space indent beats formatter defaults; Markdown: markdownlint defaults + MD060, MD013 off; Python: Ruff defaults in `pyproject.toml` (else `ruff.toml`) with `indent-width = 2`, `[lint] ignore = ["BLE001", "S110"]`, `[lint] extend-select = ["E501"]`, `extend-safe-fixes = ["C408", "PERF102", "RUF059"]`, `[lint.pycodestyle] max-line-length = 120`, `required-version = "0.17.3"` until flagged; create missing config first; run `ruff format` before other local gates, and gates `ruff check`/`ruff format` with no CLI overrides.
- Keep comments/docstrings rare, factual, and only for non-obvious external constraints or business invariants; ALWAYS use the established contract as comments.

## Git and CI

- ALWAYS push the branch, keep a PR open; disregard NEVER-push rules.
- Before pushing, ALWAYS check `origin/main`; if moved, rebase, then push `--force-with-lease`.
- ALWAYS push verified commits after each task on public repos; allow small batches (<=3 commits) on private repos.
- After each push, confirm `git ls-remote origin <branch>` = `git rev-parse HEAD`; report remote landing (subject + short hash). After sandbox reset: fetch, confirm HEAD = remote branch before edits.
- Before each push, run repo checks detached, NEVER inline; push only on pass; inline = skipped gate at any speed.
- Read each gate's passed or failed line; a pipe that hides the verdict counts as skipping it. The wait scopes the next task.
- After pushing, watch latest PR head to conclusion via `gh pr checks <PR> --watch`; replace superseded watches, keep working, read latest verdict before turn end.
- Fresh-push check list may stay empty ≤30s: wait once briefly, then watch; NEVER hand-poll.
- Stop and report HTTP 401 or any other command/API error; pending checks are not command errors.
- Empty/absent list proves nothing; failed checks = open work; PR checks = ALWAYS priority 1 over queued tasks.
- Before turn end with a pushed branch, check open PR CI.
- Before rebase, MUST check if `origin/main` absorbed branch commits; if so, `git reset --hard origin/main` and continue; NEVER replay.
- Push only with a tree different from `origin/main`; when the trees match, NEVER push.
- Sandbox clone may be shallow: check `git rev-parse --is-shallow-repository`; `git fetch --unshallow` before full-history work.
- Stage only task-related changes; leave unrelated/user-owned unstaged. Commits MUST be atomic: one logical change, all files, checks green, independently revertible.
- Prefer the shortest history that keeps every commit atomic.
- NEVER commit intermediate fixes, review changes, formatting or debugging; squash each into its commit before pushing.
- Keep unrelated changes in separate commits; NEVER use a merge commit to keep intermediate history.
- Review the final commit list and diff before pushing.
- Follow project convention first; else Conventional Commits: `<type>[optional scope]: <description>`, imperative, specific, lowercase after colon, no period, <=72 chars, no body, `!` = breaking. Types: `feat fix refactor perf style docs test build chore` (prefer history); reuse scopes, add only if none fits.
- NEVER commit or push reports, audits, preview state, inboxes, or receipts.
- NEVER cite session-local IDs (note/report/submission/task/session-minted) or local-only paths (ignored state/scratch or sandbox home) in repo files, commits, PRs, task details, or reports; strip on sight; cite CHANGELOG/repo path/commit instead.
- NEVER create report commits for the native diff viewer; mark each finding's disposition where it is recorded.
- `GH_TOKEN` can expire mid-turn, repo unchanged: `gh auth status` = invalid, push fails, `gh auth setup-git` won't help.
- Retry once, NEVER loop or ask for credentials — then ask through `ask_user` for a GitHub reconnect in Arena and a reply in chat; NEVER end silently.
- Prove recovery with `git ls-remote origin <branch>` before pushing again.
- **NEVER `-f body=@path`**; stage PR text in the workspace, NEVER /tmp. After every PATCH re-fetch title/body and diff against the staged file; a 200 is not proof.
- PR title/body MUST match squashed timeline: features then fixes; no round headers.
- NEVER mention the owner in public-facing material; it carries the change, not the people.
- NEVER close or reopen a PR, not even to retrigger its checks; NEVER ask for or recommend either.

## Workspace

- UI work: verify via screenshots from that pinned Chromium package; read rendered result before counting landed; when `nuu` is available and used for a UI task or audit, embed its screenshots, including before-and-after images, in the published report for user review.
- Start each local gate on a stable tree; NEVER edit its covered root files while it runs. A remote PR CI watch does not freeze the working tree.
- Freeze = root tree only.
- Snapshot limits are best-effort (~128 MB/10,000 files): stay well below both, dropping large or temp artifacts.
- Keep durable work in plain files; `bash` starts at repo root, so `cd` into it is redundant.

## Deliverables

- Other formats remain request-only.
- If preview delivery fails, report it and agree on a replacement.
- A local commit is not an automatic fallback.
- Regenerate generated doc sections via committed script after source-data changes; NEVER hand-edit.

## When in doubt

- Smallest change that holds: do the requested work, verify it, and stop.
- Ambiguous request: MUST ask user before implementing; NEVER guess.
