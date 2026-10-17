# Preview transport: commands and recovery

Run every command as the bare `arena-preview`; the script under the installed `arena-skill` skill is `scripts/preview.py`. The state directory uses `ARENA_PREVIEW_STATE_DIR` when set, and repository `arena-state` otherwise. Keep the same ignored, persisted directory across CLI calls and server restarts. The installed wrapper finds the repository from its own path, so every CLI call works from any working directory and needs no `cd`.

## Commands

| Command | Use |
| --- | --- |
| `init [--tz <zone>]` | Create a missing state database without starting the server. A zone names where CLI stamps print, and the TZ env wins |
| `serve --port 8000` | Start the shared preview with `start_process`. The gate warns when a serve names a port other than 8000 |
| `read` | List every pending note and report answer; mark only delivered IDs Seen, and stamp the parent report read by the agent |
| `skills` | List installed and repository skill names, full descriptions, and paths; prefer installed duplicates; do not load skills |
| `note-list [--page N] [--page-size N]` | List stored note IDs, including acknowledged and quiet notes, newest first; default page 1, 20 IDs per page, maximum 100; leave receipts and exports unchanged |
| `note <id>` | Reread a stored note by its six or full characters; a short ID that matches several notes prints every match |
| `key` | Print the recorded agent key, host and stamp; breaks while the preview server is not up, and a later session recovers the key after acknowledging the quiet note |
| `poll` | Wait for a pending inbox item before ending a turn; return at once with the task list for an unblocked upcoming task, and at once when the owner pressed Skip poll in the page. It prints a retry disclaimer at the start that names the 1800-second span. The shell `timeout` command must not wrap it; give the bash tool's timeout 1800 instead |
| `route <name> -- <command>` | The gate's helper for a detected long run, and the call a failed start names: it starts the command detached under a name and records its tail and exit, so the verdict prints on a later call as `run <name> done: exit N`, with one status line per call while it runs |
| `logs <name>` | Print a routed run's status and its recorded output tail; it works while the run lives and after its verdict, and a second read before that verdict exits 130 |
| `worktree start --gate <gate-name> [--allow <glob>] <name> <destination>` | Create a detached task worktree outside the root while the named root gate runs |
| `worktree collect <name>` | Print the allowed detached-worktree patch after a green root gate |
| `ack <markdown>` | Omit IDs only when exactly one non-quiet note or report answer is pending. Supply one Markdown text. With multiple pending items, supply the ID |
| `ack <id> <markdown>` | Answer one delivered ID with a rendered reply; single-quote inline text |
| `ack <id> -` | Read the Markdown answer from stdin, with a workspace file redirected into the command |
| `task-list [-f]` | List upcoming task IDs, order, status, report links, set type marks and each linked report answer with its stamp and text. With -f, include finished tasks. Use task ID for details |
| `task ID` | Print one stored task: its title, status, order, primary type when set, report link, blocked mark and every detail |
| `task ID TITLE [DETAIL ...]` | Add or update a task; every task carries at least one detail, and reject a write that leaves none; one task holds one job, so several jobs mean several tasks rather than one task with many details; `--msg-id` is optional and only a note-born or report-born task carries it; `--blocked` or `--unblocked` sets the blocked mark |
| `task-remove ID` | Remove a task entered by mistake |
| `import-state [FILE]` | Import copied NDJSON or JSON notes, tasks and report answers from a file or stdin in one transaction; it replaces the live notes, answers, reports and tasks unless `--merge` keeps them; `--replace-tasks` replaces only tasks |
| `download-request <url>` | Queue one pending file download for the owner to approve in Downloads; `--allow-proxy` permits the two proxies |
| `image-data-uri <path>` | Print one Markdown line carrying a local PNG, JPEG, GIF or WebP file as a data URI; the command checks the type and a 2 MiB size cap before encoding |
| `publish <source.md> --id <id> --title <title>` | Publish or update a rendered report. Publication embeds readable local images as data URIs, skips remote sources with a helper hint, and keeps links and data URIs |
| `unpublish <id>` | Remove a report from the tab, retaining answers and source. Unacknowledged answers or unseen answer receipts block the call with 409. A fresh owner view does not block agent unpublish, and the page retains the open report until the owner clicks away |
| `report unpublish --stale` | Recheck eligibility, then unpublish only stale reports through existing guards; retain sources and answers |
| `stale-reports` | List stale published report IDs and reasons without changing state |
| `report-state` | Print publication, owner view stamp, staleness and answer visibility for one report or all |
| `rename <id> <new-id>` | Rename one stored note, report answer, report or task in place, so its answers, order and stamps survive |
| `clear-state` | Empty every state table in place; preserve the agent key, refresh the save file to match, and keep a running server's page token and recorded port |
| PR title/body | Update PR title/body via REST with JSON on stdin: `jq -n --rawfile body <workspace-file> --arg title <title> '{body: $body, title: $title}' \| gh api repos/<owner>/<repo>/pulls/<n> -X PATCH --input -` |

