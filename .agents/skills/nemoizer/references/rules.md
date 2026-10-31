<!-- nonemo: NMO100; nonemo: NMO901; nonemo: NMO902; nonemo: NMO003; nonemo: STE001; nonemo: STE100; nonemo: STE200; nonemo: STE300; nonemo: STE400; nonemo: STE401; nonemo: STE901; nonemo: STE902; nonemo: STE903; nonemo: STE904; nonemo: STE905; nonemo: STE906; nonemo: STE908; nonemo: STE909; nonemo: STE910; nonemo: HMN100; nonemo: HMN200; nonemo: HMN300; nonemo: HMN400; nonemo: HMN500; nonemo: HMN600; nonemo: HMN901; nonemo: HMN902; nonemo: HMN903; nonemo: HMN904; nonemo: HMN905; nonemo: HMN906; nonemo: HMN907; nonemo: HMN908; nonemo: HMN909; nonemo: HMN910; nonemo: NMP100; nonemo: NMP101; nonemo: NMU100; nonemo: NMT100; nonemo: NMT200 -->
# Rules

This document describes the rules enforced by `nemoizer`. Each rule also has a standalone reference file (`<id>.md`) in this directory.

## Exclusions

- Use `nonemo: <id>` to suppress only the named rule on that line.
- Place `nonemo: <id>` in the first three lines with no avoided word before the marker for file scope.
- Use any comment syntax for `nonemo: <id>`.
- Run `nemoizer --ignores` to list `nonemo` markers, or `nemoizer audit` (`nemoizer collect` / `--audit`) to list both `nonemo` markers and `(manual review)` findings.

## `NMO100` - Hard word rotations

Tags: `vocabulary`, `rotations`, `blocking`

Aliases: `hard-rotation`

Fixable: Some violations can be fixed by tooling

This rule triggers when text uses an avoided word with a direct plain replacement (`transmit` -> `send`, `kind` -> `type`).

```text
Do not transmit this kind of token.
```

To fix this issue, rewrite the text as follows:

```text
Do not send this type of token.
```

Note: Each rotation matches the word stem in lower, capital, and upper case. Proper-name variations of `nemoizer` and `the nemoizer` in any case rotate to lowercase `nemoizer` with no leading `the`. Use `# nonemo: NMO100` or `<!-- nonemo: NMO100 -->` when an identifier or false friend must keep its spelling.

Rationale: Plain, consistent words are shorter and easier to scan across documentation and code.

## `NMO901` - Manual-review word rotations

Tags: `vocabulary`, `rotations`, `advisory`

Aliases: `soft-rotation`

Fixable: Some violations can be fixed by tooling

This rule triggers for manual review when text uses `pick` (`select`, or `picker` -> `selector`), `repository` (`repo`, `repositories` -> `repos`), `pull request` (`PR`, `pull requests` -> `PRs`), or `name` as a verb (`specify`, `names` -> `specify`).

```text
Pick a file from the repository, name each field, and open a pull request.
```

To fix this issue, rewrite the text as follows:

```text
Select a file from the repo, specify each field, and open a PR.
```

Note: Default checks report `NMO901` as `(manual review)` with exit code `0`; `nemoizer format` rewrites unsuppressed matches in place. Only a verb `name` rotates, so a noun `name` stays.

Rationale: Consistent terms reduce variation while keeping manual review for false friends such as `pickle`.

## `NMO902` - High "the" usage density

Tags: `style`, `conciseness`, `advisory`

Aliases: `the-density`

Parameters: `the_density_cap` (`int`; default `20`), `min_the_count` (`int`; default `3`)

This rule triggers for manual review when a prose block (paragraph, list item, blockquote, heading, or table cell) uses `"the"` at least three times and `"the"` accounts for `20%` or more of the words in that block. In flagged blocks, `nemoize.py --format` collapses repeated leading `"the"` articles in comma-separated lists.

```text
Check the status, the logs, and the metrics for the worker.
```

To fix this issue, remove unnecessary `"the"` articles where meaning stays unchanged, or run `nemoize.py --format` to collapse repeated list articles:

```text
Check status, logs, and metrics for worker.
```

