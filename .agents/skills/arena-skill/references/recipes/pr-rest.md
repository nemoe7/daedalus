# Recipe: PR title and body update via REST

- Update PR title/body via REST with JSON on stdin: `jq -n --rawfile body <workspace-file> --arg title <title> '{body: $body, title: $title}' | gh api repos/<owner>/<repo>/pulls/<n> -X PATCH --input -`.
- NEVER use `-f body=@path`; stage the pull-request body in a workspace file (never `/tmp`), then re-fetch the title and body after every PATCH and diff against the staged file.
