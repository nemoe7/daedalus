# Preview transport: commands and recovery

Use `scripts/preview.py` relative to the actual installed steering skill. Put `--state-dir <directory>` before every subcommand. Keep the same ignored, persisted directory across CLI calls and server restarts.

## Commands

| Command | Use |
| --- | --- |
| `init` | Create a missing state database before restoring an NDJSON backup |
| `serve --port 8000` | Start the shared preview with a long-lived process tool |
| `read` | List every pending note and report answer; mark only delivered IDs Seen |
| `seen <ids>` | Mark fully delivered IDs Seen without answering; never use on counts or truncated output |
| `ack <id> --reply <markdown>` | Answer one delivered ID with a rendered reply |
| `ack <id> --note <text>` | Answer one delivered ID with one plain line |
| `task-list` | List tasks and their stored status, order and details |
| `task ID TITLE [DETAIL ...]` | Add or update a task; use `--msg-id` for note-born and report-born tasks |
| `task-remove ID` | Remove a task entered by mistake |
| `task-import [FILE]` | Restore task records from JSON, a file or stdin; `--replace` clears first |
| `publish <source.md> --id <id> --title <title>` | Publish or update a rendered report |
| `unpublish <id>` | Remove a report from the tab; its answers and source survive |
| `import-notes <notes.ndjson>` | Restore note and report-answer records from an export |

Use complete IDs in CLI calls; cite their first seven characters in prose. `read` does not acknowledge an item. Supply one of `--reply` or `--note` to `ack`; use separate calls for different answers. A repeated `ack` on an ID appends one more reply block and keeps the earlier ones. The same on a submission ID appends reply blocks to its receipt. An unknown ID fails the whole receipt batch. Answer later submissions under their own IDs. If the preview is unavailable, use `ACK:` in chat for delivered notes.

## External channel (ntfy)

Use this only after the owner selects the external channel. The owner supplies a topic `<repo>-<branch>-<8-char unguessable secret>` (sanitize the branch) and posts notes to its URL. At each steering read, use page-fetch on `https://ntfy.sh/<topic>/json?poll=1&since=<marker>`. Start with `since=all`. Then use the newest `event:"message"` ID as the marker in `<state-dir>/ntfy-since.txt`; ignore `open` and `keepalive` events. An empty response, or a first 500 with no message body on a new topic, is quiet until the owner posts. For other JSON failures, try the HTML topic page, then a `since=all` replay. Read error bodies: retry an upstream error once. If it repeats, tell the owner the error and a fresh topic URL, then read that topic with `since=all`. If the new topic repeats the error, stop for this turn and retry after the owner's next message. Report a repeated 500 on an established topic once and retry at the next read. Do not use sandbox HTTP to poll ntfy.

## Read cadence

Read the inbox at turn start, each reasoning boundary, before and after every tool-call block, before expensive or irreversible work, and before turn end. When ending a turn or a report form awaits answers, loop `sleep 10` and `read`; break on a new message or after 100 loops. Co-issue a read inside each parallel block and read again after it returns; a block is the cadence unit. A count that changes inside a block is a read now, not at the next boundary: the reminder prints only a count, so a higher count means notes nobody has read. End every bash call with a poll, so no call, chained or not, starves the inbox. A blocking-only call needs its read after return. Initial discovery may precede the first read; startup MUST.

## Tasks

Task IDs have 1–64 lowercase letters, digits or hyphens and start with a letter or digit. Titles have at most 200 characters. A task has at most 40 details of 2000 characters each. Existing IDs update; omitted fields keep stored values.

| Flag | Use |
| --- | --- |
| `--status upcoming` or `--status finished` | Set status; new tasks start upcoming |
| `--order N` | Set 1-based position in the task's status group |
| `--task-id`, `--task-title`, repeatable `--task-details` | Set supplied fields; detail arguments replace stored details |
| `--task-details ""` | Clear stored details |
| `--msg-id <full-message-id>` | Link a note or report answer to its task; still call `ack` |
| `--amend <previous-task-id>` | Rename a task without losing its details or order |

`task-import` merges by ID. Use `--replace` only after you check the input. Never infer a finished task from a commit alone.

## Publish reports

A report source is UTF-8 `.md`, at most 2,000,000 bytes. IDs have 1–80 letters, digits, hyphens or underscores; titles have 1–200 characters. Source edits do not update a published report: call `publish` again. A report that has answers refuses republishing under the same ID; publish its update under a new ID. Delete a report the owner no longer needs with `unpublish <id>` or the tab's ✕ button, which asks for a second click. The delete touches the tab row alone: sent answers stay in the inbox, the `.md` source under the state directory stays for republishing, and a republish under that ID still fails while answers exist. Verify the rendered report before telling the owner it is available. If an answer is rejected after an update, ask the owner to preserve the draft, reload and review the current report.

## Report fields

| Markdown marker | Control |
| --- | --- |
| `- ( ) option` (`- (x)` to preselect) | One radio choice |
| `- [ ] option` (`- [x]` to preselect) | Checkbox choices |
| `- ( ) Label: ___` or `- [ ] Label: ___` inside a group | A labeled free-text choice |
| `Label: ___` or bare `___` | A text answer |

Pair each option group with a labeled custom-response field. The nearest non-empty line before a group is its prompt; a labeled blank supplies its own prompt. Append `{#my-id}` to a prompt to keep the field ID stable. Markers inside fenced code blocks remain text. Limit a report to 50 fields, each prompt to 500 characters, each group to 1–20 unique options of at most 200 characters, and each text answer to 2000 characters. A whole submission is limited to 150,000 characters. Fix invalid fields before publication. Each submission has its own pending ID; acknowledge every new answer, not just an earlier submission.

## Uploaded notes and Downloads

One composed note can include up to five files, each 1–50,000,000 bytes. `read` returns the note with an `attachments[]` list; read each record's `path` before acknowledging the single note ID. `present: false` means the record remains but the bytes do not. The note's receipt shows the original filenames.

The owner enters one HTTPS URL per job in Downloads. An agent asks with `download-request <url>`. That queues a pending job. The owner approves or denies it before the browser fetches the URL. Add `--allow-proxy` only for that URL. Keep their browser open for the fetch. Direct access is the default. Proxy fallback needs a separate opt-in for each URL; AllOrigins and then CodeTabs see that URL. Credentials in URLs are rejected. A download is at most 102,400,000 bytes. That cap is not measured. Each completed job sends one inbox note with the saved path. Retry failures in Downloads; report CORS, network and size failures. A restore may keep a job record but lose the file. The NDJSON backup does not restore jobs or file bytes.

## Restore

Run `<skill>/scripts/install.sh` after a sandbox restore, before `git add`, to reinstate the venv, poll hook and ignore rule. Preserve the state directory and report source files. If the database is lost but `saved-state.ndjson` survives, run `init` on that state directory first. Then run `import-notes <file>` and `task-import <file>` there; neither import alone restores both notes and tasks. Report sources must be republished if their snapshots are lost. Files and unfinished download jobs are not in the NDJSON backup.

If a port is occupied, identify its owner or choose another port; do not stop another service. Verify a restore with `read`, `task-list` and rendered reports before discarding backups. Report failed reads or saves; never treat them as empty state or a confirmed save. If the preview stays unavailable, use `ask_user` to ask how to continue. Do not enable an external channel or local report commits without a new choice.