Note: Default checks report `NMO902` as `(manual review)` (`advisory`) with exit code `0`. Retain `"the"` when removing it changes specificity or meaning. Write `nemoizer` in lowercase without a leading `"the"`.

Rationale: Excessive definite articles pad technical instructions and descriptions without adding information.

## `NMO003` - Missing serial comma

Tags: `punctuation`, `comma`, `blocking`

Aliases: `serial-comma`, `oxford-comma`

This rule triggers when a three-item list omits the comma before `and` or `or`, or mixes `/` and `or` in the same series.

```text
Check red, green and blue.
```

To fix this issue, rewrite the text as follows:

```text
Check red, green, and blue.
```

Rationale: The serial comma prevents ambiguity between list items in technical prose.

## `STE001` - Dangling conjunction on a bullet item

Tags: `ste`, `lists`, `blocking`

Aliases: `dangling-conjunction`

This rule triggers when a list item ends with `, and` or `, or`.

```text
- Check the configuration, and
- Start the server.
```

To fix this issue, rewrite the text as follows:

```text
- Check the configuration.
- Start the server.
```

Rationale: Each bullet item should stand as a self-contained point without trailing conjunctions.

## `STE100` - Semicolons in prose

Tags: `ste`, `punctuation`, `blocking`

Aliases: `no-semicolons`

This rule triggers when a prose line outside code spans and HTML entities contains a semicolon (`;`).

```text
Start the server; read the log.
```

To fix this issue, rewrite the text as follows:

```text
Start the server. Read the log.
```

Rationale: Separate sentences are clearer than semicolon-joined clauses in technical writing.

## `STE200` - Sentence word count

Tags: `ste`, `length`, `blocking`

Aliases: `sentence-length`

Parameters:

- `context`: `instruction` (`20` words, default) or `description` (`25` words)

This rule triggers when a covered sentence exceeds the word cap for its context.

```text
Start the local preview server from the root directory and wait until the status endpoint reports that every background worker is ready to accept connections.
```

To fix this issue, rewrite the text as follows:

```text
Start the local preview server from the root directory. Wait until the status endpoint is ready.
```

Note: Inline code spans and fenced code blocks are excluded from the word count.

Rationale: Short sentences keep instructions and descriptions unambiguous.

## `STE300` - Nominalizations with a plain verb

Tags: `ste`, `verbs`, `blocking`

Aliases: `nominalization`

This rule triggers when prose uses a noun phrase such as `make a decision` or `perform a validation` instead of a direct verb.

```text
Make a decision after you perform a validation.
```

To fix this issue, rewrite the text as follows:

```text
Decide after you validate the input.
```

Rationale: Direct verbs are shorter and state the action immediately.

## `STE400` - Wordy prepositional and modal phrases

Tags: `ste`, `plain-language`, `blocking`

Aliases: `wordy-phrase`

This rule triggers when prose uses a multi-word circumlocution such as `in order to`, `prior to`, `due to the fact that`, or `is able to`.

```text
Save the file prior to closing the window in order to keep your work.
```

To fix this issue, rewrite the text as follows:

```text
Save the file before closing the window to keep your work.
```

Rationale: Single plain words replace multi-word prepositional and modal phrases without losing meaning.

## `STE401` - Archaic and legalistic connectors

Tags: `ste`, `plain-language`, `blocking`

Aliases: `archaic-connector`

This rule triggers when prose uses an archaic or legalistic connector such as `heretofore`, `herein`, `thereof`, `thereby`, `whereby`, `aforementioned`, `pursuant to`, or `inter alia`.

```text
Update the aforementioned config and the keys therein.
```

To fix this issue, rewrite the text as follows:

```text
Update that config and its keys.
```

Rationale: Everyday connectors keep technical prose direct and understandable.

## `STE901` - Passive voice

Tags: `ste`, `verbs`, `advisory`

Aliases: `passive-voice`

This rule triggers for manual review when a sentence uses passive voice (`was published`, `is validated`).

```text
The report was published by the server.
```

To fix this issue, rewrite the text as follows:

```text
The server published the report.
```

Rationale: Active voice names the actor and reduces ambiguity.

