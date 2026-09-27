# AGENTS.md

## Pull request body

A plain list of the files the pull request adds. One line per file, or per small group of
files, with a short name of what it is.

No rationale. No check output. No tables. No run commands. Analysis goes to a report.

```markdown
- `daedalus/server/api.py` - OpenAI-compatible proxy: `/health`, `/v1/{path}`, SSE pass-through.
- `tests/test_smoke.py`, `tests/test_config.py`.
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
- Docs never hold narrative. State the fact, and leave out the story.
- Use lists and tables. A human reads the text, not a model.
- Write at most four sentences in a paragraph, and keep only the core idea.

## Code comments

- Write a comment only where the function is extremely complex.
- Keep a comment to two lines and three sentences.
- Keep a docstring to one line and one sentence.
- Do not refer to ADRs in code, comments, docstrings or tests.