- Every command except `init` and `serve` breaks with a restart line while no preview answers, so the agent starts the server first.
- Use complete IDs in CLI calls; cite their first six characters in prose.
- Read note, answer and task content through the CLI alone: use `read`, `note ID`, `note-list`, `task ID` and `task-list`; use report HTTP endpoints only for required publication checks.
- Treat `state.sqlite3` as the store, never as a read path.
- `read` does not acknowledge an item.
- Supply one Markdown text to `ack`; use separate calls for different answers.
- A repeated `ack` on an ID appends one more reply block and keeps the earlier ones.
- The same on a submission ID appends reply blocks to its receipt.
- An unknown ID fails the whole receipt batch.
- Answer later submissions under their own IDs.
- If the preview is unavailable, use `ACK:` in chat for delivered notes.

Refer to a note in prose by the first six characters of its ID. Use the full ID in CLI calls. Check a note reference against the ID returned by `read` before sending it.

When an acknowledgement refers to a report or task, use `ack` and put its exact full ID in inline backticks; the six-character shortening applies to note IDs only. Use IDs unique across notes, reports and tasks so the client can link them; `publish` and `task` refuse an ID the other holds.

## Chromium

- Install `@sparticuz/chromium` from npm for Chromium work.
- Extract `al2023.tar.br` with the package's exported `inflate`.
- Call `setupLambdaEnvironment("/tmp/al2023/lib")` for library and font paths.
- Launch the package's extracted binary.
- NEVER use a Playwright-managed browser.

## External channel (ntfy)

- Use this only after the owner selects the external channel.
- The owner supplies a topic `<repo>-<branch>-<8-char unguessable secret>` (sanitize the branch) and posts notes to its URL.
- At each steering read, use page-fetch on `https://ntfy.sh/<topic>/json?poll=1&since=<marker>`.
- Start with `since=all`.
- Then use the newest `event:"message"` ID as the marker in `<state-dir>/ntfy-since.txt`; ignore `open` and `keepalive` events.
- An empty response, or a first 500 with no message body on a new topic, is quiet until the owner posts.
- For other JSON failures, try the HTML topic page, then a `since=all` replay.
- Read error bodies: retry an upstream error once.
- If it repeats, tell the owner the error and a fresh topic URL, then read that topic with `since=all`.
- If the new topic repeats the error, stop for this turn and retry after the owner's next message.
- Report a repeated 500 on an established topic once and retry at the next read.
- Do not use sandbox HTTP to poll ntfy.

## Read cadence

- Read the inbox when a Bash call reports a pending note, answer or upload count on stderr, before the next work step, unless that call already printed those items in full; acknowledge the delivered IDs before another read.
- NEVER wait for the Bash gate to block before a read.
- When ending a turn or a report form awaits answers, run `poll`.
- NEVER set or export `_arena_preview_platform`; repair a blocked gate, NEVER bypass it.
- After a blocking-only call, run `read` if it did not print the pending items in full; a `poll` that printed them counts as the read, so acknowledge those IDs without a second read.
- Initial discovery may precede the first read; startup MUST.
- A blocked call that runs `tail`, `head` or `grep` hears which commands to drop.
- A call that spells the preview path hears the bare `arena-preview` form once a shell.
- A call that reads code-scanning alerts or workflow run logs hears the proxy route once a shell.
- A call the gate detects as a long run hears on stderr that the run continues detached, with one status line per later call and the verdict once at the end.
- A blocked push says that an ack clears it.

The page's Message log toolbar carries a Skip poll button. A listing that carries a `skip_poll` stamp reports it: the poll that finds it consumes it, prints `SKIP: owner pressed Skip poll. End the turn, no second poll.` on stdout, and the turn ends there. Never poll again after a skip. While a detached run has no exit, the press waits: the poll runs its span, the stamp stays armed, and the run's finish breaks the wait first.

## The gate's cases