## `STE902` - Phrasal verbs

Tags: `ste`, `verbs`, `advisory`

Aliases: `phrasal-verb`

This rule triggers for manual review when prose uses a multi-word phrasal verb (`set up`, `carry out`, `find out`) instead of a single verb.

```text
Set up the environment and carry out the check.
```

To fix this issue, rewrite the text as follows:

```text
Configure the environment and run the check.
```

Rationale: Single-word verbs translate and scan more predictably than idiomatic phrasal verbs.

## `STE903` - Ellipsis in passive coordination

Tags: `ste`, `grammar`, `advisory`

Aliases: `ellipsis`

This rule triggers for manual review when a coordinated passive clause omits the auxiliary verb (`is parsed and validated`).

```text
The state is parsed and validated.
```

To fix this issue, rewrite the text as follows:

```text
The server parses and validates the state.
```

Rationale: Complete verb forms avoid structural ambiguity in technical sentences.

## `STE904` - Compound instruction sentence

Tags: `ste`, `instructions`, `advisory`

Aliases: `compound-instruction`

This rule triggers for manual review when one sentence chains two imperative commands with `, and then`.

```text
Click save, and then click close.
```

To fix this issue, rewrite the text as follows:

```text
Click save. Then click close.
```

Rationale: One command per sentence keeps procedural steps distinct.

## `STE905` - Noun clusters

Tags: `ste`, `nouns`, `advisory`

Aliases: `noun-cluster`

This rule triggers for manual review when four or more technical nouns stack consecutively (`system server configuration management`).

```text
Update the system server configuration management module.
```

To fix this issue, rewrite the text as follows:

```text
Update the module that manages server configuration.
```

Rationale: Breaking up long noun stacks clarifies which words modify which noun.

## `STE906` - Synonym clusters and redundant pairs

Tags: `ste`, `vocabulary`, `advisory`

Aliases: `synonym-cluster`

This rule triggers for manual review when a line pairs near-synonyms (`each and every`, `checks and validations`, `null and void`).

```text
Run all checks and validations on each and every file.
```

To fix this issue, rewrite the text as follows:

```text
Run all checks on every file.
```

Rationale: Using one term per concept avoids redundancy and reader doubt.

## `STE908` - Compound tenses

Tags: `ste`, `verbs`, `advisory`

Aliases: `compound-tense`

This rule triggers for manual review when prose uses a three-part verb tense (`will have finished`, `has been running`).

```text
The job will have finished before the check starts.
```

To fix this issue, rewrite the text as follows:

```text
The job finishes before the check starts.
```

Rationale: Simple tenses keep timelines direct and easy to follow.

## `STE909` - Inline numbered or lettered lists in prose

Tags: `ste`, `plain-language`, `lists`, `advisory`

Aliases: `inline-list`

This rule triggers for manual review when a single prose line embeds an inline `(1) ... (2) ... (3)` or `(a) ... (b) ... (c)` list.

```text
The runner checks (1) formatting, (2) lint rules, and (3) unit tests.
```

To fix this issue, rewrite the text as follows:

```text
The runner checks formatting, lint rules, and unit tests.
```

Rationale: Vertical lists or simple serial lists scan faster than parenthesized inline numbering.

## `STE910` - Double negatives and litotes

Tags: `ste`, `plain-language`, `advisory`

Aliases: `double-negative`

This rule triggers for manual review when prose pairs `not` with a negated adjective (`not uncommon`, `not impossible`, `not insignificant`) or uses `never fails to`.

```text
Timeouts are not uncommon when the network is slow.
```

To fix this issue, rewrite the text as follows:

```text
Timeouts are common when the network is slow.
```

Rationale: Positive statements express conditions directly without mental inversion.

## `HMN100` - Staged opener or closer phrases

Tags: `humanizer`, `filler`, `blocking`

Aliases: `staged-frame`

This rule triggers when prose uses throat-clearing openers or sign-offs (`let's dive in`, `without further ado`, `hope this helps`).

```text
Let's dive in! Start the server.
```

To fix this issue, rewrite the text as follows:

```text
Start the server.
```

