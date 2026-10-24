# Recipe: GitHub Actions job logs

- Query the redirect headers in Bash without following the redirect: `gh api -i /repos/<owner>/<repo>/actions/jobs/<job-id>/logs 2>&1 | grep -i '^location:'`.
- Pass the `https://pipelines.actions.githubusercontent.com/...` URL from `Location` to `fetch_page` (follow `chunkIndex` while `hasMore` is `true`).
- When the redirect URL is unavailable or expired, read `/v1/gh?key=<agent-key>&path=repos/<owner>/<repo>/actions/runs/<run-id>/logs` through `fetch_page`.
- NEVER fetch the proxy URL or the signed redirect URL from Bash (`curl`, `wget`, `python urllib`, or `gh run view --log`).
