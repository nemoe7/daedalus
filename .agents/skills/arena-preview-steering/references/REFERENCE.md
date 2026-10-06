# Preview transport: commands and recovery

Use `scripts/preview.py` relative to the actual installed steering skill. The state directory is `ARENA_PREVIEW_STATE_DIR` when that is set, and the repository `arena-state` otherwise. Keep the same ignored, persisted directory across CLI calls and server restarts. The installed wrapper finds the repository from its own path, so every CLI call works from any working directory and needs no `cd`.

## Commands

| Command | Use |
| --- | --- |
| `init` | Create a missing state database without starting the server |
| `serve --port 8000` | Start the shared preview with `start_process`, prefixed with `setsid` so no shell exit reaches it |
| `read` | List every pending note and report answer; mark only delivered IDs Seen, and stamp the parent report read by the agent |
| `key` | Print the recorded agent key, host and stamp; needs no server, and a later session recovers the key after the quiet note is acknowledged |
| `poll` | Wait for a pending inbox item before ending a turn; return at once with the task list while an upcoming task is unblocked, and at once when the owner pressed Skip poll in the page |
| `ack <id> --reply <markdown>` | Answer one delivered ID with a rendered reply |
| `ack <id> --note <text>` | Answer one delivered ID with one plain line |
| `task-list` | List tasks and their stored status, order and details |
| `task ID TITLE [DETAIL ...]` | Add or update a task; use `--msg-id` for note-born and report-born tasks, and `--blocked` or `--unblocked` for the blocked mark |
| `task-remove ID` | Remove a task entered by mistake |
| `import-state [FILE]` | Import copied NDJSON or JSON notes, tasks and report answers from a file or stdin in one transaction; `--replace-tasks` replaces only tasks; an import older than the live state is refused unless `--force` |
| `publish <source.md> --id <id> --title <title>` | Publish or update a rendered report |
| `unpublish <id>` | Remove a report from the tab; its answers and source survive |

Use complete IDs in CLI calls; cite their first seven characters in prose. `read` does not acknowledge an item. Supply one of `--reply` or `--note` to `ack`; use separate calls for different answers. A repeated `ack` on an ID appends one more reply block and keeps the earlier ones. The same on a submission ID appends reply blocks to its receipt. An unknown ID fails the whole receipt batch. Answer later submissions under their own IDs. If the preview is unavailable, use `ACK:` in chat for delivered notes.

Refer to a note in prose by the first seven characters of its ID, NEVER by its sequence number (`seq`) or list position. Use the full ID in CLI calls. Before sending a note reference, check it against the ID returned by `read`.

When an acknowledgement refers to a report or task, use `ack --reply` and put its exact full ID in inline backticks; the seven-character shortening applies to note IDs only. Use IDs unique across notes, reports and tasks so the client can link them; `publish` and `task` refuse an ID the other holds.

## External channel (ntfy)

Use this only after the owner selects the external channel. The owner supplies a topic `<repo>-<branch>-<8-char unguessable secret>` (sanitize the branch) and posts notes to its URL. At each steering read, use page-fetch on `https://ntfy.sh/<topic>/json?poll=1&since=<marker>`. Start with `since=all`. Then use the newest `event:"message"` ID as the marker in `<state-dir>/ntfy-since.txt`; ignore `open` and `keepalive` events. An empty response, or a first 500 with no message body on a new topic, is quiet until the owner posts. For other JSON failures, try the HTML topic page, then a `since=all` replay. Read error bodies: retry an upstream error once. If it repeats, tell the owner the error and a fresh topic URL, then read that topic with `since=all`. If the new topic repeats the error, stop for this turn and retry after the owner's next message. Report a repeated 500 on an established topic once and retry at the next read. Do not use sandbox HTTP to poll ntfy.

## Read cadence

Read the inbox at turn start, each reasoning boundary, before and after every tool-call block, before expensive or irreversible work, and before turn end. When ending a turn or a report form awaits answers, run `poll`. Co-issue a read inside each parallel block and read again after it returns; a block is the cadence unit. A count that changes inside a block is a read now, not at the next boundary: the reminder prints only a count, so a higher count means notes nobody has read. ALWAYS run `poll` on the bash call that ends the turn, chained or not. Between two reads, NEVER run a fourth bash call; a read MUST come before it. NEVER set or export `_arena_preview_platform`; a blocked gate is repaired, NEVER bypassed. A blocking-only call needs its read after return. Initial discovery may precede the first read; startup MUST.