Rationale: Opening directly on the substance saves reader time.

## `HMN200` - Stacked qualifiers and intensifiers

Tags: `humanizer`, `vocabulary`, `blocking`

Aliases: `stacked-qualifier`

This rule triggers when prose stacks intensifiers (`very unique`, `completely essential`, `absolutely critical`).

```text
This check is completely essential.
```

To fix this issue, rewrite the text as follows:

```text
This check is required.
```

Rationale: Concrete requirements carry more weight than stacked intensifiers.

## `HMN300` - Inflated or promotional AI vocabulary

Tags: `humanizer`, `vocabulary`, `blocking`

Aliases: `ai-vocabulary`

This rule triggers when prose uses inflated buzzwords (`delve`, `tapestry`, `testament to`, `paradigm shift`, `game-changer`, `synergy`).

```text
Delve into the workflow architecture.
```

To fix this issue, rewrite the text as follows:

```text
Inspect the workflow architecture.
```

Rationale: Plain technical verbs communicate facts without promotional filler.

## `HMN400` - Unicode arrows in prose

Tags: `humanizer`, `punctuation`, `blocking`

Aliases: `prose-arrow`

This rule triggers when Markdown prose outside code spans and tables uses the Unicode arrow `→`.

```text
Run the build → check the output.
```

To fix this issue, rewrite the text as follows:

```text
Run the build, then check the output.
```

Rationale: Complete clauses state how two steps relate instead of compressing them into an arrow.

## `HMN500` - Chatbot courtesy and cutoff disclaimers

Tags: `humanizer`, `filler`, `blocking`

Aliases: `chatbot-courtesy`

This rule triggers when text includes assistant boilerplate (`As an AI`, `as of my last knowledge update`, `Certainly! Here is`).

```text
As an AI, I suggest running the test suite.
```

To fix this issue, rewrite the text as follows:

```text
Run the test suite.
```

Rationale: Documentation and reports should state facts directly without assistant disclaimers.

## `HMN600` - Curly quotation marks

Tags: `humanizer`, `punctuation`, `blocking`

Aliases: `curly-quotes`

Fixable: Some violations can be fixed by tooling

This rule triggers when prose uses typographic curly quotes (`“`, `”`, `‘`, `’`).

```text
Use “straight” quotes in source files.
```

To fix this issue, rewrite the text as follows:

```text
Use "straight" quotes in source files.
```

Note: `nemoizer format` replaces unsuppressed curly quotation marks with straight ASCII quotes.

Rationale: Straight ASCII quotes prevent copy-paste bugs in CLI commands, configs, and source files.

## `HMN901` - Negative contrast frames

Tags: `humanizer`, `rhetoric`, `advisory`

Aliases: `negative-contrast`

This rule triggers for manual review when a sentence uses a `not just X, but also Y` rhetorical contrast.

```text
The tool is not just fast, but also small.
```

To fix this issue, rewrite the text as follows:

```text
The tool is fast and small.
```

Rationale: Direct conjunctions state both properties without rhetorical framing.

## `HMN902` - Aphorisms and meta-importance labels

Tags: `humanizer`, `rhetoric`, `advisory`

Aliases: `aphorism`

This rule triggers for manual review when text uses meta-labels (`Key takeaway:`, `Why this matters:`, `At the end of the day`).

```text
Key takeaway: run the gate before pushing.
```

To fix this issue, rewrite the text as follows:

```text
Run the gate before pushing.
```

Rationale: State the conclusion directly instead of labeling it as important.

## `HMN903` - Rhetorical setups and strawman openers

Tags: `humanizer`, `rhetoric`, `advisory`

Aliases: `rhetorical-setup`

This rule triggers for manual review when prose opens with a rhetorical prompt (`You might be wondering`, `Have you ever wondered`).

```text
You might be wondering why the server binds port 8000.
```

To fix this issue, rewrite the text as follows:

```text
The server binds port 8000.
```

Rationale: Answering the question directly is shorter than staging a hypothetical reader question.

## `HMN904` - Repeated sentence openers

Tags: `humanizer`, `style`, `advisory`

Aliases: `repeated-opener`

