# Worker Session Rules

- NEVER run git commit, git push, gh pr, or gh release in a worker session; the main session owns all commits, pushes, and pull requests.
- MUST run `arena-preview board post` to confirm board entry before the first `arena-preview poll`.
- Hand off findings, verification outputs, and patches to main via `arena-preview board post` and `arena-preview board wake`.
- Run `arena-preview report publish` to send fielded worker reports to the board and wait for `main`.
- Attach a git diff patch file and hand off verified findings to the main session on the board. NEVER paste full reports into board posts. Attach files or cite a report ID instead.
- Skip unpushed-commit and pull-request checks that apply only to the main session, and keep the working tree scoped to the assigned task.
- Wait on `arena-preview poll` (`timeout: 1800`) after posting a handoff so the main session can wake this worker with follow-up work.
- Inform the main session through `arena-preview board post` when open PR checks fail.
- ALWAYS check whether the main session branch moved and rebase onto it before auditing or building a patch.
