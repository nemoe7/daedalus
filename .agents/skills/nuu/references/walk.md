# Walk Reference (`nuu walk` / `scripts/walk.py`)

`nuu walk` drives live pages in dark mode across three modes: automatic crawl, persistent CDP interactive sessions (`-S`), and deterministic Python `@step` scripts (`nuu walk <walk.py>`). Output artifacts default to `./nuu-artifacts` (with an automatic `*` `.gitignore`), animated captures encode pure-Python `GIF89a` via `-g <out.gif>`, and optional Playwright `.webm` recording runs on `--video [out.webm]`.

## Modes

1. **Auto-crawl (`nuu walk [url_or_port_or_html]`):** Crawls same-origin links, `[role="tab"]`, `<dialog>` openers, and non-destructive buttons across `[audit].viewports`, checking `RUN001`, `HRC203`, and `ACC205` to `ACC207`.
2. **Interactive MCP session (`nuu walk [-S <name>] <verb> ...`):** Launches detached headless Chromium with `--remote-debugging-port=0`, stores `{pid, ws_endpoint, current_tab, refs, gif_frames}` under `$XDG_RUNTIME_DIR/nuu-walk-<session>.json` (or `/tmp/nuu-walk-<uid>-<session>.json`, `0600`), and reconnects via `playwright.chromium.connect_over_cdp()`.
3. **Batch Python script (`nuu walk <walk.py>`):** Runs `@step` functions in definition order and writes named board PNGs via `w.view("<name>")` into `-o` (`./nuu-artifacts`).

## `42` Interactive CLI Verbs

`<target>` accepts a deterministic ARIA snapshot ref (`e1`..`eN`) or a CSS selector.

| Group | CLI verb signatures |
| --- | --- |
| Lifecycle & tabs (`7`) | `open <url>`, `close`, `goto <url>`, `back`, `forward`, `reload`, `tab <list\|new [url]\|select <idx>\|close [idx]>` |
| ARIA snapshot (`2`) | `snapshot [--boxes] [--depth <n>] [<target>]`, `find <text\|/regex/>` |
| Pointer & form (`11`) | `click <target> [--double] [--button <left\|right\|middle>] [--mod <Alt\|Control\|Meta\|Shift>...]`, `dblclick <target>`, `hover <target>`, `drag <src> <dst>`, `fill <target> <value>`, `type <target> <text> [--slowly] [--submit]`, `select <target> <val>...`, `check <target>`, `uncheck <target>`, `upload <target> [<file>...]`, `press <key>` |
| Raw input (`2`) | `wheel <dx> <dy>`, `mouse-xy <move <x> <y>\|click <x> <y>\|down\|up\|drag <x1> <y1> <x2> <y2>>` |
| Dialogs & media (`3`) | `dialog <accept [prompt_text]\|dismiss>`, `resize <width> <height>`, `emulate [--scheme <dark\|light\|no-preference>] [--motion <reduce\|no-preference>] [--contrast <more\|no-preference>]` |
| Network & DevTools (`7`) | `console [<error\|warning\|info\|all>]`, `requests [<url-substr>]`, `request <idx>`, `route <glob> [--status <code_int>] [--body <str_or_@file>] [--content-type <mime>] [--abort]`, `unroute [<glob>]`, `offline <true\|false>`, `eval <js_expr>` |
| Storage (`4`) | `storage <save <json>\|load <json>>`, `cookie <list\|get <name>\|set <name> <val> [--url <u_or_domain>]\|delete <name>\|clear>`, `localstorage <list\|get <k>\|set <k> <v>\|delete <k>\|clear>`, `sessionstorage <list\|get <k>\|set <k> <v>\|delete <k>\|clear>` |
| Timing & capture (`6`) | `wait [--text <str>\|--text-gone <str>\|--selector <css>\|--sec <float>]`, `screenshot [<target>] [-o <png>] [--full-page]`, `pdf -o <pdf>`, `gif-start`, `gif-frame`, `gif-stop -o <gif>` |

## Batch Script API (`nuu walk <walk.py>`)

`DEVICE_INDEX = {"desktop": 0, "laptop": 1, "tablet": 2, "landscape": 3, "mobile": 4}` maps aliases onto `[audit].viewports` in `nuu.toml`; `@step` also accepts an explicit `"WxH"` string (`"1920x957"`, `"411x799"`).

```python
from walk import Demo, Walker, step


@step("desktop")
def verify_dashboard_and_save_views(w: Walker, demo: Demo) -> None:
  w.goto(1500)
  assert w.errors == []
  w.view("dashboard")
  demo.sign_in(False)
  demo.wait_until_checked(False)
  w.goto(2500)
  w.view("signed-out")
```

```bash
nuu walk -c nuu.toml scripts/walk_app.py -o nuu-artifacts -g nuu-artifacts/flow.gif
```
