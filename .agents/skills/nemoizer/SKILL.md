---
name: nemoizer
description: >
  Find AI-flavored synonyms and replace them with the plain word, using one
  rotation table and a bundled linter that reads every text file, comments and
  prose included. Use when naming a variable, field, parameter, key, or prose
  term, when a draft sounds machine-written, or when asked to run the rotation
  table.
compatibility: Python 3.11+, with Node.js and Acorn for JavaScript checks.
---

# nemoizer

One table, one plain word per row. Read the table before you name something, and
run the linter before you hand work over.

## Rotation table

| Avoid | Use |
| --- | --- |
| `transmit` | send |
| `pick` | select |
| `kind` | type |
| `name` | specify |
| `repository` | repo |
| `pull request` | PR |
| `the nemoizer` | nemoizer |

Read references/rules.md for lint IDs and their meanings. `name` rotates only as a verb.

## Linter

Run it from the repository root and pass the paths to read:

```bash
python3 skills/nemoizer/scripts/nemoize.py .
python3 skills/nemoizer/scripts/nemoize.py --ignore vendor --ignore third_party .
```

- Every text file is read, including comments and Markdown prose.
- Print each finding as `path:line: [ID] (violation|review) description` with its indented source line and a `Summary` footer; exit 1 only for violations, and have the agent or user inspect review findings.
- Skip vendored and generated trees. Configure repo or `[[files]]` glob rules, exclusions, and caps in `nemoizer.toml` or `pyproject.toml`.

- Run word rotations and serial-comma checks (`NMO`), semicolon, sentence-length, nominalization, and manual-review style checks (`STE`), human-prose checks (`HMN`), paragraph-length checks (`NMP`), UI and byte-unit divisor checks (`NMU`), and test-assertion reviews (`NMT`) by default.
- Use decimal `KB`, `MB`, `GB`, and `Mbps` for storage marketing, network rates, and SI data, and binary `KiB`, `MiB`, and `GiB` for OS file sizes, RAM, caches, and buffers.
- Use 20 words per sentence for unclassified prose and 25 for an explicit description scope.

## Writing the rotation

`--format` rewrites the tree in place and prints every changed line twice, as it
was and as it becomes:

```bash
python3 skills/nemoizer/scripts/nemoize.py --format .
```

Each change prints as a pair: `path:line: -` and the old line, then `path:line: +`
and the new one.

- NEVER rewrite exclusion markers or the rotation table, and apply only unsuppressed rotations and straight-quote replacements for curly quotation marks.
- Pass a license file or a generated tree to `--ignore`; the tool reads every other
  text file.

## Ignore markers

- NEVER add a # nonemo suppression without explicit user authorization.
- Use `nonemo: <id>` to suppress only the named rule on that line.
- For file scope, place `nonemo: <id>` or `nemoizer: key=value` in the first three lines with no avoided word before the marker.
- Use any comment syntax for `nonemo: <id>`; the scanner reads text, not syntax.
- Apply an exclusion only to a known matching rule ID.
- Print every nonemo marker with its path, line, rule ID, scope, and text when `nemoizer collect` or `--ignores` is used, and print every `nonemo` marker and manual-review finding when `nemoizer audit` or `--audit` is used.
- Review target prose manually against the Prose rules and check documentation against code, CLI flags, configs, and paths for docs drift alongside `nemoizer audit` output, format audit reports with the [audit report template](references/audit-report.md) using one section per file (`Rule | Before | After | Verdict | Reason`), show the rule applied in `After` even when `Verdict` is `keep`, omit empty sections, and state 0 in Counts.

```bash
python skills/nemoizer/scripts/nemoizer collect .
python3 skills/nemoizer/scripts/nemoize.py --ignores .
python skills/nemoizer/scripts/nemoizer audit .
python3 skills/nemoizer/scripts/nemoize.py --audit .
```

Delete a marker when the line no longer needs it.

## Prose review

- Prefer removing "the" when the meaning stays unchanged.
- Replace wordy action phrases with plain verbs only when actor, timing, and meaning stay unchanged.
- Remove stock openers and closers from prose only when they add no claim or required action.
- State the supported claim directly unless both sides of a contrast carry distinct facts.
- Preserve facts, negations, conditions, numbers, names, commands, citations, and confidence in every prose rewrite.
- Remove a closer only when it repeats earlier content and adds no fact, consequence, or required action.
- Replace an aphorism or claim of importance with its supported concrete claim.
- Remove an objection only when it answers no real claim and carries no independent information.
- Keep each list item that adds a distinct fact, and merge only redundant items.
- Vary repeated sentence openings only when repetition is unintended and every claim survives.
- Replace prose dashes unless the requested writing sample uses them, preserving each clause relation.
- Remove a redundant qualifier only when the remaining wording preserves confidence, scope, and every safety or legal condition.
- Remove unsupported claims of importance or future success while preserving concrete facts and stated plans.
- Specify a relationship only when the source states it, otherwise retain its uncertainty.
- Keep an attached explanatory phrase only when the source supports its claim.
- Replace an unsupported quality claim with a supported measurement, or remove only the unsupported claim.
- Specify the source and its claim when available, otherwise remove only unsupported appeals to authority.
- Use is, are, or has only when it preserves the relation expressed by a longer verb phrase.
- Remove bold labels only when the labels add no information or necessary structure.
- Use straight quotation marks (`"` and `'`) instead of curly quotation marks, and avoid `→` arrows and consecutive emojis in Markdown prose.
- State a missing source fact without inventing a plausible replacement or hiding a relevant knowledge limit.
- Remove a sentence that repeats its heading only when it adds no information.
- Remove redundant document narration while retaining useful source credits, action-changing caveats, and change history.
- Lead with the direct answer, NEVER restate the question, and order needed explanations as conclusion, reason, and action.
- Limit instructions to 20 words and descriptions to 25 unless a longer form is necessary to preserve meaning.
- Put one instruction in each sentence unless simultaneous actions require a single sentence.
- Rewrite noun clusters longer than three words when the rewrite preserves the technical term and its meaning.
- Keep one topic per paragraph within four sentences or the stricter project limit.
- Use the serial (Oxford) comma in lists of three or more items, and keep list separators consistent instead of mixing `/` and `or` in the same series.
- Use a list for three or more steps or conditions while preserving their order and grouping.
- Define an uncommon technical term when needed, and use that term consistently.
- Prefer simple tenses unless a compound form carries confidence or current relevance that must survive.
- Add comments or docstrings only for non-obvious logic, use the established contract, and cap blocks at two lines.
- Match length and structure to task complexity, omitting unrequested examples, sections, tables, background, and follow-ups.
- Provide requested finished text without meta commentary.

## Test assertions

- Assert test behavior and relations instead of prose wording.
- Prefer named constants to literals when they define expected contract values.
- Assert pixel, configuration, and implementation measurements through growth, presence, or absence relations.
- Keep exact expected text only for executable, protocol, or verbatim-data contracts.

## Learn

- On `/nemoizer learn`, survey repo prose patterns, cite target rules, and publish a maintainer handoff.

