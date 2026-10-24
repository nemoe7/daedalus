# Recipe: Sandbox restore

Run `<skill>/scripts/install.sh` after a sandbox restore, before `git add`, to reinstate the venv, poll hook and ignore rule. Run it as a normal Bash call, never through the background process tool. Preserve the state directory and report source files. If the database is lost but `saved-state.ndjson` survives, run `state import <file>` on that state directory; it creates the database and restores notes, tasks, report answers and report sources together. Files and unfinished download jobs are not in the NDJSON backup.

`state import` replaces the live notes, answers, reports and tasks; no flag keeps the live rows. Use `-r` / `--replace-tasks` only after checking the input. Never infer a finished task from a commit alone.

Verify a restore with `read`, `task list` and rendered reports before discarding backups. Report failed reads or saves; never treat them as empty state or a success. If the preview stays unavailable, use `ask_user` to ask how to continue. Do not enable an external channel or local report commits without a new choice.
