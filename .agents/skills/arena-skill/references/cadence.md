# Inbox read cadence and gate reminders

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
