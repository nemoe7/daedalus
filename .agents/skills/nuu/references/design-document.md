# The Design Document (`docs/design.md` v2)

Keep one authoritative design record per repository at `[paths].doc` in `nuu.toml` (`docs/design.md` default), updated in the same change as any visual work (`DOC001`).

## Six Sections

Use these six sections for new design records (`docs/design.md` v2), and map their contents onto existing records without forcing heading rewrites. Pair sections with composite boards rendered via `nuu board -T 1..9,0,canvas` (see [design-visuals.md](design-visuals.md)):

1. **Pitch and Scope (`Board 1`: `design-visuals/overview-1.png`)**: Problem statement, appetite and constraints, routed flow overview (`-T 1` / `lay-of-the-land`), and explicit **No-gos**.
2. **Domain and Copy Rules**: Domain nouns, ID formats (`--font-mono`, tabular figures), UTC storage / relative display rules (`HRC201`–`HRC202`), and realistic copy rules with zero `Lorem ipsum` (`HRC203`).
3. **Foundations and Tokens (`Board 6`: `design-visuals/styles-6.png`)**: Authoritative `[paths].stylesheet` role tokens (`--bg`..`--scrollbar-thumb`), `Inter` / `Monaspace Neon` `9`-step type ramp (`11..32 px`, weights `400..700`), `16 px` / `20 px` Lucide icons (`ICN101`, `ICN102`), `68em/60em/30em` + `24em` breakpoints, and `var(--ease)`.
4. **Global Components (`Boards 5 & 7`: `design-visuals/components-5.png`, `anatomy-7.png`)**: Holistic variants vs `In current use` variants, all six Clause 9 states, concentric radii, and contrast-checked anatomy redlines.
5. **Major Areas (`Boards 2, 3, 4, 8`: `<area>-2.png`, `<area>-3.png`, `<area>-4.png`, `responsive-8.png`)**: Per-area deep dive, `Default` / `Empty` / `Error` / `Offline` views, control state matrix, `5`-viewport reflow strip, optimistic slow-network writes (`ACC207`), and URL/`localStorage` state memory.
6. **Evidence, Deviations, and Decisions (`Boards 9 & 0`: `a11y-9.png`, `diff-0.png`)**: Latest `nuu audit` summary, keyboard `:focus-visible` and contrast trail, `nuurecipe:` list, human-approved `nuu-deviation:` log, `[[assets]]` credits, and dated `Proposed` / `Accepted` / `Retired` ADRs.

## Filled Example

```markdown
# Ops Inbox Design

Status: proposed | Owner: web-team | Stylesheet: `src/style.css`

## 1. Pitch and Scope (`Board 1`: `design-visuals/overview-1.png`)

- **Problem:** Operators lose triage context when switching between incoming requests and workspace settings.
- **Appetite and constraints:** Two views (`Inbox`, `Settings`), offline bundled fonts, zero external CDNs.
- **Flow overview:** `Inbox` list -> `Inspector` drawer -> `Resolve` optimistic mutation, plus `Settings` 3-way theme control.
- **No-gos:** No custom query-builder DSL, no multi-window popouts, no decorative shadows in dark mode.

## 2. Domain and Copy Rules

| Concept / Noun | Token / Format | Copy Rule |
| --- | --- | --- |
| `Request` | `req_<id>` (`--font-mono`, `tabular-nums`) | Store UTC epoch ms, localize relative age (`relative-timestamps`), zero `Lorem ipsum` (`HRC203`) |
| `Workspace` | `ws_<slug>` (`--font-mono`, `tabular-nums`) | Concrete destructive verbs (`Delete workspace`, never `Yes`/`No`, `HRC001`) |

## 3. Foundations and Tokens (`Board 6`: `design-visuals/styles-6.png`)

- **Color roles (`src/style.css`):** `--bg`, `--surface`, `--surface-2`, `--field`, `--line`, `--text`, `--muted`, `--accent`, `--accent-fill`, `--on-accent`, `--danger`, `--warn`, `--ok`, `--focus`, and `--scrollbar-thumb` (`#767686` in dark mode, `>= 3.5:1`).
- **Typography:** Bundled `Inter` (`--font-sans`) and `Monaspace Neon` (`--font-mono`) at `24/16/12 px` dashboard defaults (`400, 500, 600, 700`) plus `11 px` `.eyebrow` (`0.06em`).
- **Icons, spacing, breakpoints, motion:** Vendored `16x16 px` Lucide SVGs matching paired text `line-height` (`ICN102`), `4 px` scale (`32 px` rows, `6 px` radius), `68em/60em/30em` width + `24em` landscape max-height, and `var(--ease)` (`cubic-bezier(0.22, 1, 0.36, 1)`).

## 4. Global Components (`Boards 5 & 7`: `design-visuals/components-5.png`, `anatomy-7.png`)

| Component | Holistic variants | In-current-use variants | States and anatomy |
| --- | --- | --- | --- |
| `.button` | `default`, `accent`, `danger`, `ghost` | `default`, `accent`, `danger` | All `6` Clause 9 states, `8px 16px` pad (`var(--space-2) var(--space-4)`), equal group widths (`HRC102`) |
| `.row` | `default`, `selected`, `loading` | `default`, `selected`, `loading` | `32 px` height (`44 px` coarse pointer), `live-dot` fixed `16x16 px` loading slot |
| `dialog.modal` | `detail`, `confirm-danger` | `confirm-danger` | `modal-manager` top-right `32x32 px` `.close-button` (`-8 px` margin) |

## 5. Major Areas (`Boards 2, 3, 4, 8`: `inbox-2.png`, `inbox-3.png`, `inbox-4.png`, `responsive-8.png`)

- **Inbox (`inbox-2.png`, `inbox-3.png`, `inbox-4.png`):** Filter bar, request table (`table-to-cards` below `60em`), and detail inspector. Covers `Default`, `Empty` (`Clear filters`), `Error` (`Retry`), and `Offline` (draft preserved).
- **Optimistic writes and memory (`page-memory`):** `POST /api/requests` renders the pending row immediately under slow network (`ACC207`) and persists `?status=` + draft text in URL/`localStorage`.
- **Responsive reflow (`responsive-8.png`):** Verified across `1920x957`, `1080x2437`, `958x958`, `855x325`, and `411x799` with `0 px` horizontal overflow (`SPC206`).

## 6. Evidence, Deviations, and Decisions (`Boards 9 & 0`: `a11y-9.png`, `diff-0.png`)

- **Audit status:** Not run (template example). Run `nuu audit -c nuu.toml -u http://127.0.0.1:8000` before landing.
- **Recipes (`nuurecipe:`):** `offline-fonts`, `live-dot`, `loading-bar`, `page-memory`, `modal-manager`, `relative-timestamps`, `scrollbars`, `table-to-cards`.
- **Approved deviations (`nuu-deviation:`):** None.
- **Decisions:** `2026-11-07` (`Accepted`) — collapse `Inspector` below `60em` so the request list keeps full reading measure.
```

## Rules for an Entry

- Mark decisions as `Proposed`, `Accepted`, or `Retired`, keeping open questions separate from settled facts.
- Mark uncaptured screenshots or unrun audits explicitly as `not captured` or `not run` instead of inventing evidence.
- Never add `nuu-deviation:` entries without explicit human approval.
- Delete an entry only together with the code it describes.
