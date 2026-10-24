# Recipe: External channel (ntfy)

- Use this only after the owner selects the external channel.
- The owner supplies a topic `<repo>-<branch>-<8-char unguessable secret>` (sanitize the branch) and posts notes to its URL.
- At each steering read, use page-fetch on `https://ntfy.sh/<topic>/json?poll=1&since=<marker>`.
- Start with `since=all`.
- Then use the newest `event:"message"` ID as the marker in `<state-dir>/ntfy-since.txt`; ignore `open` and `keepalive` events.
- An empty response, or a first 500 with no message body on a new topic, is quiet until the owner posts.
- For other JSON failures, try the HTML topic page, then a `since=all` replay.
- Read error bodies: retry an upstream error once.
- If it repeats, tell the owner the error and a fresh topic URL, then read that topic with `since=all`.
- If the new topic repeats the error, stop for this turn and retry after the owner's next message.
- Report a repeated 500 on an established topic once and retry at the next read.
- Do not use sandbox HTTP to poll ntfy.
