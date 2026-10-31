# Preview CLI commands and IDs

Run every command as the bare `arena-preview`; the script under the installed `arena-skill` skill is `scripts/preview.py`. The state directory uses `ARENA_PREVIEW_STATE_DIR` when set, and repository `arena-state` otherwise. Keep the same ignored, persisted directory across CLI calls and server restarts. The installed wrapper finds the repository from its own path, so every CLI call works from any working directory and needs no `cd`.

## Commands

| Command | Use |
| --- | --- |
| `init [-z, --tz <zone>]` | Create a missing state database without starting the server. A zone names where CLI stamps print, and the TZ env wins |
| `serve [-p, --port 8000]` | Start the shared preview with `start_process`. The gate warns when a serve names a port other than 8000 |
| `read` | List every pending note and report answer; mark only delivered IDs Seen, and stamp the parent report read by the agent |
| `survey [-i, --smallest-image] [<json>]` | Show the instructions, questions, and JSON schema, or print the smallest or fallback image path with `-i, --smallest-image`. Supply JSON to submit answers. The receipt has no verdict. Results stay in server RAM and appear through the connection-dot dialog |
| `skills` | List installed and repository skill names, full descriptions, and paths; prefer installed duplicates; do not load skills |
| `agents [<path>]` | List `AGENTS.md` paths from the repository root through the target directory or file path |
| `note list [-p, --page N] [-s, --page-size N]` | List stored note IDs, including acknowledged and quiet notes, newest first; default page 1, 20 IDs per page, maximum 100; leave receipts and exports unchanged |
| `note show <id>` | Reread a stored note by its six or full characters; a short ID that matches several notes prints every match |
| `key` | Print the recorded agent key, host and stamp; breaks while the preview server is not up, and a later session recovers the key after acknowledging the quiet note |
| `poll` | Wait for inbox items with `poll` before ending a turn; continue when it returns unblocked tasks. End after Skip poll or a 1799-second return with no new messages. Set the Bash tool timeout to 1800 seconds; NEVER wrap `poll` in shell `timeout`. |
| `run start [-t, --task <task-id>] [-a, --after <gate-name>] <name> -- <command>` | The gate's helper for a detected long run, and the call a failed start names: without `--task`, it links the first unblocked upcoming task when one exists; it starts the command detached under a name and records its tail and exit, so a live linked run blocks and names its task until it ends, and the verdict prints on a later call as `run <name> done: exit N`, with one status line per call while it runs. `--after` waits for that run instance to pass. A failed, missing, or replaced dependency cancels the waiting gate and its downstream chain. |
| `run logs <name>` | Print a routed run's status and its recorded output tail; it works while the run lives and after its verdict, and a second read before that verdict exits 130 |
| `worktree start [-g, --gate <gate-name>] [-a, --allow <glob>] <name> <destination>` | Create a detached task worktree outside the root, beside a named root gate when one runs |
| `worktree collect <name>` | Print the allowed detached-worktree patch after a green root gate |
| `ack <markdown>` | Omit IDs only when exactly one non-quiet note or report answer is pending. Supply one Markdown text. With multiple pending items, supply the ID |
| `ack <id> <markdown>` | Answer one delivered ID with a rendered reply; single-quote inline text. Add `-i, --id`, `-t, --title`, `-T, --type`, `-S, --scope <scope>`, and repeatable `-d, --detail` to create its linked task atomically |
| `task list [-f, --full]` | List upcoming task IDs, order, status, report links, set type marks and each linked report answer with its stamp and text. With `-f`, include finished tasks. Use task ID for details |
| `task show ID` | Print one stored task: its title, status, order, primary type when set, report link, blocked mark and every detail |
| `task set ID TITLE [DETAIL ...]` | Add or update a task; every task carries at least one detail, and reject a write that leaves none; one task holds one job, so several jobs mean several tasks rather than one task with many details; `-m, --msg-id` is optional and only a note-born or report-born task carries it; `-u, --unblocked` clears a report wait. |
| `task unlink SUBMISSION-ID` | Remove an answer from all task answer lists without changing tasks, text, or receipts. Retain the explicit unlink in backups; `task set -m` can link it again |
| `task remove ID` | Remove a task entered by mistake |
| `state import [-r, --replace-tasks] [FILE]` | Import copied NDJSON or JSON notes, tasks and report answers from a file or stdin in one transaction; it always replaces the live notes, answers, reports and tasks; `-r, --replace-tasks` replaces only tasks |
| `state compare [FILE]` | Compare an NDJSON or JSON state backup (or the newest uploaded backup when omitted) against the live store without replacing state |
| `download-request [-a, --allow-proxy] <url>` | Queue one pending file download for the owner to approve in Downloads; `-a, --allow-proxy` permits the two proxies |
| `image-data-uri <path>` | Print one Markdown line carrying a local PNG, JPEG, GIF or WebP file as a data URI; the command checks the type and a 2 MiB size cap before encoding |
| `outline [-m, --max-entries N] [-j, --json] <path>` | Print a read-only source outline with line ranges for a local repository file up to 1 MB and 200 entries |
| `pr-update -n, --number <pr> -t, --title <title> -f, --body-file <path>` | Update a pull request title and body via REST from a workspace file (never `/tmp` or `-f body=@path`), then re-fetch and verify exact parity |
| `job-log [-j, --job <job-id>] [-r, --run <run-id>]` | Resolve the signed GitHub Actions job-log `Location` redirect URL via `gh api -i` (or fallback `/v1/gh` proxy URL) and print it for `fetch_page` |
| `report publish <source.md> -i, --id <id> -t, --title <title> [-s, --submission-format <json>] [-u, --allow-unparsed-markers]` | Publish or update a rendered report. Publication embeds readable local images as data URIs, skips remote sources with a helper hint, keeps links and data URIs, and rejects possible unparsed `==` or `~~` diff markers unless `-u, --allow-unparsed-markers` is set |
| `report unpublish <id>` | Remove a report from the tab, retaining answers and source. Unacknowledged answers or unseen answer receipts block the call with 409. A fresh owner view does not block agent unpublish, and the page retains the open report until the owner clicks away |
| `report unpublish -s, --stale` | Recheck eligibility, then unpublish only stale reports through existing guards; retain sources and answers |
| `report stale` | List stale published report IDs and reasons without changing state |
| `report state` | Print publication, owner view stamp, staleness and answer visibility for one report or all |
| `rename <id> <new-id>` | Rename one stored note, report answer, report or task in place, so its answers, order and stamps survive |
| `state clear` | Empty every state table in place; preserve the agent key, refresh the save file to match, and keep a running server's recorded port |
| `board status \| post [-t, --to <session>] [-f, --file <path>] <text> \| wake [<session>]` | Inspect board sessions and unread entries, post a board message (with optional target or attachment), or wake a polling session |
| `GET / POST /api/board` | Read or update the multi-session board (`action`: `sync`, `allow`, `revoke`, `set-main`, `heartbeat`, `post`, `wake`, or `clear`) |

