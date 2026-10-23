---
name: nemoizer
description: >
  Replace AI-flavored synonyms with the plain word, using one rotation table and
  a bundled linter that reads every text file, comments and prose included.
compatibility: Python 3.10+, with Node.js and Acorn for JavaScript checks.
---

# Nemoizer

| Avoid | Use |
| --- | --- |
| `transmit` | send |
| `pick` | select |
| `kind` | type |
| `repository` | repo |

Read references/rules.md for lint IDs and meanings.

```bash
python3 skills/nemoizer/scripts/nemoize.py .
python3 skills/nemoizer/scripts/nemoize.py --ignore vendor .
python3 skills/nemoizer/scripts/nemoize.py --ignores .
python skills/nemoizer/scripts/nemoizer collect .
```

Print each finding as `path:line: ID word -> use`; exit 1 only for mandatory findings, and mark advisory findings for manual review by agent or user. Vendored and generated
trees are skipped, and `--ignore` adds a directory or file.

`--format` rewrites the tree and prints each changed line as it was and as it
becomes:

```bash
python3 skills/nemoizer/scripts/nemoize.py --format .
```

Do not rewrite exclusion markers or the rotation table; apply only unsuppressed rotations.

- Use `nonemo: <id>` to suppress only that rule on the line.
- For file scope, place `nonemo: <id>` in the first three lines with no avoided word before the marker.
- Use any comment syntax for `nonemo: <id>`; scanning reads text, not syntax.
- Apply an exclusion only to a known matching rule ID.
- With `nemoizer collect` or `--ignores`, print every nonemo marker: path, line, rule ID, scope, and text.


Run rotations and serial-comma checks (`NMO`), semicolon/sentence/manual-review style checks (`STE`), paragraph checks (`NMP`), and test reviews (`NMT`) by default.
Limit sentences to 20 words for unclassified prose, 25 for explicit description scope.

## Prose review

- Prefer removing “the” only if phrases/sentences stay complete and natural without changing meaning.
- Replace wordy action phrases with plain verbs only when actor, timing, and meaning stay unchanged.
- Remove stock prose openers and closers only if they add no claim or required action.
- State supported claims directly unless both contrast sides carry distinct facts.
- Preserve facts, negations, conditions, numbers, names, commands, citations, and confidence in every prose rewrite.
- Remove a closer only when it repeats earlier content and adds no fact, consequence, or required action.
- Replace aphorisms or importance claims with their supported concrete claims.
- Remove objections only if they answer no real claim and carry no independent information.
- Avoid forced triads (three-item lists, three parallel examples, or three short facts plus a lesson): keep three items only if each adds a distinct fact, and merge or cut redundant ones.
- Vary repeated sentence openings only when repetition is unintended and every claim survives.
- Replace prose dashes unless the requested writing sample uses them, preserving each clause relation.
- Remove redundant qualifiers only if confidence, scope, and every safety or legal condition survive.
- Remove unsupported importance or future-success claims, retaining concrete facts and stated plans.
- Name relationships only when the source states them, otherwise retain their uncertainty.
- Keep an attached explanatory phrase only when the source supports its claim.
- Replace unsupported quality claims with supported measurements, or remove only unsupported claims.
- Name the source and its claim when available, otherwise remove only unsupported appeals to authority.
- Use is, are, or has only if it preserves the longer verb phrase's relation.
- Remove bold labels only if they add no information or necessary structure.
- Match requested quotation style without changing quoted content.
- State missing source facts without inventing plausible replacements or hiding relevant knowledge limits.
- Remove sentences that repeat their headings only if they add no information.
- Remove redundant document narration while retaining useful source credits, action-changing caveats, and change history.
- Lead contextual replies with their decisions, retaining required evidence and reasoning that can change those decisions.
- Limit instructions to 20 words and descriptions to 25 unless preserving meaning requires more.
- Rewrite noun clusters over three words when the technical term and its meaning survive.
- Keep one topic per paragraph within four sentences or the stricter project limit.
- Use the serial (Oxford) comma in lists of three or more items.
- List three or more steps or conditions, preserving their order and grouping.
- Define uncommon technical terms when needed, and use them consistently.
- Prefer simple tenses unless compound forms carry confidence or current relevance that must survive.

## Test assertions

- Assert behavior/relations, not prose wording.
- Prefer named constants for expected contract values.
- Assert pixel/config/implementation measurements through growth/presence/absence.
- Keep exact expected text only for executable/protocol/verbatim-data contracts.