The profile gate and the EXIT hook beside it hold one case per shape below. Every case carries a label in `install.sh`, and a test compares this table with those labels.

| Case | Trigger | Answer |
| --- | --- | --- |
| `pr-state` | visible `gh pr close`, `gh pr reopen`, `gh pr merge`, equivalent API writes, or destructive head-ref operations | `exit 130` before bypasses. A read line skips the veto spawn. Reads, comments, title/body edits, and permitted working-branch pushes pass |
| `shell-shape` | a caller that is not `bash`, `sh`, `dash`, `ksh` or `zsh` | pass, so a hosted process keeps its shell |
| `shell-line` | every gated shell | the command line is read once per shell, so a command costs no read |
| `platform-shell` | the platform's probe or its own hosted process | pass, and mark the shell once |
| `task-reminder` | `git commit`, `git push` or `gh pr checks` | the task-list notice once per shell, then the line runs |
| `main-moved` | `git commit` or `git push` | the drift check once per shell, from local refs at the commit line and with a fresh fetch at the push line, then the line runs |
| `route-marker` | a shell the gate started itself (`ARENA_PREVIEW_ROUTE=1`) | pass, so a detached run never matches its own family |
| `idle-wait` | a line that names both a `sleep` and an `arena-preview` call | `exit 130` before the sleep, so the turn takes queued work. A sleep on its own runs |
| `long-run` | `gh pr checks`, `gh run watch`, or a test runner head (`pytest`, `node --test`, `npm test`, `make test`, `go test`, `cargo test` and the rest) | the call ends clean and the run continues, or `exit 130` when the start fails. Any run of 10 seconds or more is a long run too, and MUST use `route` |
| `bare-path` | a line that spells out the installed `preview.py` or `arena-preview` | the bare-call notice once per shell, then the line runs |
| `proxy-route` | a read of code-scanning alerts or workflow run logs | the proxy notice once per shell, then the line runs |
| `ack-ticks` | an `ack` whose inline text holds a backtick inside double quotes | the warning lands before the call runs |
| `serve-port` | a `serve` on a port other than 8000 | the warning lands before the call runs |
| `egress` | `curl`, `wget`, `git clone`, `git fetch` or `git ls-remote` | the egress notice lands before the call runs |
| `commit-id` | a `git commit` whose staged message carries a session ID | `exit 130`, with the message unchanged |
| `inert-line` | a line of the families the gate ignores | pass, with no count and no stamp |
| `push-checkpoint` | a `git push` with a pending inbox | `exit 130` until the ack |
| `inbox-line` | a line whose every piece is an inbox call | pass, whatever the call count holds |
| `call-count` | a line over the call threshold with a pending inbox | `exit 130` until the ack |

A blocked call repeats its reason on stderr, and every case above answers one bash call: the gate never holds the turn.

Every message carries one class. The prefix names the class, and the class fixes the stream and the exit code.

| Class | Prefix | Stream and exit |
| --- | --- | --- |
| advisory | `arena-preview gate:` | stderr, 0, and the call still runs |
| refusal | `Preview error:` | stderr, 1 |
| verdict | an all-caps opener, such as `WAIT BLOCKED:` or `READ INBOX NOW.` | stdout for the gate and a poll, stderr for a wait, 130 or 1, and 0 when a poll line hands the turn back |
| hint | `arena-preview:` | stderr, 0, or 2 from the wrapper |

A detached `gh pr checks` wait first looks for a check on the branch head, once every five seconds for up to 120. The watch runs only after a check appears, and a window that runs out, a moved head or a gh fault fails the run.

## Tasks

Task IDs have 1–64 lowercase letters, digits or hyphens and start with a letter or digit; use a short kebab-case title. Titles have at most 200 characters. A task has at most 40 details of 2000 characters each. Existing IDs update; omitted fields keep stored values.

### Task types

Choose the primary deliverable type: `report` for findings or assessments, `code` for source or configuration changes, `docs` for documentation or rule text, `ops` for repository, environment, build, test, or release operations, and `misc` for other work.

### Options

| Flag | Use |
| --- | --- |
| `--status upcoming` or `--status finished` | Set status; new tasks start upcoming |
| `--task-type report\|code\|docs\|ops\|misc` | Required for normal new tasks; omit on updates to keep the stored type. Restore missing legacy types as `misc` |
| `--order N` | Set 1-based position in the task's status group |
| Repeatable `--task-details` | Set the detail lines; the arguments replace the stored details |
| `--task-details ""` | Clear stored details |
| `--msg-id <full-message-id>` | Link the note or report answer a task came from; optional, and only a note-born or report-born task carries it; still call `ack` |
| `--report <report-id>` | Link a blocked task to the report it waits on; an answer to that report clears the blocked mark |
| `--amend <previous-task-id>` | Rename a task without losing its details or order |

