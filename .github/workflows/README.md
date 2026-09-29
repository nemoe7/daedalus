# AI GitHub workflows

Reusable GitHub Actions sources, with no instruction budget. Source: [nemoe7/clankers `github/workflows`](https://github.com/nemoe7/clankers/tree/481509c90e03d1627224e97e3bf3602764faad0d/github/workflows).

## Gemini release draft

[gemini-release.yml](gemini-release.yml), the Python files, the prompt files and the [release template](gemini-release-template.md) are all in `.github/workflows/`. The workflow runs only from the default branch.

Add `GEMINI_API_KEY` as an Actions secret. Run **Gemini Release Draft** from the default branch with an existing tag. The model defaults to [`gemini-3.5-flash-lite`](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite). It uses the built-in GitHub token with Contents write permission.

The baseline is the most recently published release reachable from the target tag, including prereleases and excluding drafts. An optional previous tag selects a published ancestor release. With no published release, the input includes all history reachable from the target. Missing tags, shallow history and API errors stop the run.

The input contains commit messages and each commit's diffs against every parent, including root and binary patches. Merge patches can repeat changes, and reverted changes remain in the history. Non-UTF-8 bytes use escaped text. The script never executes target-tag code.

Large inputs are split without truncation. Gemini summarizes each part and combines the summaries until they fit one final request. Coverage checks track every fragment, not model accuracy. Failed, blocked, incomplete or non-shrinking output stops the run without saving new notes.

The script creates or updates a draft. It refuses published releases, verifies the saved body and never publishes automatically. An external publish can race a draft update because the API has no atomic draft-only update. Review the draft against the commit comparison before publication.

Requests send repository history to Google and can incur API costs. The job has a 60-minute timeout. `INPUT_BYTES`, `PIECE_CHARS` and the model input are calibration controls, not instruction budgets. No live API run is part of the offline check.

## Check

```cmd
python .github\workflows\check_gemini_release.py
```

The check uses temporary Git history and mocked API responses. It does not need API keys or create releases.
