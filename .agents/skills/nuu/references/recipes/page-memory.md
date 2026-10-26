# Recipe: Page memory and optimistic writes

Show optimistic write rows immediately, key in-memory page caches on content version (`branch tip` or `updated_at`), and persist active view state (selected tab, filter, sort, scroll position, and unsent draft text) in URL query parameters or `localStorage` so reloads and tab switches never lose context.

![Page Memory Dark-Mode Example](../examples/write-shows-its-row.png)

## Rules

- Every write shows its pending row or state immediately with a client-generated request ID, reconciles in place when the server replies, and reuses the same ID on retry so duplicate rows never appear.
- Key list, report, and detail caches in a `Map` on the content version (`repo/ref/tip/cursor` or `updated_at`) so unchanged views paint immediately and stale keys miss automatically; pass `fresh: true` only on explicit Reload.
- Persist view state (selected tab, search/filter query, sort order, and unsent form drafts) in URL query parameters (`?tab=...`) or `localStorage`, and restore it before first paint on reload.

## Reference implementation

```js
const COMMIT_PAGES = 60;
const commitPages = new Map();

function commitKey(cursor) {
  const s = state.repo?.status || {};
  const ref = selectedArena(s);
  const tip = ref ? (s.tips || {})[ref] : (s.head || "").split("\t")[0];
  return tip ? `${state.repo.id}/${ref || "HEAD"}/${tip}/${cursor || ""}` : "";
}

async function commitPage(url, cursor, fresh = false) {
  const key = commitKey(cursor);
  if (key && !fresh && commitPages.has(key)) return commitPages.get(key);
  const data = await api(url);
  if (!key) return data;
  if (commitPages.size >= COMMIT_PAGES) commitPages.delete(commitPages.keys().next().value);
  commitPages.set(key, data);
  return data;
}

// Optimistic write with idempotent retry under one client ID
async function submitItem(text, clientId = crypto.randomUUID()) {
  let row = state.items.find(item => item.clientId === clientId);
  if (!row) {
    row = { clientId, text, status: "saving" };
    state.items.unshift(row);
  } else {
    row.status = "saving";
  }
  render();
  try {
    const saved = await api("/api/items", { method: "POST", body: { clientId, text } });
    Object.assign(row, saved, { status: "ok" });
  } catch (err) {
    row.status = "failed";
  }
  render();
}
```