- Every command except `init` and `serve` breaks with a restart line while no preview answers, so the agent starts the server first.
- Use complete IDs in CLI calls; cite their first six characters in prose.
- Read note, answer and task content through the CLI alone: use `read`, `note show ID`, `note list`, `task show ID` and `task list`; use report HTTP endpoints only for required publication checks.
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

## Multi-session board (`/api/board`)

Run `arena-preview board status`, `arena-preview board post <text>`, or `arena-preview board wake [<session>]` from the shell.

`GET /api/board` returns the current `allowlist`, `main_session_id`, `sessions`, `entries`, and `wake_stamps`. `POST /api/board` accepts JSON with `action` set to `sync`, `allow`, `revoke`, `set-main`, `heartbeat`, `post`, `wake`, or `clear`.

```bash
curl -s -X POST http://127.0.0.1:8000/api/board \
  -H 'Content-Type: application/json' \
  -d '{"action":"post","session_id":"<sid>","to":"<target>","payload":{"text":"hi"}}'
```

- Only allowlisted sessions can post entries, send file attachments, or update heartbeats (`allow` and `revoke` manage the allowlist).
- `set-main` designates the main session (`main_session_id`); worker sessions hand off findings to the main session instead of committing or pushing.
- `wake` records a wake stamp for the target session or all polling sessions, which wakes `arena-preview poll` (`BOARD WAKE`) without arming `skip_poll`.
