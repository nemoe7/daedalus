# AGENTS.md

## Development phase

The project is in development. Code changes are rapid and can break existing behavior.
Do not add backward-compatibility code yet. Use the current contract without compatibility layers or legacy migrations.

## PR body

- Use the H2 sections Summary, Changes, and Validation in that order.
- Breaking Changes and Related follow in that order when present. Omit either section when empty.
- Summary holds one paragraph, not a list.
- Changes holds dash bullets with text.
- Validation holds `- [x]` or `- [ ]` items, then one space and text. Check an item only after its check runs.
- A present Breaking Changes or Related section holds dash bullets with text. Do not include `None` or empty headings.
- If any commit uses `!`, Breaking Changes must list the breaking change.
- Add no other H2 or H3 headings, HTML comments, preamble, or trailing content.
- Permit angle brackets only inside fenced code blocks.
- Use the trusted base copy of `scripts/check_pr.py` in CI, not the PR copy.
- Put analysis in a report, not the PR body.

```markdown
## Summary

State the change in one paragraph.

## Changes

- State a concrete change.

## Validation

- [x] State a check that ran.
- [ ] State a check that did not run.

```

## ADRs

An ADR holds one decision and the reason for it.

- Edit an ADR in place. Never add dates.
- Submit an amendment in a report first. Change the ADR only after the answer comes back.
- Say that an ADR needs an amendment before the implementation changes, not after.
- Owner input defines each ADR. Write a terse, formatted draft, publish it as a report, and change `docs/adr` only after owner approval.

## Human-facing text

- Lint every human-facing file with `.agents/skills/nemoizer/scripts/nemoizer`. Each run must report 0 violations, advisory findings included.
- Lint Python comments, docstrings, and page paragraphs with `scripts/lint_prose.py`.
- Lint Markdown with `npx markdownlint-cli2 "**/*.md"`. Each run must report 0 issues. Config rules live in `.markdownlint-cli2.jsonc`: markdownlint defaults, MD060 on, and MD013 off.
- Review every human-facing edit to `AGENTS.md` with `.agents/skills/nemoizer/SKILL.md`.
- Run `.agents/skills/nemoizer/scripts/nemoizer AGENTS.md` after each edit. Each run must report 0 violations.
- Docs never hold narrative. State facts, and leave out story text.
- Use lists and tables. Humans read this text, not models.
- Write at most four sentences in a paragraph, and keep only one core idea.
- Give each subject its own short section. State what it does, where it runs, what it writes, and which config names it reads. Do not write a wall of text.

## Docs workflow

- Write only facts that code or a primary source shows.
- For each point that needs owner words or an owner decision, add a `> Q:` line with that question. Owner input supplies the answer. Keep that question until an answer arrives.
- Use a neutral voice. Do not add a claim, an opinion, or a promise without owner approval.
- Lint each changed file with `.agents/skills/nemoizer/scripts/nemoizer`. Each run must report 0 violations.
- Show each command for cmd, PowerShell, and bash. Use Mermaid for diagrams.

## Front-end work

- Read and apply `.agents/skills/nuu/SKILL.md` before front-end work.
- For a dashboard audit, inspect every route at 1440, 768, and 390 pixels. Capture and review screenshots.
- Check keyboard focus, contrast, reduced motion, touch targets, and horizontal overflow.
- Match surrounding design patterns for focused fixes. Avoid unrelated redesigns.

## Dashboard pages

- A new page uses `.flow` in `style.css`. Put its table in `.column.wide`, and put cards in `.column` beside it.
- Do not put cards in a row above a table.
- Reuse an existing shared UI component before adding page-specific markup, behavior, or CSS. When no component fits and the same contract has at least two current callers, add one shared component. Migrate those callers in the same change. Keep one-off domain UI local.
- For each new UI component, reuse a suitable key in `daedalus/dashboard/ui/en.js` or add a semantic key. Do not add an English fallback in the component.
- Update the local demo in `scripts/pages_demo.py` when code or UI changes.
- Keep the demo local on port 8001. Do not claim a public Pages deployment.

## Open WebUI plugins

- Bump the `version:` line of a plugin file on every change to that file.
- Read the version of the file on `main`. Increase its last number by 1, so a branch keeps 1 bump and not 1 for each commit.

## Hooks

A hook file is an optional plugin in the folder that `hooks.dir` names under `config`, and the
surfaces live in `daedalus/providers/hooks.py`. The base runs with no hook file, and it loads no hook
at import time. The shipped files are examples, not a dependency.

- Give an installed hook file a `# ---` frontmatter block, and bump its `version:` line on every
  change to that file. The manager reads the block before any run.
- Keep the base free of a member that exists only for a hook. A helper, a constant, or a config
  key that one hook needs belongs in that hook file.
- Keep a hook file portable. It uses the documented surface of its hook and the public modules
  of the base, so it runs against any daedalus version.
- Treat a hook file as admin code: it runs in the server process with full access.

## Pools

Each chat pool is a gateway to one tier. The rule of thumb for the tiers:

| Pool | Tier | Rule of thumb |
| --- | --- | --- |
| moros | TIER-D | small |
| koinos | TIER-C | small but more capable |
| deinos | TIER-B | weak thinkers |
| sophos | TIER-A | strong thinkers (also check user feedback on models when considering for TIER-A) |

## State database

- A table change needs an Alembic step. See the State database section of `docs/architecture.md`.
- Never edit a step that is on `main`. Add a new step.

## Code comments

- Write a comment only where the function is extremely complex.
- Keep a comment to two lines and three sentences.
- Keep an ordinary docstring to one line and one sentence.
- Preserve the required multiline format of plugin metadata and parser-required tool parameter fields.
- Keep a tool docstring's prose summary to one line and one sentence.
- Do not refer to ADRs in code, comments, docstrings, or tests.
