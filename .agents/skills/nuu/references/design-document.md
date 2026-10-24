# The design document

Keep one authoritative design record per repository, using the existing record or docs/design.md when none exists.

## Sections

Use these eleven sections for new design records, and map their contents onto existing records without forcing heading changes.

1. **Purpose and status**: Record the product goal, scope, constraints, acceptance checks, owner, and current design status.
2. **Users, tasks, and language**: Record supported user groups, primary tasks, domain objects, terminology, and the evidence behind stated needs.
3. **Journeys and information architecture**: Record entry points, navigation, task paths, branch conditions, exits, and recovery routes.
4. **Screens and states**: Record each screen and its applicable default, loading, empty, error, offline, disabled, and permission states.
5. **Design system and tokens**: Record color, type, icons, layout, breakpoints, themes, motion, and their authoritative token sources.
6. **Components and contracts**: Record component sources, variants, inputs, states, interaction contracts, and justified local exceptions.
7. **Accessibility and inclusion**: Record the accessibility target, applicable criteria, test methods, results, exceptions, and unverified coverage.
8. **Content and assets**: Record interface copy, locale rules, user-content constraints, asset sources, licenses, and credits.
9. **Behavior and data**: Record preferences, defaults, persistence, validation, mutations, retry rules, and data-dependent behavior.
10. **Evidence and handoff**: Link dated screenshots, measurements, task-flow results, research findings, implementation readiness, and known limits.
11. **Decisions and history**: Record significant decisions with their status, context, evidence, tradeoffs, implementation links, and change history.

Visual-reference template: [design-visuals.md](design-visuals.md).

## Filled example

```markdown
# Design example

## Purpose and status

- Goal: route requests through the inbox and settings.
- Scope: inbox and settings.
- Constraints, acceptance checks, and owner: <constraints; acceptance checks; design owner>.
- Design status: proposed example.

## Users, tasks, and language

- User groups, primary tasks, and research: <supported user groups; primary tasks; research links>.
- Request: one item a person sent. Identifier `req_<number>`.
- Owner: the person who answers it.

## Journeys and information architecture

- Entry points, navigation, task paths, branch conditions, exits, and recovery routes: <entry points; navigation; task paths; branches; exits; recovery routes>.

## Screens and states

- Inbox: filters, list, inspector.
- Settings: preferences and the theme control.
- Default, loading, disabled, and permission coverage: <test methods and results for default, loading, disabled, and permission states>.
- Inbox empty: says no request matches, and offers Clear filters.
- Inbox failed: names the failed load and offers Retry.
- Offline: a banner above the list, and the write keeps its draft.

## Design system and tokens

### Colour
- Roles: bg, surface, surface-2, field, line, text, muted, accent, accent-fill, on-accent, danger, warn, ok.
- Light and dark resolve the same names. Body-text target: 4.5:1 in both. Contrast checks: <contrast test method, results, and evidence links>.

### Type
- Inter at 400, 600, 800. Sizes 11, 12, 13, 14, 15, 20, 28 px.
- IDs use the mono face with tabular figures.

### Icons
- Lucide, vendored one glyph at a time, ISC licence beside each file.
- 18 px on every surface.

### Layout
- One 1280 px column with 32 px gutters.
- 1200 px: inspector takes 320 px. 900 px: the opened item fills the page. 640 px: tables stack.

### Theme and motion
- Theme control: System, Light, Dark.
- One curve `cubic-bezier(0.22, 1, 0.36, 1)`, 160 to 220 ms. Reduced motion stops it.

- Authoritative token sources: <project token file paths or design-system URLs>.

## Components and contracts

- `row`: height 40 px, padding 0 16 px, border-bottom 1 px line. States: default, hover, selected.
- `pill`: dot and word, 24 px high, radius 10 px.
- Component sources, inputs, contracts, and exceptions: <component sources; inputs; interaction contracts; local exceptions>.

## Accessibility and inclusion

- Accessibility target, applicable criteria, methods, results, and exceptions: <target; applicable criteria; methods; results; exceptions>.
- Coverage: <verified coverage and remaining gaps>.

## Content and assets

- Interface copy, locale rules, user-content constraints, asset paths, and credits: <interface copy; locale rules; user-content constraints; asset paths; credits>.
- Proposed icon source and license: see Design system and tokens.

## Behavior and data

- Theme: System by default; Light and Dark are explicit choices.
- Page size: 20 rows.
- Persistence, validation, mutations, retries, and data-dependent behavior: <persistence; validation; mutations; retry rules; data-dependent behavior>.

## Evidence and handoff

- Visual references: [Inbox workflow](design-visuals/inbox.md), [Shared components](design-visuals/components.md), and [Style reference](design-visuals/styles.md).
- Screenshots, measurements, task-flow checks, and participant research: <dated screenshots; measurements; task-flow results; participant research links>.
- Implementation readiness: <readiness status and unresolved blockers>.
- This example is not verification evidence.

## Decisions and history

- Decision status: proposed example. Context, evidence, tradeoffs, and implementation links: <decision context; supporting evidence; tradeoffs; implementation links>.
- Historical example entry:
- 2026-03-04: inspector width at 75em set to 320 px.
```

## Rules for an entry

- Mark decisions as proposed, accepted, or retired, and keep unresolved questions distinct from settled implementation facts.
- Mark missing research, measurements, approvals, and test coverage as unknown or not run instead of inventing evidence.
- Keep composite image boards — overview wireframe flow boards with navigation arrows, detail wireframes, side-by-side workflow views, multi-state component boards, shared component family sheets, and foundation style boards — in separate local visual-reference documents, and link them from the authoritative design record.
- Delete an entry only with the code it describes.