This rule triggers for manual review when three consecutive sentences on a prose line share the same two-word opener.

```text
The server starts. The server listens. The server logs.
```

To fix this issue, rewrite the text as follows:

```text
The server starts, listens on port 8000, and writes to the log.
```

Rationale: Varying or combining repetitive sentence starts improves readability.

## `HMN905` - Em-dash and spaced en-dash breaks

Tags: `humanizer`, `punctuation`, `advisory`

Aliases: `prose-dash`

This rule triggers for manual review when prose or a `.user.js` UI string uses an em-dash (`—`) or spaced en-dash (` – `).

```text
Wait—then retry the request.
```

To fix this issue, rewrite the text as follows:

```text
Wait, then retry the request.
```

Rationale: Commas, colons, parentheses, or separate sentences express the clause relation more explicitly than long dashes.

## `HMN906` - Vague attributions

Tags: `humanizer`, `citations`, `advisory`

Aliases: `vague-attribution`

This rule triggers for manual review when prose appeals to unnamed authorities (`experts agree that`, `studies have shown that`).

```text
Experts agree that static checks catch bugs early.
```

To fix this issue, rewrite the text as follows:

```text
Static checks catch syntax errors before runtime.
```

Rationale: Concrete evidence or named sources are verifiable; vague appeals to authority are not.

## `HMN907` - Copula avoidance

Tags: `humanizer`, `verbs`, `advisory`

Aliases: `copula-avoidance`

This rule triggers for manual review when prose substitutes `serves as a`, `stands as a`, or `boasts a` for `is` or `has`.

```text
The script serves as a wrapper around the CLI.
```

To fix this issue, rewrite the text as follows:

```text
The script wraps the CLI.
```

Rationale: Simple verbs (`is`, `has`, or a direct action verb) avoid inflated phrasing.

## `HMN908` - Title-Case bold label bullets

Tags: `humanizer`, `lists`, `advisory`

Aliases: `bold-label-bullet`

This rule triggers for manual review when a bullet item starts with a Title-Case bold label and colon (`- **Bold Label:**`).

```text
- **Fast Startup:** Starts in 10 ms.
```

To fix this issue, rewrite the text as follows:

```text
- Starts in 10 ms.
```

Rationale: Leading directly with the fact or using a table avoids repetitive bold-label list templates.

## `HMN909` - Consecutive emojis in prose

Tags: `humanizer`, `punctuation`, `advisory`

Aliases: `consecutive-emoji`

This rule triggers for manual review when two or more emojis appear consecutively in prose.

```text
Ship the release 🚀🔥.
```

To fix this issue, rewrite the text as follows:

```text
Ship the release.
```

Rationale: Technical prose relies on words and measurements rather than decorative emoji clusters.

## `HMN910` - Document self-narration

Tags: `humanizer`, `filler`, `advisory`

Aliases: `self-narration`

This rule triggers for manual review when a section narrates itself (`This section covers`, `In this guide, we will`).

```text
This section covers the CLI flags.
```

To fix this issue, rewrite the text as follows:

```text
Pass `--format` to rewrite rotations in place.
```

Rationale: Headings already identify the topic, so self-narrating openers are redundant.

## `NMP100` - Paragraph sentence count

Tags: `length`, `paragraphs`, `blocking`

Aliases: `paragraph-length`

Parameters:

- `paragraph_cap`: maximum sentences per prose paragraph (`4` by default)

This rule triggers when a prose paragraph or table cell exceeds `paragraph_cap` sentences, when a list item exceeds 2 sentences, or when a heading exceeds 1 sentence, and flags blockquotes exceeding `paragraph_cap` sentences for manual review.

```text
First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence.
```

To fix this issue, rewrite the text as follows:

```text
First sentence. Second sentence. Third sentence. Fourth sentence.

Fifth sentence.
```

Rationale: Capping paragraphs at four sentences prevents walls of text.

## `NMP101` - Comment block line count

Tags: `length`, `comments`, `blocking`

Aliases: `comment-block-length`

Parameters:

- `comment_block_cap`: maximum physical lines per contiguous comment block (`2` by default)

