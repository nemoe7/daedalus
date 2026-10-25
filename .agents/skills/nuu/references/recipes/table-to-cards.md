# Recipe: Table to cards

The markup stays one real `table` at every width. Wide screens show it as a table. Narrow screens (`<= 75em`) hide `thead` and turn `tbody` into a grid. Each `td` hands its content to the `tr` with `display: contents`, so the row lays out by grid area.

A `width: 100%` column takes the free width. The compact columns need `white-space: nowrap` on their longest text, since a table column shrinks to its longest word. A test reads `tr` cells and `th scope` on the wide side and the grid areas on the narrow side.

```html
<table class="grid" id="repo-table">
  <thead><tr><th scope="col">Repo</th><th scope="col">Default</th><th scope="col" class="wide">Arena Branches</th><th scope="col">Actions</th></tr></thead>
  <tbody id="repo-grid"></tbody>
</table>
```

```css
.grid { width: 100%; border-collapse: separate; border-spacing: 0; border: 1px solid var(--line); border-radius: 12px; }
.grid th { padding: 12px; text-align: left; white-space: nowrap; }
.grid td { padding: 0; border-top: 1px solid var(--line); vertical-align: middle; }
.grid .wide { width: 100%; }
.grid td .repo-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 75em) {
  .grid { display: block; border: 0; }
  .grid thead { display: none; }
  .grid tbody { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 12px; }
  .grid td { display: contents; }
  .repo-card { display: grid; grid-template-areas: "ico name rm" "arena arena arena" "actions actions actions"; }
}
```
