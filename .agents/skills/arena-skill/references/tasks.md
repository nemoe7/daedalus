# Tasks and options

## Tasks

Task IDs have 1–64 lowercase letters, digits or hyphens and start with a letter or digit; use a short kebab-case title. Titles have at most 200 characters. Use at most 40 task details, each on one line of 120 characters or fewer. Existing IDs update; omitted fields keep stored values.

### Task types

Choose the primary deliverable type: `report` for findings or assessments, `code` for source or configuration changes, `docs` for documentation or rule text, `ops` for repository, environment, build, test, or release operations, and `misc` for other work.

### Options

| Flag | Use |
| --- | --- |
| `-s`, `--status upcoming\|finished` | Set status; new tasks start upcoming |
| `-t`, `-T`, `--type report\|code\|docs\|ops\|misc` | Required for normal new tasks; omit on updates to keep the stored type. Restore missing legacy types as `misc` |
| `-o`, `--order N` | Set 1-based position in the task's status group |
| Repeatable `-d`, `--details` | Set the detail lines; the arguments replace the stored details |
| `-A`, `--append` | Append the detail lines to the stored details without replacing them |
| `-m`, `--msg-id <full-message-id>` | Link the note or report answer a task came from; optional, and only a note-born or report-born task carries it; still call `ack` |
| `-r`, `--report <report-id>` | Link a blocked task to the report it waits on; an answer to that report clears the blocked mark |
| `-u`, `--unblocked` | Clear a report wait or legacy blocked mark |
| `-a`, `--amend <previous-task-id>` | Rename a task without losing its details or order |
| `-b`, `--blocked-by <task-id>` | Link a dependency task; repeatable; cycles refused |

A task linked with `--report` stays blocked until the owner answers or the report is unpublished. Either action clears the mark once, and the link then only records what the wait was. The command refuses a report ID that no report holds. A report-linked task sorts ahead of unlinked tasks in its status group until an answer arrives. The `current` seat, in the listing and in the page's Current div, goes to the first upcoming task no blocked mark holds, so a task waiting on a report never reads as the work in hand. A typed task shows its type as a badge on its row, and the page's Type filter narrows every task div to one type.

A task row shows the answers linked to it in a read-only block under its details, and the block never replaces those details.