This rule triggers when a contiguous block of non-directive code comments exceeds two lines.

```python
# First line of the comment.
# Second line of the comment.
# Third line of the comment.
```

To fix this issue, rewrite the text as follows:

```python
# Keep only the essential explanation within two lines.
```

Rationale: Capping code comment blocks at two lines keeps comments concise and discourages narrative blocks in source files.

## `NMU100` - Padded UI label

Tags: `ui`, `blocking`

Aliases: `padded-ui-label`

Parameters:

- `nmu100_max_words`: maximum words in a checked UI label (`6` by default)

This rule triggers when a short UI label on `title`, `aria-label`, `placeholder`, or `label` in HTML or JavaScript contains an article or filler word (`the`, `this`, `these`, `those`, `a`, `an`, `please`, `here`, `just`) and does not end with sentence punctuation (`.`, `!`, or `?`).

```html
<button title="Open the repo">Open</button>
```

To fix this issue, rewrite the label as follows:

```html
<button title="Open repo">Open</button>
```

Note: Labels longer than `nmu100_max_words`, template literals containing `$`, and explanatory sentences ending on `.`, `!`, or `?` stay skipped. Configure `nmu100_max_words` under `[lint]` in `nemoizer.toml` or `[tool.nemoizer.lint]` in `pyproject.toml`.

Rationale: Terse UI labels and tooltips scan faster and omit filler words that add no meaning.

## `NMU200` - Byte-unit divisor label mismatch

Tags: `units`, `ui`, `blocking`

Aliases: `byte-unit-mismatch`

Flag 1024-based binary divisors paired with decimal byte labels (`KB`, `MB`, `GB`, `TB`) on the same line.

Flag 1000-based decimal divisors paired with binary byte labels (`KiB`, `MiB`, `GiB`, `TiB`) on the same line.

```python
label = f"{size / 1024:.1f} KB"
```

```python
label = f"{size / 1_000_000:.1f} MiB"
```

Matching pairs such as `size / 1024` with `KiB` and `size / 1_000_000` with `MB` pass. `Mbps` is a network rate, not a byte unit.

Note: The rule checks literal divisor, shift, and power expressions on one line. It does not track named constants across files. Use `# nonemo: NMU200` for an intentional mismatch.

Rationale: Decimal and binary byte labels describe different scales and must match their conversion factors.

## `NMT100` - Prose-literal output assertion in tests

Tags: `tests`, `assertions`, `blocking`

Aliases: `prose-assertion`

Parameters:

- `test_helper`: additional test helper paths to include in static discovery

This rule triggers when a test compares a multi-word prose sentence literal (`str` or `bytes`, including HTML-wrapped prose) against CLI streams (`stdout`, `stderr`, `output`, `out`, `err`), HTTP/DOM response channels (`body`, `html`, `text`, `data`, `content`, `get_data`, `decode`, `readouterr`, `inner_text`, `text_content`, `innerHTML`, `textContent`), or local variables tainted by `WEB_CHANNELS` or `CLIENT_CALLS` (`get`, `post`, `put`, `patch`, `delete`, `open`, `walk`, `guest`).

```text
assert "The server started cleanly on port 8000" in result.stderr
```

To fix this issue, rewrite the text as follows:

```text
assert "8000" in result.stderr
```

Note: Check exit codes, structured fields, or stable identifier tokens instead of prose wording. Wildcard `per-file-ignores` on test patterns (`test*` or `*test*`) cannot suppress `NMT100`. Use line-scoped `# nonemo: NMT100` on protocol contracts only.

Rationale: Prose-wording assertions make tests brittle when log, UI, or help phrasing is edited.

## `NMT200` - Literal equality assertion on a measurement

Tags: `tests`, `measurements`, `blocking`

Aliases: `measurement-equality`

This rule triggers when a test asserts exact literal equality against a measurement call (`measure`, `token_count`, `byte_count`, `duration`, `width`, `height`).

```text
assert check.measure(path) == 42
```

To fix this issue, rewrite the text as follows:

```text
assert check.measure(path) > 0
```

Rationale: Relational or named-constant assertions keep tests resilient to legitimate size or timing changes.
