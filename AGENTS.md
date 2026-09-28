# AGENTS.md

## Pull request body

A plain list of the files the pull request adds. One line per file, or per small group of
files, with a short name of what it is.

No rationale. No check output. No tables. No run commands. Analysis goes to a report.

```markdown
- `daedalus/server/api.py` - OpenAI-compatible proxy: `/health`, `/v1/{path}`, SSE pass-through.
- `tests/server/test_smoke.py`, `tests/config/test_config.py`.
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

## Docs workflow

- Write only facts that the code or a source shows.
- For each point that needs the owner's words or decision, add a `> Q:` line with the
  question. The owner writes the answer. Keep the question until the owner answers it.
- Use a neutral voice. Do not add a claim, an opinion or a promise that the owner did not
  make.
- Lint each changed file with `.agents/skills/asd-ste100/scripts/ste-lint.py`. The run must
  report 0 violations.
- Show each command for cmd, PowerShell and bash. Use Mermaid for diagrams.

## Code comments

- Write a comment only where the function is extremely complex.
- Keep a comment to two lines and three sentences.
- Keep a docstring to one line and one sentence.
- Do not refer to ADRs in code, comments, docstrings or tests.
