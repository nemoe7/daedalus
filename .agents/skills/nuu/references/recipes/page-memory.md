# Recipe: Page memory

Three memories live in a `Map` or a field on page state, none in storage. A list, report, or detail page keys on the content version, such as the branch tip or `updated_at` stamp, so a stale key misses on its own and switching back to an unchanged view paints immediately without refetching. A settings answer paints at once before the loading bar shows, and a fresh read replaces it only when it differs; an optimistic write applies its pending state immediately and reconciles with the server receipt under the same ID. A Reload button passes `fresh`.

A test counts requests and asserts that a known paint lands before its answer.

```js
const COMMIT_PAGES = 60;
const commitPages = new Map();
// the key names the repo, the ref, the tip, and the cursor, so a moved tip misses
function commitKey(cursor) {
  const s = state.repo?.status || {};
  const ref = selectedArena(s);
  const tip = ref ? (s.tips || {})[ref] : (s.head || "").split("\t")[0];
  return tip ? `${state.repo.id}/${ref || "HEAD"}/${tip}/${cursor || ""}` : "";
}
async function commitPage(url, cursor, fresh) {
  const key = commitKey(cursor);
  if (key && !fresh && commitPages.has(key)) return commitPages.get(key);
  const data = await api(url);
  if (!key) return data;
  if (commitPages.size >= COMMIT_PAGES) commitPages.delete(commitPages.keys().next().value);
  commitPages.set(key, data);
  return data;
}

// paint the known answer, read the fresh one, and re-render only on a difference
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
async function renderSettings() {
  const known = state.settings;
  if (known) render();
  state.settings = await api("/api/settings");
  if (known && same(known, state.settings)) return;
  render();
}
```
