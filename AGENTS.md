# AGENTS.md

## Pull request body

A plain list of the files the pull request adds. One line per file, or per small group of
files, with a short name of what it is.

No rationale. No check output. No tables. No run commands. Analysis goes to a report.

```markdown
- `daedalus/api.py` - OpenAI-compatible proxy: `/health`, `/v1/{path}`, SSE pass-through.
- `tests/test_smoke.py`, `tests/test_config.py`.
```

## ADRs

An ADR holds one decision and the reason for it.

- Edit an ADR in place. Never add dates.
- Submit an amendment in a report first. Change the ADR only after the answer comes back.
- Say that an ADR needs an amendment before the implementation changes, not after.
- The owner dictates an ADR. Write a terse, formatted draft, publish it as a report, and
  change `docs/adr` only after the owner approves it.
