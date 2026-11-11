# Direct GitHub Actions job logs

Use a numeric job ID, not a run ID, to read the text of one GitHub Actions job log.

1. Get the numeric job ID from `gh pr checks <PR-number>` or the run's job list.
2. Run `steer job-log -j <job-id>` in the checkout. The helper uses `gh` authentication and prints a signed HTTPS log URL without following the redirect.
3. Use `functions.fetch_page` for that URL, not GitHub's job page, because it is separate from the shell egress allowlist. Do not claim failure until `fetch_page` errors, and fetch the next `chunkIndex` on the same URL when `hasMore` is true.
4. If the URL expires, run `steer job-log -j <job-id>` again. Do not commit, publish, or quote the URL.
5. If the helper prints log text and a `no URL` reason, use the fallback text or report the lookup failure.

Never print or copy the authentication token. `steer job-log -r <run-id>` links to the run page, not a fetchable per-job log.
