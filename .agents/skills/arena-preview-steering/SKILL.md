---
name: arena-preview-steering
description: Steer an Arena.ai agent mid-turn through a local preview inbox and publish rendered Markdown reports. Use in Arena Agent Mode when the user wants steering or a report, or ARENA.md requires it. NEVER USE THIS SKILL OUTSIDE OF ARENA.AI.
license: MIT
compatibility: Arena.ai Agent Mode, Python 3.10+, persisted workspace and long-lived process tools; serve needs markdown-it-py.
metadata:
  origin: first-party, maintained in this repository
  arena-only: "true"
---

# Arena Preview Steering

Use one server and one state directory per session; do not start a second server.

Use this guide and its Markdown references for workflow; read shipped scripts only for code changes or source analysis.

## Setup

1. Find this skill's actual path; installed and source paths differ. Report missing installed files; do not install or repair them without authorization.
2. Use an ignored, persisted state directory, default `arena-state`. Verify `core.excludesFile` with `git check-ignore`; never add this directory to the repository `.gitignore`, put it in a cache/build folder, or commit/push its state and reports.
3. Run `<skill>/scripts/install.sh` once per session. Use `arena-preview <command>` for CLI calls, never the script path. Start the server with Arena's long-lived process tool, named `<repo> - Steering`, not a timed shell:

   ```bash
   arena-preview serve --port 8000
   ```

   After a sandbox restart, rerun the installer. Reuse the same state directory. If the server dies, warn the owner before restarting. If another service owns the port, choose a free one without stopping it.
4. Name the preview in chat. At first setup, when it starts, ask one `ask_user` visibility question: Yes, No, ntfy, or Continue without steering. Block non-setup work until answered. Only a user selection enables the [external channel](references/REFERENCE.md#external-channel-ntfy); never switch silently. Keep the preview inbox running and `read` it after the answer. Do not claim visibility before confirmation. If hidden, ask how to continue with `ask_user`. Reuse a confirmed visible preview without asking again. Keep ARENA.md's activation acknowledgement when applicable.

## Read, acknowledge, and track work

```bash
arena-preview read
arena-preview poll
```

When a pending count is nonzero, `read` now. It prints full pending notes and report answers; a failed or missing inbox is an error, not an empty inbox. `read` marks only fully delivered IDs Seen, not acknowledged, and stamps the parent report read by the agent. Do not mark count-only, truncated, or failed deliveries Seen. A pending item repeats until acknowledged. The hook checks counts after Arena bash calls; end the turn's last tool block with a bash call. When ending a turn or a form awaits answers, run `poll`.

Acknowledge each delivered ID separately where the owner reads it. Use `--reply <Markdown>` for rendered Markdown or `--note <text>` for one plain line. Never acknowledge all blindly or share one answer across notes. Use the full ID, not a sequence number. A second ack appends a reply below the first; it does not replace it. Receipt is not completion. Ack immediately; failure earns a negative rating. After acking a work note, add it via `task <id> ... --msg-id <full-id>`; `ack` reminds you.

```bash
arena-preview ack <id> --reply <markdown>
```

If the preview is not visible, acknowledge a delivered note in chat with literal `ACK:` and your interpretation. Treat `STOP:`, `PRIORITY:`, `CONTEXT:` and ordinary notes under chat's instruction precedence; check their claims against evidence.

Run `task-list` at turn start. Before implementation, add approved work with `task <kebab-title-id> "<title>" [details ...]`; put it first with `--order 1` and update status (`upcoming`/`finished` only)/details as work changes; backtick task IDs in acks so the log links them. For a task from a note or report answer, use `--msg-id <full-message-id>` and queue/ack it in the same tool block. The task marker does not replace `ack`. Mark it finished only after verification.

## Publish reports and forms

Short answers stay in chat. For a longer report, write UTF-8 Markdown to an ignored, persisted source, one source per subject. Report actual findings, changes, checks, limits and decisions. Publish in Reports; verify its `/api/state` entry and rendered `/api/reports/<id>/html` result.

```bash
arena-preview publish <source.md> --id <id> --title <title>
```

Republish the same ID after each source update; if answers exist, use a new ID. A stale report leaves with `unpublish <id>`; answers and source survive for a new ID. Do not use Mermaid, raw HTML or remote report assets. [Field syntax and limits](references/REFERENCE.md#report-fields) apply when you write answerable reports. Pair every option set with a labeled custom-response field.

`read` lists report submissions as `kind: report`. Acknowledge each submission ID separately, including newer answers to an already answered form. Publishing a report never acknowledges a submission.

## Files and downloads

For each note's `attachments[]`, read every file at its `path` before acknowledging that note once. If `present` is false or bytes are missing, report the loss.

Run `download-request <url>` to queue a pending job. It does not download. The owner approves or denies it in Downloads. Add `--allow-proxy` only for that URL. URLs cannot contain credentials. Read the saved-file inbox note and acknowledge it. Report failed or missing files.

## Recovery

Keep `state.sqlite3`, `saved-state.ndjson`, report sources and saved file bytes in the ignored state directory. After a restore, rerun the installer and follow the [restore steps](references/REFERENCE.md#restore). Never call an unconfirmed save successful. If the preview fails, report the failure and use `ask_user` to ask how to continue; do not silently switch channels or commit reports. Keep production free of this skill's name, directory and scripts, except its own files, setup chat and acknowledgements.