The page's Message log toolbar carries a Skip poll button. A press arms one flag and writes no note, so no stale line waits in the log for a later turn to misread. A second press clears the flag, so the owner can take the skip back before the poll reads it. A listing that carries a `skip_poll` stamp reports it: the poll that finds it consumes it, prints `SKIP: the owner pressed Skip poll; end the turn without another poll.` on stderr, and the turn ends there. Never poll again after a skip. A note or a report answer clears the flag as it arrives, so the wait delivers it; an item that arrived before the press, or an unblocked task, still outranks the skip and keeps the flag armed.

Every CLI call stamps `agent_seen_at`. The preview header turns amber with `No agent since <time>` after three quiet minutes, so a preview left open after a turn says no agent has spoken.

## Tasks

Task IDs have 1–64 lowercase letters, digits or hyphens and start with a letter or digit; use a short kebab-case title. Titles have at most 200 characters. A task has at most 40 details of 2000 characters each. Existing IDs update; omitted fields keep stored values.

| Flag | Use |
| --- | --- |
| `--status upcoming` or `--status finished` | Set status; new tasks start upcoming |
| `--order N` | Set 1-based position in the task's status group |
| Repeatable `--task-details` | Set the detail lines; the arguments replace the stored details |
| `--task-details ""` | Clear stored details |
| `--msg-id <full-message-id>` | Link a note or report answer to its task; still call `ack` |
| `--amend <previous-task-id>` | Rename a task without losing its details or order |

`import-state` merges by ID and preserves existing message receipts. It refuses an import whose newest message is older than the newest message in the live state, and names both stamps. `--force` overrides the guard. Use `--replace-tasks` only after checking the input. Never infer a finished task from a commit alone.

## Publish reports

A report source is UTF-8 `.md`, at most 2,000,000 bytes. IDs have 1–80 letters, digits, hyphens or underscores; titles have 1–200 characters. Source edits do not update a published report: call `publish` again. A report that has answers refuses republishing under the same ID; publish its update under a new ID. Delete a report the owner no longer needs with `unpublish <id>`; its sent answers and its `.md` source under the state directory stay. Verify the rendered report before telling the owner it is available. An image renders from its URL. `![alt](src =320x200)` sets its size, and `=320x` scales the height. If an answer is rejected after an update, ask the owner to preserve the draft, reload and review the current report.

## Report fields

| Markdown marker | Control |
| --- | --- |
| `- ( ) option` (`- (x)` to preselect) | One radio choice |
| `- [ ] option` (`- [x]` to preselect) | Checkbox choices |
| `- ( ) Label: ___` or `- [ ] Label: ___` inside a group | A labeled free-text choice |
| `Label: ___` or bare `___` | A text answer |

Write an option group's custom slot inside the group, as `- ( ) custom: ___`. The nearest non-empty line before a group is its prompt; a labeled blank supplies its own prompt. Append `{#my-id}` to a prompt to keep the field ID stable. Markers inside fenced code blocks remain text. Limit a report to 50 fields, each prompt to 500 characters, and each group to 1–20 unique options of at most 200 characters. Fix invalid fields before publication. Each submission has its own pending ID; acknowledge every new answer, not just an earlier submission.

## Uploaded notes and Downloads

Each file on a composed note is 1–50MB. `read` returns the note with an `attachments[]` list; read each record's `path` before acknowledging the single note ID. `present: false` means the record remains but the bytes do not.

An agent asks with `download-request <url>`. That queues a pending job. The owner approves or denies it before the browser fetches the URL. Add `--allow-proxy` only for that URL. Credentials in URLs are rejected. Each completed job sends one inbox note with the saved path. Retry failures in Downloads; report CORS, network and size failures. A restore may keep a job record but lose the file. The NDJSON backup does not restore jobs or file bytes.

## Restore

Run `<skill>/scripts/install.sh` after a sandbox restore, before `git add`, to reinstate the venv, poll hook and ignore rule. Preserve the state directory and report source files. If the database is lost but `saved-state.ndjson` survives, run `import-state <file>` on that state directory; it creates the database and restores notes, tasks, report answers and report sources together. Files and unfinished download jobs are not in the NDJSON backup.

If a port is occupied, identify its owner or choose another port; do not stop another service. Verify a restore with `read`, `task-list` and rendered reports before discarding backups. Report failed reads or saves; never treat them as empty state or a confirmed save. If the preview stays unavailable, use `ask_user` to ask how to continue. Do not enable an external channel or local report commits without a new choice.
