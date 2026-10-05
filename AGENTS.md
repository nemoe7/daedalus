# AGENTS.md

## Pull request body

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
- The owner dictates an ADR. Write a terse, formatted draft, publish it as a report, and
  change `docs/adr` only after the owner approves it.

## Human-facing text

- Lint every human-facing file with `.agents/skills/asd-ste100/scripts/ste-lint.py`. The run
  must report 0 violations, advisory findings included.
- Lint Python comments and docstrings with `scripts/lint_prose.py`.
- Lint Markdown with `npx markdownlint-cli2 "**/*.md"`. The run must report 0 issues. The
  rules are in `.markdownlint-cli2.jsonc`: the markdownlint defaults, MD060 on, MD013 off.
- Docs never hold narrative. State the fact, and leave out the story.
- Use lists and tables. A human reads the text, not a model.
- Write at most four sentences in a paragraph, and keep only the core idea.
- Give each subject its own short section: what it does, where it runs, what it writes,
  and the config names it reads. Do not write a wall of text.

## Docs workflow

- Write only facts that the code or a source shows.
- For each point that needs the owner's words or decision, add a `> Q:` line with the
  question. The owner writes the answer. Keep the question until the owner answers it.
- Use a neutral voice. Do not add a claim, an opinion or a promise that the owner did not
  make.
- Lint each changed file with `.agents/skills/asd-ste100/scripts/ste-lint.py`. The run must
  report 0 violations.
- Show each command for cmd, PowerShell and bash. Use Mermaid for diagrams.

## Dashboard pages

- A new page uses the `.flow` layout of `style.css`: the table in a `.column.wide`, and the cards in a `.column` beside it.
- Do not put cards in a row above a table.
- Update the pages simulation when code or UI changes: `scripts/pages_demo.py`, live at <https://nemoe7.github.io/daedalus/>.

## Hooks

A hook file is an optional plugin under `config/hooks`, and the points live in
`daedalus/providers/hooks.py`. The base runs with no hook file, and it loads no hook at import
time. The shipped files are examples, not a dependency.

- Keep the base free of a member that exists only for a hook. A helper, a constant or a config
  key that one hook needs belongs in that hook file.
- Keep a hook file portable. It uses the documented surface of its point and the public modules
  of the base, so it runs against any daedalus version.
- Treat a hook file as admin code: it runs in the server process with full access.

## State database

- A table change needs an Alembic step. See the State database section of `docs/architecture.md`.
- Never edit a step that is on `main`. Add a new step.

## Code comments

- Write a comment only where the function is extremely complex.
- Keep a comment to two lines and three sentences.
- Keep a docstring to one line and one sentence.
- Do not refer to ADRs in code, comments, docstrings or tests.