A task linked with `--report` stays blocked until the owner answers that report. The answer clears the mark once, and the link then only records what the wait was. The command refuses a report ID that no report holds. A report-linked task sorts ahead of unlinked tasks in its status group until an answer arrives. The `current` seat, in the listing and in the page's Current div, goes to the first upcoming task no blocked mark holds, so a task waiting on a report never reads as the work in hand. A typed task shows its type as a badge on its row, and the page's Type filter narrows every task div to one type.

A task row shows the answers linked to it in a read-only block under its details, and the block never replaces those details. The export keeps an answer its task still references, even after the report row is gone.

`import-state` replaces the live notes, answers, reports and tasks. With `--merge` it merges by ID and preserves existing message receipts. Use `--replace-tasks` only after checking the input. Never infer a finished task from a commit alone.

## Publish reports

- A report source is UTF-8 `.md`, at most 2,000,000 bytes.
- Report IDs have 1–80 letters, digits, hyphens or underscores; titles have 1–200 characters.
- Source edits do not update a published report: call `publish` again.
- A report that has answers refuses republishing under the same ID; publish its update under a new ID.
- Delete a report the owner no longer needs with `unpublish <id>`; its sent answers and its `.md` source under the state directory stay.
- Block agent unpublish for an unseen answer ack, not for a fresh owner view.
- Let the owner remove a report through the page without these agent-side guards.
- Verify the rendered report before telling the owner it is available.
- An image renders from its URL.
- `![alt](src =320x200)` sets its size, and `=320x` scales the height.
- If an update rejects an answer, ask the owner to preserve the draft, reload and review the current report.

## Report fields

| Markdown marker | Control |
| --- | --- |
| `- ( ) option` (`- (x)` to preselect) | One radio choice; the preselect is the marked recommended answer |
| `- [ ] option` (`- [x]` to preselect) | Checkbox choices |
| `- ( ) Label: ___` or `- [ ] Label: ___` inside a group | A labeled free-text choice |
| `Label: ___` or bare `___` | A text answer |

Write an option group's custom slot inside the group, as `- ( ) custom: ___`. The nearest non-empty line before a group is its prompt; a labeled blank supplies its own prompt. Append `{#my-id}` to a prompt to keep the field ID stable. Markers inside fenced code blocks remain text. The server caps a report's fields, prompts and options, and an over-limit value fails with an error the agent reads then. Fix invalid fields before publication. Each submission has its own pending ID; acknowledge every new answer, not just an earlier submission.

Give each group its own prompt, so every answer maps to its question:

```
Pick the release channel: {#release-channel}

- ( ) stable
- ( ) beta
- ( ) custom: ___

Which checks must pass first? {#release-checks}

- [ ] unit tests
- [ ] integration tests
- [ ] custom: ___
```

## Uploaded notes and Downloads

Keep composed-note files nonempty, with no application byte cap. `read` returns the note with an `attachments[]` list that carries each file's byte size; read an image record's `path` before acknowledging the single note ID, and read a text record with caution. `present: false` means the record remains but the bytes do not.

An agent asks with `download-request <url>`. That queues a pending job. The owner approves or denies it before the browser fetches the URL. Add `--allow-proxy` only for that URL. Reject credentials in URLs. Each completed job sends one inbox note with the saved path. Retry failures in Downloads; report CORS, network and size failures. A restore may keep a job record but lose the file. The NDJSON backup does not restore jobs or file bytes.

## Restore

Run `<skill>/scripts/install.sh` after a sandbox restore, before `git add`, to reinstate the venv, poll hook and ignore rule. Run it as a normal Bash call, never through the background process tool. Preserve the state directory and report source files. If the database is lost but `saved-state.ndjson` survives, run `import-state <file>` on that state directory; it creates the database and restores notes, tasks, report answers and report sources together. Files and unfinished download jobs are not in the NDJSON backup.

For an occupied port, identify its owner or choose another port; do not stop another service. Verify a restore with `read`, `task-list` and rendered reports before discarding backups. Report failed reads or saves; never treat them as empty state or a success. If the preview stays unavailable, use `ask_user` to ask how to continue. Do not enable an external channel or local report commits without a new choice.
