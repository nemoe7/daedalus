# Reports, fields, and uploads

## Publish reports

- A report source is UTF-8 `.md`, at most 2,000,000 bytes.
- Explain findings, bugs, decisions, and options in plain English, not internal code symbols or database identifiers.
- Report IDs have 1–80 letters, digits, hyphens or underscores; titles have 1–200 characters.
- Source edits do not update a published report: call `publish` again.
- A report that has answers refuses republishing under the same ID; publish its update under a new ID.
- Delete a report the owner no longer needs with `unpublish <id>`; its sent answers and its `.md` source under the state directory stay.
- Block agent unpublish for an unseen answer ack, not for a fresh owner view.
- Let the owner remove a report through the page without these agent-side guards.
- Verify the rendered report before telling the owner it is available.
- An image renders from its URL.
- `![alt](src =320x200)` sets its size, and `=320x` scales the height.
- When `nuu` is available and used, always embed its captures, including before-and-after images for UI changes and audits, in the report.
- If an update rejects an answer, ask the owner to preserve the draft, reload and review the current report.

## Report fields

| Markdown marker | Control |
| --- | --- |
| `- ( ) option` (`- (x)` to mark recommended) | One radio choice; the UI prefixes a marked choice with `* ` and `report publish` prints `you recommended:` |
| `- [ ] option` (`- [x]` to mark recommended) | Checkbox choices; the UI prefixes each marked choice with `* ` |
| `- ( ) Label: ___` or `- [ ] Label: ___` inside a group | A labeled free-text choice |
| `Label: ___` or bare `___` | A text answer |

Write an option group's custom slot inside the group, as `- ( ) custom: ___`. The nearest non-empty line before a group is its prompt; a labeled blank supplies its own prompt. Append `{#my-id}` to a prompt to keep the field ID stable. Markers inside fenced code blocks remain text. The server caps a report at 24 fields, prompts and options, and an over-limit value fails with an error the agent reads then. Fix invalid fields before publication. Each submission has its own pending ID; acknowledge every new answer, not just an earlier submission.

Give each group its own prompt, so every answer maps to its question:

```
Select the release channel: {#release-channel}

- ( ) stable
- ( ) beta
- ( ) custom: ___

Which checks must pass first? {#release-checks}

- [ ] unit tests
- [ ] integration tests
- [ ] custom: ___
```

## Uploaded notes and Downloads

Keep composed-note files nonempty, with no application byte cap. `read` returns the note with an `attachments[]` list that carries each file's byte size; read an image record's `path` before acknowledging the single note ID, and read a text record with caution. `present: false` means the record remains but the bytes do not.

An agent asks with `download-request <url>`. That queues a pending job. The owner approves or denies it before the browser fetches the URL. Add `-a` / `--allow-proxy` only for that URL. Reject credentials in URLs. Each completed job sends one inbox note with the saved path. Retry failures in Downloads; report CORS, network and size failures. A restore may keep a job record but lose the file. The NDJSON backup does not restore jobs or file bytes.
