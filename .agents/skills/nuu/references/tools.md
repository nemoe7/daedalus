# Tool reference

`python scripts/uiux.py COMMAND --help` lists arguments. Python 3.10+ and its standard library cover local tools. Browser tools also use Node and an installed, floating `playwright` package. No command installs a package or downloads a browser.

## Local commands

| Command | Input | Report |
| --- | --- | --- |
| `brief` | Optional `--input` JSON; audience, task, goal, and stack flags | Context, retained fields, and explicit unknowns |
| `search QUERY` | Optional dataset, stack, and limit | Ranked CSV records, matched terms, fallback flag, source, and coverage |
| `system --brief FILE` | Brief and project sources | Proposed catalog options and existing tokens; no product writes |
| `teach` | Optional record path and explicit `--create` | Existing authority and headings, or a proposed record |
| `assets --manifest FILE` | JSON array of asset records | Missing metadata, missing local files, and selected active-SVG patterns |
| `assets --fetch URL --destination PATH --authorize-fetch` | Explicit HTTP(S) URL and new project path | Saved bytes, hash, and metadata still required |
| `lint` | Project HTML, CSS, and script paths | Selected source candidates and per-file coverage |
| `extract` | Project CSS and HTML | Custom properties, class patterns, source locations, and coverage limits |
| `doctor` | Optional asset manifest and JSON configuration | Runtime, browser inventory, catalog hashes, assets, and configuration |

`--project` selects the source tree. `--output` saves JSON to a new file; an existing file remains unchanged. Search never executes catalog content. A matched term is not proof that a recommendation fits the product.

`teach --record PATH` selects an existing authority. Existing records remain byte-identical, including with `--create`. A new record uses the unfilled template; its example is not product evidence. Record and asset destinations remain inside the selected project, including after symlink resolution.

An asset record has string fields `path`, `source`, `license`, and `attribution`. The SVG check is not a complete security audit or a license verification. Fetches reject embedded credentials and unsafe redirect schemes, refuse overwrite, and stop above 10 MiB. Downloaded license and attribution remain unknown until verified.

## Source findings

| ID | Pattern | Criterion reference |
| --- | --- | --- |
| `UIX001` | HTML image without an `alt` attribute | WCAG 1.1.1 |
| `UIX002` | HTML form control without selected source naming evidence | WCAG 4.1.2 |
| `UIX003` | CSS outline removal | WCAG 2.4.7 |

All three are review candidates, not conformance failures. Labels, native input names, direct naming attributes, and existing label references supply source evidence. Rendered names, appropriate alternative text, and visible focus replacement still need review.

A same-line comment `noui: UIX001` suppresses only that exact ID on that physical line. Unknown IDs do not suppress another rule. JSX, TSX, JavaScript, and TypeScript receive `not_checked`, not a simulated source pass. Dynamic styles and generated markup need rendered evidence.

## Browser commands

`inspect --url URL` captures the rendered page. `flow --url URL --flow FILE` also runs a declared task. `audit` combines source, token, browser, and flow evidence; without a URL its rendered coverage is `not_checked`. `live --url URL` is read-only without an edit.

The matrix contains project widths from `--widths` plus 360 and 1280 px, light and dark color preferences, and normal and reduced motion. Preference emulation does not prove that a product supports a theme. Reports keep screenshots, computed values, overflow, browser errors, focus state, and declared flow results.

`--engine auto` selects an available installed engine. Explicit engine and executable arguments select another installed browser. Missing packages, unavailable engines, and failed launches produce `not_checked` with a reason. `doctor` checks availability, not launch or visual behavior. Nothing downloads a browser implicitly.

The default profile is `project`. `--profile house` also runs the existing `measure.py check` logic against captured values and the bundled sheet. The standalone `measure.py check` and `ruler` commands keep their existing inputs and behavior.

Use inspect --overlaps for bounded rectangle intersections, sampled top-to-bottom hit-test order, and ancestor stacking properties, and report coverage limits instead of a global z-distance.

`--artifacts` selects the screenshot root. Each invocation creates a new capture directory, so earlier screenshots remain unchanged. `--selector` limits inspected elements. `--timeout` sets each action's seconds. The default viewport height is 900 px. Flows and authorized live edits run on the first capture only; other captures show a fresh page. This scope appears in the report.

| Browser ID | Evidence |
| --- | --- |
| `UIX101` | Horizontal overflow |
| `UIX102` | Browser console or page errors |
| `UIX103` | Failed declared flow |
| `UIX104` | Failed navigation, capture, or edit |
| `UIX201` | Value outside the explicitly selected house sheet |

## Declarative flows

A flow file contains a JSON array. Actions are `goto`, `click`, `fill`, `press`, `hover`, `focus`, `offline`, `visible`, `hidden`, `focused`, `text`, `value`, `attribute`, `count`, and `screenshot`. Element actions use a CSS `selector`. Embedded code is not a supported action.

```json
[
  {"action": "focus", "selector": "#email"},
  {"action": "focused", "selector": "#email"},
  {"action": "fill", "selector": "#email", "value": "sample@example.com"},
  {"action": "press", "selector": "#email", "key": "Enter"},
  {"action": "visible", "selector": "#receipt"}
]
```

`fill` takes a string `value`; `press` takes a `key`; `offline` takes a boolean `value`. Text and value checks take a string `expected`. Attribute checks take `name` and a string or null `expected`; count checks take a nonnegative integer `expected`. Expected-value checks wait for the condition until the action timeout.

A failed action stops that flow and reports remaining actions. A flow without assertions is `actions_run`, not `passed`. Branch coverage, recovery paths, and long content depend on the supplied scenario. Frames, shadow-tree assertions, assistive technology, participant research, and real devices are not certified by these checks.

## Live edits and task modes

An edit file contains one CSS `selector`, optional string `text`, and optional `styles` object with string values. `live --edit FILE --authorize-edit` requires exactly one matching element. The report retains before/after measurements and screenshots. Edits affect only that transient rendered page, not source files.

`critique`, `polish`, `harden`, `normalize`, `animate`, `delight`, `distill`, and `onboard` combine source evidence, extracted patterns, and mode-specific review dimensions. Optional `--evidence FILE` adds an inspect or audit report, computed-value frequencies, and recorded flow actions. Animate also identifies nonzero duration candidates under reduced motion.

These reports leave contextual and visual judgments unrun. The agent's procedures and repairs use the active design authority, the task, and actual captured evidence. Automated input remains separate from participant research and real-device evidence.

## Exit status

`0`: requested local report generated without direct findings. `1`: direct findings. `2`: invalid input or an execution error. `3`: the requested browser check did not run. A zero exit is not an accessibility, visual-quality, or usability certificate; coverage fields remain part of the result.
