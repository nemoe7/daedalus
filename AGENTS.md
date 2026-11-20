# AGENTS.md

## Development phase

The project is in development, and rapid code changes can break existing behavior.
Do not add backward-compatibility code yet. Use the current contract without compatibility layers or legacy migrations.

## Checks

Run these no matter what changed.

- Lint changed files with `.agents/skills/nemoizer/scripts/nemoizer`, which reads every text file including comments. Require 0 violations.
- Before every commit, run `python3 .agents/skills/nemoizer/scripts/nemoizer .` with `NODE_PATH=tests/e2e/node_modules`. Require 0 violations.
- Run `ruff format .` then `ruff check .` before every commit, and require a clean report.
- Lint Markdown with `npx markdownlint-cli2 "**/*.md"`. Require 0 issues.
- Keep Markdown config in `.markdownlint-cli2.jsonc`: markdownlint defaults, MD060 on, and MD013 off.

## Human-facing text

- Docs state facts, and never hold narrative or story text.
- Use lists and tables, since humans read this text, not models.
- Write at most four sentences in a paragraph, and keep only one core idea.
- Give each subject its own short section stating what it does, where it runs, what it writes, and which config keys it reads. Do not write a wall of text.
- Write only facts that code or a primary source shows.
- Use a neutral voice. Do not add a claim, opinion, or promise without owner approval.
- Add a `> Q:` line for each point that needs owner words or an owner decision. Keep it until owner input answers.
- Show each command for cmd, PowerShell, and bash. Use Mermaid for diagrams.

## Code comments

- Write a comment only where the function is extremely complex.
- Keep a comment to two lines and three sentences, and a docstring to one line and one sentence.
- Preserve the required multiline format of plugin metadata and parser-required tool parameter fields.
- Keep a tool docstring's prose summary to one line and one sentence.
- Do not refer to ADRs in code, comments, docstrings, or tests.

## Front-end work

- Read and apply `.agents/skills/nuu/SKILL.md` before front-end work.
- Check keyboard focus, contrast, reduced motion, touch targets, and horizontal overflow.
- Match surrounding design patterns for focused fixes, and avoid unrelated redesigns.

### Dashboard pages

- A new page uses `.flow` in `style.css`. Put its table in `.column.wide`, and put cards in `.column` beside it.
- Do not put cards in a row above a table.
- Reuse an existing shared UI component before adding page-specific markup, behavior, or CSS. When no component fits and at least two callers share the contract, add one shared component and migrate those callers, keeping one-off UI local.
- For each new UI component, reuse a suitable key in `daedalus/dashboard/ui/en.js` or add a semantic key. Do not add an English fallback in the component.
- Update the local demo in `scripts/pages_demo.py` when code or UI changes.
- Keep the demo local on port 8001, and claim no public Pages deployment.

## Hooks

A hook file is an optional plugin in the folder that `hooks.dir` names under `config`, and the
surfaces live in `daedalus/providers/hooks.py`. The base runs with no hook file and loads no hook
at import time, and the shipped files are examples, not a dependency.

- Give an installed hook file a `# ---` frontmatter block, and bump its `version:` line on every
  change to that file. The manager reads the block before any run.
- Keep the base free of members that exist only for a hook. A helper, constant, or config key that
  one hook needs belongs in that hook file.
- Keep a hook file portable. It uses the documented surface of its hook and the public modules
  of the base, so it runs against any daedalus version.
- Treat a hook file as admin code: it runs in the server process with full access.

## Open WebUI plugins

- Bump the `version:` line of a plugin file on every change to that file.
- Read the version of the file on `main`. Increase its last number by 1, so a branch keeps 1 bump and not 1 for each commit.

## Pools

Each chat pool is a gateway to one tier. Rule of thumb:

| Pool | Tier | Rule of thumb |
| --- | --- | --- |
| moros | TIER-D | small |
| koinos | TIER-C | small but more capable |
| deinos | TIER-B | weak thinkers |
| sophos | TIER-A | strong thinkers (also check user feedback on models when considering for TIER-A) |

## State database

- A table change needs an Alembic step. See the State database section of `docs/architecture.md`.
- Never edit a step that is on `main`. Add a new step.

## ADRs

An ADR holds one decision and the reason for it.

- Edit an ADR in place. Never add dates.
- Submit an amendment in a report first. Change the ADR only after the answer comes back.
- Say that an ADR needs an amendment before the implementation changes, not after.
- Owner input defines each ADR. Write a terse, formatted draft, publish it as a report, and change `docs/adr` only after owner approval.
