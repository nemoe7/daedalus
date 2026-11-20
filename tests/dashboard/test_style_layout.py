import re
from pathlib import Path

from conftest import (
  EXPECTED_DESKTOP_TILE_GAP_PX,
  EXPECTED_ICON_PX,
  EXPECTED_TABLET_TILE_GAP_PX,
  EXPECTED_TILE_PX,
  MIN_WCAG_AA_CONTRAST,
  MOBILE_TOUCH_MIN_PX,
  _wcag_luminance,
  run_app_js,
)
from fastapi.testclient import TestClient


def test_bundled_fonts(client: TestClient) -> None:
  """The dashboard preloads and serves bundled variable fonts with exact filenames."""
  page = client.get("/").text
  for name in ("inter-var.woff2", "monaspace-neon-var.woff2"):
    preload = (
      f'<link rel="preload" href="ui/fonts/{name}" as="font"'
      ' type="font/woff2" crossorigin>'
    )
    assert preload in page and page.index(preload) < page.index("ui/style.css"), name
    font = client.get(f"/ui/fonts/{name}")
    assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"
    assert font.headers["cache-control"] == "no-cache", "an update applies at once"
  assert client.get("/ui/fonts/nope.woff2").status_code == 404, "a missing font"
  assert client.get("/ui/fonts/INTER-VAR.woff2").status_code == 404, "the name is exact"


def test_phone_model_head_keeps_a_tap_area() -> None:
  """Phone model CSS defines header padding and shared chip layout."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  base_css, mobile_css = css.split("@media (max-width: 30em) {", 1)
  mobile_css = mobile_css.split("\n}", 1)[0]
  assert "th[data-sort] { cursor: pointer; }" in base_css
  assert ".models th { padding: 13px 8px; }" in mobile_css
  assert ".phone-types, .phone-chips { display: contents; }" in mobile_css, (
    "the chips of a dropped Type column join the capability chips"
  )
  assert ".models td.name .cell-value { flex-basis: 100%; }" in mobile_css, (
    "the name keeps its row above the chips"
  )


def test_pool_strip_keeps_every_card_its_own_width() -> None:
  """No pool card stretches over a row. A phone swipes the strip instead."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".pools > .pool:last-child:nth-child(odd)" not in css
  assert "repeat(auto-fill, minmax(min(136px, 100%), 1fr))" in css
  assert ".pools { display: flex; overflow-x: auto;" in css
  assert ".pools > .pool { flex: 0 0 144px;" in css


def test_phone_chips_share_one_row() -> None:
  """Phone chip CSS removes wrapper boxes and shares chip sizing."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".phone-types, .phone-chips { display: contents; }" in css, "one row"
  assert ".models td.name .chip {" in css, "one size"
  chip_block = css.split(".models td.name .chip {", 1)[1].split("}", 1)[0]
  assert "align-self: center;" in chip_block, "an icon chip sits level with a word chip"
  assert ".phone-types { display: flex" not in css, "no row of its own"
  assert ".phone-chips {\n    display: flex" not in css, "no row of its own"


def test_phone_panels_clear_the_last_row() -> None:
  """Phone CSS adds bottom padding to the main area and panels."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  mobile = css.split("@media (max-width: 30em) {", 1)[1].split("\n}", 1)[0]
  assert "#app > main { padding-bottom: 76px; }" in mobile, "the screen bottom clears"
  assert ".panel { padding-bottom: 8px; }" in mobile, "the panel edge clears"


def test_the_model_rows_light_up_on_hover() -> None:
  """Models hover CSS uses the field color only with pointer hover support."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert (
    "@media (hover: hover) {\n"
    "  table.models tbody tr:hover td { background: var(--field); }\n"
    "  tr.request.has-chain:hover td { background: var(--field); }\n"
    "}"
  ) in css, "the pointer hover lights model rows and chained request rows"


def test_notification_actions_match_the_shared_pointer_hover() -> None:
  """Notification-action hover CSS uses the shared field color only with pointer support."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  gated = "\n".join(
    re.findall(r"@media \(hover: hover\) \{\n(.*?)\n *\}", css, re.DOTALL)
  )
  bare = re.sub(r"@media \(hover: hover\) \{\n.*?\n *\}", "", css, flags=re.DOTALL)
  hover = "button.notification-item:hover, a.notification-item:hover { background: var(--field); }"
  assert hover in gated, "notification actions and links share the row hover color"
  assert "button.notification-item:hover" not in bare, (
    "a touch tap keeps no notification hover"
  )


def test_limit_events_and_rebuilds_share_a_relative_notification_feed() -> None:
  """Notification markup combines limit warnings, resets, and rebuilds with relative times."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  section = page.split('<section data-page="notifications" hidden>', 1)[1].split(
    "</section>", 1
  )[0]
  assert 'id="notification-feed"' in section
  assert 'aria-label="nav.notifications_link"' in section
  assert 'data-i18n-aria-label="nav.notifications_link"' in section
  assert 'id="reset-rows"' not in section and 'id="rebuild-rows"' not in section
  assert 'id="limit-warnings-card"' not in section
  run_app_js(
    "renderNotifications, limitMeta, icon, relative, modelName, state",
    """
const now = Math.floor(Date.now() / 1000);
probe.state.models = [{ id: 'groq/model', name: 'GPT OSS 120B' }];
probe.renderNotifications({
  resets: [{ at: now - 65, provider: 'groq', client: 'owui', kind: 'tokens', span: 'minute' },
    { at: now - 70, provider: 'groq', client: null, model: 'groq/model',
      kind: 'requests', span: 'minute' }],
  rebuilds: [{ at: now - 125, reason: 'scheduled', models: 3,
    added: ['p/new'], removed: [], changed: [], failed: ['openrouter'] }],
  update: null,
  limits: [{ at: now - 5, model: 'groq/model', client: 'owui', kind: 'requests', span: 'minute',
    remaining: 5, limit: 100, share: 0.05 },
  { at: now - 8, model: 'groq/zero', client: null, kind: 'tokens', span: 'minute',
    remaining: 0, limit: 250, share: 0, reset: now + 600 },
  { at: now - 10, model: 'groq/past-zero', client: null, kind: 'tokens', span: 'minute',
    remaining: 0, limit: 250, share: 0, reset: now - 900 }],
});
const html = node('notification-feed').innerHTML;
const modelSpan = (id) => `<span title="${id}">${probe.modelName(id)}</span>`;
const tokensPerMinute = LOCALE["limits.unit.rate_initial_text"]
  .replace("{unit}", LOCALE["limits.unit.tokens.initial_text"])
  .replace("{span}", LOCALE["limits.span.minute.initial_text"]);
const rebuildText = LOCALE["notification.catalog_rebuild_text"]
  .replace("{reason}", 'scheduled');
assert(html.includes('groq/model') && html.includes('GPT OSS 120B'), 'the warning renders marks and real name');
assert(html.includes('ui/icons/groq.svg') && html.includes('ui/icons/openrouter.svg'),
  'reset and failed-rebuild notices render provider marks');
assert(html.includes('5 of 100'), 'the warning keeps its allowance');
assert(html.includes('groq') && html.includes(tokensPerMinute),
  'the reset notification shows');
const requestsPerMinute = LOCALE["limits.unit.rate_initial_text"]
  .replace("{unit}", LOCALE["limits.unit.requests.initial_text"])
  .replace("{span}", LOCALE["limits.span.minute.initial_text"]);
const modelReset = LOCALE["notification.model_reset_text"]
  .replace("{model}", modelSpan('groq/model')).replace("{unit}", requestsPerMinute);
assert(html.includes(modelReset), 'a model-scoped reset names the model');
assert(html.includes(LOCALE["notification.model_renewed_text"]),
  'the model-scoped body says the model allowance renewed');
const reached = LOCALE["notification.limit_reached_text"].replace("{model}", modelSpan('groq/zero'));
const resets = LOCALE["notification.limit_resets_text"]
  .replace("{when}", probe.relative(now + 600));
const pastReached = LOCALE["notification.limit_reached_text"].replace("{model}", modelSpan('groq/past-zero'));
const pastResets = LOCALE["notification.limit_resets_text"]
  .replace("{when}", probe.relative(now - 900));
assert(html.includes(reached), 'a zero remainder says the limit is reached');
assert(!html.includes(LOCALE["notification.limit_near_text"].replace("{model}", modelSpan('groq/zero'))),
  'a zero remainder never reads near its limit');
assert(html.includes(resets), 'the reached notice names the reset time');
assert(html.includes(pastReached), 'a past reached notice still shows');
assert(!html.includes(pastResets), 'a past reset time is omitted from the reached notice');
const futureMeta = probe.limitMeta({ kind: 'tokens', span: 'minute', reset: now + 600, at: now - 5 });
const pastMeta = probe.limitMeta({ kind: 'tokens', span: 'minute', reset: now - 900, at: now - 5 });
assert(futureMeta.includes(LOCALE["limits.meta.reset_text"].replace("{when}", probe.relative(now + 600))),
  'a future limit row uses future tense');
assert(pastMeta.includes(LOCALE["limits.meta.reset_past_text"].replace("{when}", probe.relative(now - 900))),
  'a past limit row uses past tense');
assert(html.includes(rebuildText), 'the rebuild notification shows');
assert(html.includes(LOCALE["time.just_now_text"])
  && html.includes(LOCALE["time.minute_ago.one_text"])
  && html.includes(LOCALE["time.minute_ago.other_text"].replace("{count}", "2")),
  'the feed times are relative');
assert(html.indexOf('groq/model') < html.indexOf(rebuildText),
  'newest notifications come first');
assert(html.includes('data-rebuild="0"'), 'the rebuild notification still opens its diff');
""",
  )
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    ".notification-feed { width: 100%; max-width: 740px;" in css
    and "min-height: 40px; padding: 8px 16px;" in css
  ), "the shared card lays out notification rows"
  desk = css.split("@media (max-width: 30em) {", 1)[0]
  for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", desk):
    if "#limit-rows" in selector:
      assert "padding" not in body, "Limits columns use the shared table cell padding"


def test_the_rebuild_diff_strikes_the_old_value() -> None:
  """Old diff chips carry a strike class, and the CSS strikes them."""
  run_app_js(
    "DIFF_LABELS, diffModel",
    """
assert(!("max_tokens" in probe.DIFF_LABELS), "retired max_tokens stays out of DIFF_LABELS");
assert(probe.DIFF_LABELS.max_output_tokens === LOCALE["model.column.output_column"],
  "output limit label stays mapped");
const entry = 'z-ai/glm-4.7-flash: supported_efforts ["low", "max"]->["none", "max"], '
  + 'max_input_tokens 4096->8192';
const changed = probe.diffModel(entry);
assert(changed.includes('class="chip flag effort e-low old">'), 'a dropped effort reads struck');
assert(changed.includes('class="chip flag old">' + LOCALE["model.column.context_column"] + ' 4096</span>'),
  'the old count reads struck');
assert(changed.includes('class="chip flag effort e-none">'), 'an added effort stays plain');
assert(changed.includes('>' + LOCALE["model.column.context_column"] + ' 8192</span>'),
  'the new count stays plain');
assert(!changed.includes('e-none old'), 'no strike rides on a new effort');
""",
  )
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".diff-chips .chip.old { text-decoration: line-through; }" in css, (
    "the strike marks the old value"
  )


def test_a_card_closes_on_the_room_of_its_rows() -> None:
  """Settings and Provider card CSS uses 8px-scale insets and 40px desktop rows."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert "--radius: 10px;" in css, "panel radius follows the 10px house rule"
  assert ".card { padding: 16px; }" in css, "cards sit on the 8px spacing scale"
  assert (
    'section[data-page="settings"] .card:not(.list),\n'
    'section[data-page="providers"] .card:not(.list) { padding-top: 8px; }'
  ) in css, "the first row of a card keeps an 8px top inset"
  assert (
    'section[data-page="settings"] .card,\n'
    'section[data-page="providers"] .card { padding-bottom: 8px; }'
  ) in css, "the last row of a card keeps an 8px bottom inset"
  assert (
    'section[data-page="settings"] .field:not(.stack),\n'
    'section[data-page="providers"] .field:not(.stack) { min-height: 40px; padding: 0; }'
  ) in css, "every single-line settings row shares one 40px desktop height"
  assert ".fold summary { min-height: 40px; }" in css, (
    "closed field groups share the 40px desktop row height"
  )
  assert "#settings > .section-pane .sections button { min-height: 40px; }" in css, (
    "providers section file and block rows share the 40px desktop row height"
  )
  assert ".sections button { min-height: 40px; }" in css, (
    "desktop settings rail rows share the 40px row height"
  )
  assert ".field select { padding-block: 4px; }" in css, (
    "select controls match input control height inside 40px settings rows"
  )
  narrow = css.rsplit("@media (max-width: 60em) {", 1)[1]
  assert (
    ".sections button { min-height: 44px; padding: 10px 12px 10px 8px; gap: 10px; }"
    in narrow
  ), "narrow viewports preserve the 44px touch target floor"
  assert ".fold summary { min-height: 44px; }" in narrow, (
    "narrow viewports preserve a 44px touch target on closed field groups"
  )


def test_provider_files_use_the_shared_section_list_style() -> None:
  """Provider file rows use the same panel and button spacing as other section lists."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  sections = css.partition(".sections {")[2].partition("}")[0]
  rows = css.partition(".sections button {")[2].partition("}")[0]
  assert "#files .provider-files" not in css, (
    "the provider list does not override the shared style"
  )
  assert "background: var(--panel)" in sections, "shared lists have a panel background"
  assert "border: 1px solid var(--line)" in sections, (
    "shared lists have the standard border"
  )
  assert "border-radius: var(--radius)" in sections, (
    "shared lists have the standard radius"
  )
  assert "padding: 8px 12px 8px 6px" in rows, (
    "shared list rows match 8px symmetric tile spacing"
  )


def test_the_yaml_editor_fills_the_pane_and_saves_at_the_right() -> None:
  """YAML textarea flexes to leftover height, and Save sits at right."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".card.yaml-view textarea { flex: 1 1 auto; min-height: 120px; }" in css, (
    "the editor takes the leftover card height"
  )
  assert ".yaml-view > .editbar .hint { order: -1; margin-left: 0; }" in css, (
    "the hint yields the right edge to Save"
  )


def test_the_info_tip_lifts_off_the_card() -> None:
  """Both hover tip containers carry a shadow, so they read above the card."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert css.count("box-shadow: 0 2px 10px rgb(0 0 0 / 25%);") == 2, (
    "the field tip and the group tip both lift"
  )


def test_the_limits_table_scrolls_inside_its_panel() -> None:
  """The Limits table panel is the only scrollport on wide screens."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    'main[data-page="limits"] { display: flex; flex-direction: column; overflow: hidden; }'
    in css
  ), "the limits page holds its height instead of scrolling the document"
  assert (
    'section[data-page="limits"] > .flow > .column > .panel:not(.editbar) '
    "{ flex: 1; min-height: 0; overflow: auto; }"
  ) in css, "the table panel carries the scroll"
  assert 'section[data-page="limits"] thead th { position: sticky;' in css, (
    "the column head stays in view while the rows scroll"
  )
  assert 'section[data-page="models"] > .panel { overflow: clip; }' in css, (
    "the models panel lets sticky children anchor to the main scrollport"
  )
  assert "position: sticky; top: 0; z-index: 3; background: var(--panel);" in css, (
    "the models toolbar sticks at the top of the scrollport"
  )
  assert (
    '#app > main[data-page="models"]::before {' in css
    and "box-shadow: 0 -1px 0 0 var(--line), 0 -13px 0 1px var(--bg);" in css
  ), "the top padding gutter and toolbar corner spandrels mask scrolled rows"
  assert "position: sticky; top: 68px; z-index: 2; background: var(--panel);" in css, (
    "the models table header sticks directly under the toolbar"
  )


def test_the_limits_filter_bar_stops_where_the_table_stops() -> None:
  """The Limits filter bar takes the table column width, and field names itself."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  wide = re.search(r"@media not \(max-width: 60em\) \{(.*?)\n\}", css, re.DOTALL)
  assert wide, "the wide-page layout block"
  assert 'section[data-page="limits"] > .editbar { width: calc(100% - 276px); }' in (
    wide.group(1)
  ), "the bar stops at the table edge, clear of the balance column"
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert 'class="search-ico"' not in page, (
    "the placeholder names the field; no loose magnifier"
  )


def test_the_version_reads_in_the_mono_font() -> None:
  """Header and login version CSS uses the mono font."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".mono { font-family: var(--mono); }" in css, "the mono class exists"
  brand = css.split(".brand small {", 1)[1].split("}", 1)[0]
  assert "font-family: var(--mono);" in brand, "the header version"
  assert (
    "font-family: var(--mono);"
    in css.split(".login-head small {", 1)[1].split("}", 1)[0]
  ), "the login version"


def test_the_requests_head_keeps_its_rule_while_it_sticks() -> None:
  """Sticky request-header CSS uses an inset shadow instead of a bottom border."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"\.requests thead th \{([^}]*)\}", css)
  assert rule and "position: sticky" in rule.group(1), "the head sticks"
  assert "border-bottom: 0" in rule.group(1), "the collapsed border goes"
  assert "box-shadow: inset 0 -1px 0 var(--line)" in rule.group(1), (
    "the rule rides on the inset shadow"
  )


def test_provider_values_and_helpers_use_readable_text_sizes() -> None:
  """Provider data, chips, and helper copy stay at 14px or larger."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  rule = re.search(
    r"(?s)/\* Provider form data and helper text stay at 14px\. \*/(.*?)\{([^}]*)\}",
    css,
  )
  assert rule and "font-size: 14px;" in rule.group(2)
  for selector in (
    ".card.provider .sub",
    ".card.provider .field small",
    ".card.provider .pill",
    ".card.provider .add",
    ".card.provider .adder input",
    ".card.provider .adder select",
    ".card.provider .override input.text",
    ".card.provider .pill.hook .select",
    ".card.provider .pill.hook .menu button",
  ):
    assert selector in rule.group(1), selector


def test_panel_borders_are_soft_and_controls_keep_their_edges() -> None:
  """Panel borders stay soft, controls stay distinct, and text meets AA contrast in both themes."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  light = css.split(":root {", 1)[1].split("}", 1)[0]
  dark = css.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
  light_colors = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", light))
  dark_colors = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", dark))

  def luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
      value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
      for value in channels
    ]
    return sum(
      value * weight
      for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )

  def contrast(first: str, second: str) -> float:
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)

  for colors in (light_colors, dark_colors):
    assert 1.1 <= contrast(colors["--line"], colors["--panel"]) <= 1.3
    assert contrast(colors["--control-line"], colors["--field"]) >= 1.8
    assert contrast(colors["--text"], colors["--panel"]) >= 4.5
    assert contrast(colors["--muted"], colors["--panel"]) >= 4.5
    accent = colors.get("--accent-text", colors["--accent"])
    assert contrast(accent, colors["--panel"]) >= 4.5


def test_active_controls_have_no_resting_shadows() -> None:
  """Active navigation and buttons use borders instead of shadows."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  selectors = (
    r"\.sections button\.on",
    r"\.primary:hover:not\(:disabled\)",
    r"\.pool\.on",
    r"\.tabs a\.on",
  )
  for selector in selectors:
    rule = re.search(selector + r" \{([^}]*)\}", css)
    assert rule and "box-shadow" not in rule.group(1), selector


def test_narrow_navigation_scrolls_in_one_row() -> None:
  """Narrow headers keep one row of links and scroll it without a hard cut."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "@media (max-width: 60em) {\n  header { column-gap: 16px; }" in css
  assert (
    'header[data-wrapped="1"] { grid-template-columns: 1fr auto; grid-template-areas: "brand chips" "tabs tabs"; }'
    in css
  )
  assert ".tabs { flex-wrap: nowrap; gap: 0 16px; overflow-x: auto; }" in css
  assert ".tabs { flex-wrap: wrap" not in css, "a phone never wraps the tabs"
  assert (
    ".sections button { min-height: 44px; padding: 10px 12px 10px 8px; gap: 10px; }"
    in css
  )
  assert ".page-title" not in css
  narrow = css.rsplit("@media (max-width: 60em) {", 1)[1]
  assert (
    'main[data-page="settings"]:has(#provider-form .pane-title:not([hidden]):not(:empty))'
    " #settings > .section-pane > .pane-bar > .pane-title { display: none; }" in narrow
  )


def test_mobile_request_cards_use_route_first_grid() -> None:
  """Mobile request CSS places route, metrics, and details on separate grid rows."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  base_css = css.split("@media (max-width: 60em) {", 1)[0]
  mobile_css = css.split("@media (max-width: 60em) {", 1)[1].split(
    "@media (max-height: 24em) {", 1
  )[0]
  narrow_css = mobile_css.rsplit(".panel:has(> .requests) { margin-top: 0; }", 1)[1]
  assert ".requests td.fallbacks-cell { display: none; }" in base_css, (
    "the desktop table reads the fallback count in the chain of the row"
  )
  assert "tr.request.has-chain { cursor: pointer; }" in css, (
    "the chained row is itself the control"
  )
  assert ".caret" not in css, "no chevron glyph remains on request rows"
  assert ".open td" not in css, "no row keeps an expanded state"
  assert (
    "@media (hover: hover) {\n  table.models tbody tr:hover td "
    "{ background: var(--field); }\n  tr.request.has-chain:hover td "
    "{ background: var(--field); }\n}" in base_css
  ), "the row hover needs a pointer, a touch tap keeps no background"
  assert ".requests tr.request, .requests tr.live-row {" in mobile_css
  assert (
    "grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr) auto" in mobile_css
  ), (
    "the stamp fits its text, the client tag fills the rest, the status badge fits its own width"
  )
  assert (
    ".requests tr.request > td:nth-child(4), .requests tr.live-row > td:nth-child(4) "
    "{ grid-column: 1 / -1; grid-row: 3; }"
  ) in mobile_css
  assert (
    ".requests tr.request > td:nth-child(6), .requests tr.live-row > td:nth-child(6) "
    "{ grid-column: 1 / -1; grid-row: 5; }"
  ) in mobile_css, "the effort takes its own row after the served model"
  assert (
    ".requests tr.request > td:nth-child(7), .requests tr.live-row > td:nth-child(7) {\n"
    "    grid-column: 4; grid-row: 1; justify-content: flex-end;\n"
    "  }"
  ) in mobile_css, "the status is the badge of the first row"
  assert (
    ".requests tr.request > td:nth-child(8), .requests tr.live-row > td:nth-child(8) "
    "{ grid-column: 1 / 3; grid-row: 6; }"
  ) in mobile_css, "the input count pairs with the output count"
  assert (
    ".requests tr.request > td:nth-child(9), .requests tr.live-row > td:nth-child(9) "
    "{ grid-column: 3 / 5; grid-row: 6; }"
  ) in mobile_css, "the output count shares the row with the input count"
  assert (
    ".requests tr.request > td:nth-child(10), .requests tr.live-row > td:nth-child(10) "
    "{ grid-column: 1 / 3; grid-row: 7; }"
  ) in mobile_css, "the TTFT pairs with the stream time"
  assert (
    ".requests tr.request > td:nth-child(3), .requests tr.live-row > td:nth-child(3) "
    "{ grid-column: 1 / -1; grid-row: 2; }"
  ) in mobile_css, "the session id takes its own row under the card head"
  assert (
    ".requests tr.request > td:nth-child(5), .requests tr.live-row > td:nth-child(5) "
    "{ grid-column: 1 / -1; grid-row: 4; }"
  ) in mobile_css, "the served model takes its own row after the model"
  assert (
    ".requests tr.request > td.fallbacks-cell, .requests tr.live-row > td.fallbacks-cell {\n"
    "    display: block; grid-column: 1 / -1; grid-row: 8;\n"
    "  }"
  ) in mobile_css, "the chain details take the last row"
  assert (
    ".requests tr.request > td.fallbacks-cell.no-details,\n"
    "  .requests tr.live-row > td.fallbacks-cell.no-details { display: none; }"
  ) in mobile_css, "a card with no details keeps no empty row"
  assert (
    ".requests tr.request:active td, .requests tr.live-row:active td "
    "{ background: transparent; }"
  ) in mobile_css, "a press paints no patch behind a selection"
  assert (
    ".requests tr.request:active, .requests tr.live-row:active { background: var(--field); }"
  ) in mobile_css, "a press paints the field of the whole card"
  assert (
    ".requests tr.request > td:nth-child(1) .cell-value,\n"
    "  .requests tr.live-row > td:nth-child(1) .cell-value "
    "{ font-size: inherit; white-space: nowrap; }"
  ) in mobile_css, "the stamp holds 1 line at its own width"
  assert (
    ".models tr, .keys tr, .requests tr.request, .requests tr.live-row "
    "{ font-size: 14px; }"
  ) in mobile_css, "1 text size holds every phone card"
  assert "user-select" not in mobile_css, "the phone card keeps the text selection"
  assert (".chain-modal ol { margin: 4px 0 0; }") in mobile_css, (
    "the phone dialog keeps its own step list"
  )
  assert ".caret" not in mobile_css, "no chevron reaches the phone cards"
  assert ".chain-open" not in mobile_css, "no chain pill reaches the phone cards"
  assert ".requests .mobile-request-meta" not in mobile_css, (
    "the details drop the 3 row list"
  )
  assert ".requests .mobile-request-more" not in mobile_css, (
    "the more disclosure is gone"
  )
  assert ".requests tr.live-row > td:nth-child(1) .dot" in narrow_css
  assert "grid-column" not in narrow_css, (
    "the pair rows already fit a narrow card, so no row steps down again"
  )


def test_requests_hover_rules_need_a_pointer() -> None:
  """Selected request hover rules require pointer hover support."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  gated = "\n".join(
    re.findall(r"@media \(hover: hover\) \{\n(.*?)\n *\}", css, re.DOTALL)
  )
  bare = re.sub(r"@media \(hover: hover\) \{\n.*?\n *\}", "", css, flags=re.DOTALL)
  rules = (
    ".ghost:hover { color: var(--text); border-color: var(--muted); }",
    "table.models tbody tr:hover td { background: var(--field); }",
  )
  for rule in rules:
    assert rule in gated, f"{rule} needs a pointer device"
    assert rule not in bare, f"{rule} stays outside the pointer gate"


def test_limit_columns_stay_compact() -> None:
  """Limit CSS wraps a model name in full and keeps the unit labels on one line."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  model_cell = re.search(r"#limit-rows td\.name\s*\{([^}]*)\}", css)
  value = re.search(r"#limit-rows td\.name \.cell-value\s*\{([^}]*)\}", css)
  limit_cell = re.search(r"#limit-rows td:nth-child\(2\)\s*\{([^}]*)\}", css)
  mobile_css = css.split("@media (max-width: 30em) {", 1)[1]
  mobile_weight = re.search(r"#limit-rows \.weight\.left\s*\{([^}]*)\}", mobile_css)
  assert model_cell and "white-space: normal" in model_cell.group(1)
  assert model_cell and "max-width: 0" in model_cell.group(1)
  assert value and "display: block" in value.group(1)
  assert value and "overflow-wrap: anywhere" in value.group(1)
  assert limit_cell and "white-space: nowrap" in limit_cell.group(1)
  assert mobile_weight and "min-width: 0" in mobile_weight.group(1)
  stacked = re.search(r"#limit-rows \.weight\.left\s*\{([^}]*)\}", css)
  stacked_num = re.search(r"#limit-rows \.weight\.left \.num\s*\{([^}]*)\}", css)
  assert stacked and "flex-direction: column" in stacked.group(1)
  assert stacked_num and "font-size: 12px" in stacked_num.group(1)


def test_a_tablet_puts_the_limit_cards_under_the_table() -> None:
  """The 601 to 1000 px band stacks the provider cards, so the name column keeps its width."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  band = re.search(
    r"@media \(max-width: 60em\) \{(.*?)\n\}",
    css,
    re.DOTALL,
  )
  assert band, "the tablet band sets its own limits layout"
  rules = band.group(1)
  assert "flex-direction: column" in rules
  assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in rules


def test_requests_time_column_keeps_its_width() -> None:
  """Desktop request CSS gives model columns equal widths and leaves phones on grid layout."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  desktop = css.split("@media not (max-width: 60em) {", 1)[1].split("\n}", 1)[0]
  hint = re.search(
    r"\.requests tr:not\(\.chain\) > th, \.requests tr:not\(\.chain\) > td \{ width: 1%; \}",
    desktop,
  )
  assert hint, "the other columns keep the width of their content"
  free = re.search(
    r"\.requests tr:not\(\.chain\) > th:nth-child\(4\), \.requests tr:not\(\.chain\) > td:nth-child\(4\),\n"
    r"\s+\.requests tr:not\(\.chain\) > th:nth-child\(5\), \.requests tr:not\(\.chain\) > td:nth-child\(5\) \{\n"
    r"\s+width: 30%; max-width: 30%;",
    css,
  )
  assert free, "the model and served by columns share the free width"
  shared = re.search(
    r"\.requests tr:not\(\.chain\) > td\.name > \.cell-value \{\n"
    r"\s+display: inline-block; width: 100%; overflow: hidden; text-overflow: ellipsis;",
    desktop,
  )
  assert shared, "both name columns give the name the full width of the column"
  assert ".requests td.name { overflow: hidden; text-overflow: ellipsis; }" in css, (
    "a name past its share ends in an ellipsis"
  )
  assert (
    ".requests td.status, .requests td:nth-child(n+8):nth-child(-n+11) { text-align: right; }"
    in css
  ), "status, token, and timing values align right while headers stay left"
  phone = css.split("@media (max-width: 60em) {", 1)[1]
  assert (
    "display: grid; grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr) auto;"
    in phone
  ), "the phone row stays a grid"
  assert "width: 1%" not in phone, "the phone cells keep their grid tracks"


def test_the_live_switch_reads_blue_live_and_gray_paused() -> None:
  """Live state controls switch markup, pressed attributes, and color rules."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  run_app_js(
    "state, renderLiveToggle, esc",
    """
const button = node('live-toggle');
const attributes = {};
button.setAttribute = (name, value) => { attributes[name] = value; };
for (const catalog of [LOCALE, {
  ...LOCALE,
  'request.live_button': 'fixture-live',
  'request.live.paused_button': 'fixture-paused',
  'request.live.paused_new_button': 'fixture-waiting-{count}',
}]) {
  sandbox.DAEDALUS_LOCALE = catalog;
  for (const [paused, waiting] of [[false, 0], [true, 0], [true, 2], [false, 2]]) {
    probe.state.livePaused = paused;
    probe.state.liveWaiting = new Set(Array.from({ length: waiting }, (_, i) => i));
    probe.renderLiveToggle();
    const key = paused
      ? waiting ? 'request.live.paused_new_button' : 'request.live.paused_button'
      : 'request.live_button';
    const expected = catalog[key].replaceAll('{count}', String(waiting));
    assert.strictEqual(button.innerHTML.replace(/<[^>]*>/g, '').trim(), probe.esc(expected));
    assert.strictEqual(attributes['aria-pressed'], String(paused));
  }
}
""",
  )
  assert (
    '.live-toggle[aria-pressed="false"] { border-color: var(--accent); color: var(--accent-text); }'
    in css
  ), "blue reads Live"
  assert '.live-toggle[aria-pressed="true"]' not in css, (
    "gray reads Paused, from the ghost look"
  )
  assert 'aria-pressed="false"' in page, "the first paint reads Live"


def test_the_requests_bar_keeps_its_line_on_a_filter_selection() -> None:
  """Desktop toolbar CSS prevents wrapping and lets the search field shrink."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  desktop = css.split("@media not (max-width: 60em) {", 1)[1].split("\n}", 1)[0]
  assert (
    'section[data-page="requests"] .requests-bar { flex-wrap: nowrap; }' in desktop
  ), "the bar keeps 1 row"
  assert (
    'section[data-page="requests"] .requests-bar input[type="search"] '
    "{ flex: 1 1 8rem; min-width: 0; }" in desktop
  ), "the search field gives up its width first"
  assert (
    'section[data-page="requests"] .requests-bar .hint { margin-left: auto;'
    " white-space: nowrap; }" in desktop
  ), "the count hint holds its own line"


def test_tablet_requests_filters_wrap_without_clipping() -> None:
  """A tablet wraps Requests filters into its narrow column instead of scrolling sideways."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  narrow = css.split("@media (max-width: 60em) {", 1)[1].split("\n}", 1)[0]
  assert ".requests-bar {" in narrow and "flex-wrap: wrap;" in narrow
  assert ".requests-bar input[type=search] { flex: 1 1 10rem; min-width: 0; }" in narrow
  assert (
    'section[data-page="requests"] .requests-bar #request-clear.off { display: none; }'
    in narrow
  )
  assert ':root:not([data-keyboard-nav="true"]) :focus-visible { outline: 0; }' in css
  assert ".sections button, .notification-item," in css
  assert ".sections button:focus-visible, .notification-item:focus-visible," in css
  assert ".limits-check-stack { max-height: none; margin-block: 0; }" in narrow


def test_header_state_matches_the_page_switcher() -> None:
  """Header CSS removes chip borders and shares status spacing with the tab bar."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"\.chips \.chip, \.chips \.ghost \{([^}]*)\}", css)
  assert rule and "border: 0" in rule.group(1)
  assert "border-radius: 0" in rule.group(1) and "font-size: 14px" in rule.group(1)
  assert ".tabs { grid-area: tabs; display: flex; gap: 24px" in css
  assert "#status { padding: 0; gap: 24px; }" in css
  assert "grid-area: chips; display: flex; gap: 32px" in css, (
    "Log out stands a step apart from the status group"
  )


def test_pills_cap_their_radius() -> None:
  """Pill CSS specifies a bounded corner radius."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"(?m)^\.pill \{([^}]*)\}", css)
  assert rule and "border-radius: 10px" in rule.group(1)


def test_a_phone_keeps_the_tap_size_of_its_controls() -> None:
  """Filter-control CSS sets a 44px minimum height."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert (
    ".toolbar input, .toolbar select, .requests-bar input, .requests-bar select,\n"
    "  .tab { min-height: 44px; }"
  ) in css, "the phone keeps the 44px target of the filter controls"


def test_the_confirm_dialog_keeps_the_phone_gutter() -> None:
  """Dialog CSS caps width at 420px or the viewport width minus page gutters."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"\n\.modal \{([^}]*)\}", css)
  assert rule and "max-width: min(420px, calc(100vw - 32px));" in rule.group(1), (
    "the dialog stops at the page gutter on a phone, in place of edge to edge"
  )


def test_scroll_edge_affordances_share_the_soft_motion() -> None:
  """Scroll-edge fade registers block and inline @property lengths and scrollbar carve-outs."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  for edge in ("start", "end", "inline-start", "inline-end"):
    registered = re.search(rf"@property --edge-{edge} \{{([^}}]*)\}}", css)
    assert registered and 'syntax: "<length>";' in registered.group(1), (
      f"the {edge} mask stop can interpolate"
    )
    assert "initial-value: 0px;" in registered.group(1)
  assert "@property --fade-" not in css, "legacy JS fade properties are gone"
  assert "var(--scrollbar-carve)" in css, "vertical masks carve out the scrollbar track"
  assert "scroll-padding-block: var(--edge-fade);" in css
  assert ".tab-step" not in css, "no chevron remains to animate"


def test_a_phone_keeps_one_rhythm_around_the_bar_rules() -> None:
  """Phone bar CSS defines matching padding and gaps around rules."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  phone = re.search(r"@media \(max-width: 30em\) \{(.*?)\n\}", css, re.DOTALL)
  assert phone, "the phone media query holds the bar rules"
  assert not re.search(
    r'section\[data-page="requests"\] \.requests-bar \{([^}]*)\}', phone.group(1)
  ), "the requests bar keeps its shared rounded editbar card on mobile"
  models_bar = re.search(
    r'section\[data-page="models"\] \.toolbar \{([^}]*)\}', phone.group(1)
  )
  assert models_bar and "border-radius: var(--radius);" in models_bar.group(1), (
    "the mobile models toolbar renders as a rounded card"
  )
  panel = re.search(r"\.panel:has\(> \.requests\) \{([^}]*)\}", phone.group(1))
  assert panel and "margin-top: 0;" in panel.group(1), (
    "the flow gap alone keeps the 16px under the requests rule"
  )
  bodies = re.search(r"\.models tbody, \.keys tbody \{([^}]*)\}", phone.group(1))
  assert bodies and "margin-top: 16px;" in bodies.group(1), (
    "the first card row keeps the flow gap under the bar rule"
  )
  assert not re.search(r'\[data-page="limits"\] > \.editbar \{[^}]*padding', css), (
    "a page-scoped editbar padding breaks the shared border rhythm"
  )


def test_the_save_bars_keep_no_sticky_rule() -> None:
  """Provider and Settings page-specific selectors contain no sticky-position rules."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  for page in ("providers", "settings"):
    rules = re.findall(rf'section\[data-page="{page}"\][^{{}}]*\{{([^}}]*)\}}', css)
    assert not any("position: sticky" in body for body in rules), (
      f"a sticky Save bar hides the rows of the {page} form"
    )


def test_the_wide_rail_sticky_never_pushes_the_rail_down() -> None:
  """Wide-layout CSS places the sticky section rail at the scrollport top."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  wide = re.search(r"@media not \(max-width: 60em\) \{(.*?)\n\}", css, re.DOTALL)
  assert wide, "the wide media query holds the still-header layout"
  rail = re.search(r"\.rail \{([^}]*)\}", wide.group(1))
  assert rail and "position: sticky" in rail.group(1), (
    "the rail sticks on a wide screen"
  )
  assert "top: 0;" in rail.group(1), (
    "a top above the scrollport top pushes the rail below the pane at rest"
  )


def test_wide_settings_and_providers_scroll_only_the_section_pane() -> None:
  """Wide section CSS assigns vertical overflow to the pane and disables outer scrolling."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  wide = re.search(r"@media not \(max-width: 60em\) \{(.*?)\n\}", css, re.DOTALL)
  assert wide, "the wide section layout"
  body = wide.group(1)
  outer = re.search(
    r'#app > main\[data-page="providers"\],\n'
    r'\s+#app > main\[data-page="settings"\] \{([^}]*)\}',
    body,
  )
  assert outer and "overflow-y: hidden" in outer.group(1), (
    "the wide main page is not a second scrollport"
  )
  assert "scrollbar-gutter: auto" in outer.group(1), (
    "the disabled outer scrollport reserves no scrollbar gutter"
  )
  sections = re.search(
    r'section\[data-page="providers"\], section\[data-page="settings"\] \{([^}]*)\}',
    body,
  )
  assert sections and "flex: 1 1 auto" in sections.group(1)
  assert "min-height: 0" in sections.group(1), "the section can fit the main scrollport"
  forms = re.search(
    r'section\[data-page="providers"\] > \.settings,\n'
    r'\s+section\[data-page="settings"\] > \.settings \{([^}]*)\}',
    body,
  )
  assert forms and "flex: 1 1 auto" in forms.group(1)
  assert "min-height: 0" in forms.group(1), "the pane row takes only the free height"
  pane = re.search(r"\.section-pane \{([^}]*)\}", body)
  assert pane and "overflow-y: auto" in pane.group(1)
  assert "min-height: 0" in pane.group(1), "the section pane owns the vertical overflow"


def test_the_requests_bar_keeps_its_own_height() -> None:
  """Request CSS assigns scrolling to the table panel and keeps toolbar height independent."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert (
    'section[data-page="requests"] > .flow > .column > .panel:not(.editbar) '
    "{ flex: 1; min-height: 0; overflow: auto; }"
  ) in css, "the table panel keeps the free height"
  assert (
    'section[data-page="requests"] > .flow > .column > .requests-bar { flex: none; }'
  ) in css, "the toolbar keeps its own height"


def test_table_to_cards_recipe_grids_across_mobile_tables() -> None:
  """Mobile tables hide thead and fold into auto-fill rounded card grids with 12 px gaps."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  phone = css.split("@media (max-width: 30em) {", 1)[1].split("\n}", 1)[0]
  for selector in (".requests thead", ".models thead", ".keys thead", ".limits thead"):
    assert f"{selector} {{ display: none; }}" in phone
  assert "clip: rect" not in css
  assert (
    ".models tbody, .keys tbody {\n"
    "    display: grid; grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr));"
    " gap: 12px; margin-top: 16px;"
  ) in phone
  assert (
    "#limit-rows {\n"
    "    display: grid; grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr));"
    " gap: 12px;"
  ) in phone
  assert (
    "border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel);"
    in phone
  )


def test_a_section_row_answers_the_hold() -> None:
  """Section-button CSS uses the field background for active and hover states."""
  style = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert ".sections button:active { background: var(--field); }" in style, (
    "the hold of a section row shows as a background"
  )
  assert ".sections button:hover { background: var(--field); }" in style, (
    "a pointer keeps the same look"
  )


def test_the_notice_takes_the_red_and_wraps() -> None:
  """Notice CSS uses red text, message wrapping, and shared arrival-animation tokens."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"\n\.notice \{([^}]*)\}", css)
  assert rule, "the notice keeps its rule"
  assert "color: var(--red);" in rule.group(1), "the failure line takes our red"
  assert "box-shadow: 0 8px 20px rgb(0 0 0 / 25%);" in rule.group(1)
  assert "#notice-text { min-width: 0; overflow-wrap: anywhere; }" in css, (
    "a long message wraps in place of a clipped line"
  )


def test_the_notice_floats_without_moving_page_content() -> None:
  """Notice markup precedes page content, with fixed positioning and bounded scrolling in CSS."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  rule = re.search(r"\n\.notice \{([^}]*)\}", css)
  assert rule, "the notice keeps its rule"
  rules = dict(re.findall(r"([a-z-]+):\s*([^;]+)", rule.group(1)))
  assert rules["position"] == "fixed"
  assert rules["margin"] == "0 auto"
  assert {"left", "right", "bottom", "max-width", "max-height"} <= rules.keys()
  assert rules["overflow-y"] == "auto"
  assert rules["align-items"] == "safe center"
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  first = re.split(r"<main\b[^>]*>", page, maxsplit=1)[1].lstrip().split(">", 1)[0]
  assert first.startswith('<div class="notice"'), (
    "the alert precedes the page content in document order"
  )


def test_model_modal_heading_sizes_images_and_wraps_text() -> None:
  """Model-heading CSS sizes images and wraps text like identity values."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  rules = re.findall(r"([^{}]+)\{([^{}]+)\}", css)
  sizes = [
    selector.strip()
    for selector, declarations in rules
    if ".model-modal h3" in selector
    and re.search(r"\b(?:width|height)\s*:", declarations)
  ]
  assert sizes
  assert all(re.search(r"\bimg\.mark\b", selector) for selector in sizes), sizes
  styles = {
    selector.strip(): dict(re.findall(r"([\w-]+)\s*:\s*([^;]+)", declarations))
    for selectors, declarations in rules
    for selector in selectors.split(",")
  }
  wrap = styles[".model-id .value"]["overflow-wrap"]
  for selector in (".model-modal h3 .name", ".model-modal h3 .slug"):
    assert styles.get(selector, {}).get("overflow-wrap") == wrap


def test_dark_accent_roles_keep_text_and_controls_readable() -> None:
  """Dark theme color tokens meet text and control contrast thresholds."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  theme = css.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
  colors = dict(re.findall(r"(--[a-z-]+): (#[0-9a-f]{6})", theme))

  def luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
      x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels
    ]
    return sum(
      x * weight for x, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )

  def contrast(first: str, second: str) -> float:
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)

  text = colors.get("--accent-text", colors["--accent"])
  for background in ("--bg", "--panel", "--field"):
    assert contrast(text, colors[background]) >= 4.5
    assert contrast(colors["--red"], colors[background]) >= 4.5
  assert contrast("#ffffff", colors["--accent"]) >= 4.5
  assert contrast(colors["--accent"], colors["--panel"]) >= 3


def test_scrollbars_are_thin_and_consistent_across_containers() -> None:
  """Scrollbars use 10 px WebKit tracks, 6 px padded thumbs, and non-WebKit thin fallbacks."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  root = re.search(r":root\s*\{([^}]+)\}", css)
  tabs = re.search(r"\.tabs\s*\{([^}]+)\}", css)
  assert root is not None and tabs is not None
  assert "--scrollbar-thumb: #767682;" in root.group(1)
  assert "::-webkit-scrollbar { width: 10px; height: 10px; }" in css
  assert "border: 2px solid var(--bg);" in css
  assert "background-clip: padding-box; border-radius: 9999px;" in css
  assert ':root[data-keyboard-nav="true"] ::-webkit-scrollbar-thumb:hover' in css
  assert "::-webkit-scrollbar-button { display: none; width: 0; height: 0; }" in css
  assert "@supports not selector(::-webkit-scrollbar)" in css
  assert (
    "scrollbar-width: thin; scrollbar-color: var(--scrollbar-thumb) var(--bg);" in css
  )
  assert "scrollbar-width: none;" in tabs.group(1)


def test_scrollable_elements_follow_scroll_edge_fade_recipe() -> None:
  """Scrollable containers and inputs use the scroll-driven edge-fade recipe."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "--edge-fade: 28px; --edge-ramp: 36px; --scrollbar-carve: 14px;" in css
  for prop in ("--edge-start", "--edge-end"):
    registered = re.search(rf"@property {prop} \{{([^}}]*)\}}", css)
    assert registered and 'syntax: "<length>";' in registered.group(1)
    assert "initial-value: 0px;" in registered.group(1)
  assert "@supports (animation-timeline: scroll(self))" in css
  assert "animation: edge-start linear both, edge-end linear both;" in css
  assert (
    "animation-range: 0px var(--edge-ramp), calc(100% - var(--edge-ramp)) 100%;" in css
  )
  for target in (
    '#app > main:not([data-page="models"])',
    ".section-pane:not(.fit-host)",
    ".step pre",
    ".notice",
    "textarea",
  ):
    assert target in css


def test_new_provider_insets_focus_outline_and_keeps_mobile_touch_floor() -> None:
  """The New provider button insets its focus halo inside `.section-pane` and reaches 44 px below 900 px."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "#new-provider:focus-visible" in css
  narrow = "\n".join(
    re.findall(r"@media \(max-width: 60em\) \{\n(.*?)\n\}\n", css, re.DOTALL)
  )
  assert "#new-provider," in narrow


def test_section_search_single_result_hides_empty_groups_and_dividers() -> None:
  """A single visible section result hides empty rail groups and draws no row divider."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  rows = css.partition(".sections button {")[2].partition("}")[0]
  assert "border-bottom" not in rows
  assert (
    ".sections button:not([hidden]) ~ button:not([hidden]) { border-top: 1px solid var(--line); }"
    in css
  )
  run_app_js(
    "state, filterRail",
    """
const makeButton = (section, label) => ({
  dataset: { section },
  textContent: label,
  hidden: false,
  querySelector: () => null,
});
const makeNav = (items) => ({
  hidden: false,
  buttons: items.map(([key, label]) => makeButton(key, label)),
  querySelectorAll(sel) { return sel === 'button' ? this.buttons : []; },
});
const empty = { hidden: true };
const navs = [
  makeNav([['routing', 'Routing'], ['affinity', 'Affinity']]),
  makeNav([['privacy', 'Privacy'], ['hooks', 'Hooks']]),
  makeNav([['personalization', 'Personalization']]),
];
const host = {
  querySelectorAll: (sel) => (sel === ':scope > .rail > .sections' ? navs : []),
  querySelector: (sel) => (sel === ':scope > .rail > .rail-empty' ? empty : null),
};
probe.state.settingsSearch = 'personalization';
probe.filterRail(host);
assert.deepStrictEqual(navs.map((nav) => nav.hidden), [true, true, false]);
assert.strictEqual(navs[2].buttons[0].hidden, false);
assert.strictEqual(empty.hidden, true);

probe.state.settingsSearch = 'routing';
probe.filterRail(host);
assert.deepStrictEqual(navs.map((nav) => nav.hidden), [false, true, true]);
assert.deepStrictEqual(navs[0].buttons.map((b) => b.hidden), [false, true]);
assert.strictEqual(empty.hidden, true);
""",
  )


def test_modal_close_icon_button_rest_hover_and_active() -> None:
  """Modal close X has no resting fill/border and shows fill + text color on hover and active."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    "border-color: transparent; background: transparent; color: var(--muted);" in css
  )
  assert ".ghost.modal-close:hover {" in css
  assert ".ghost.modal-close:active {" in css


def test_table_panels_apply_inline_scroll_edge_fade() -> None:
  """Scrolling table panels combine inline scroll(self inline) fades with bottom vertical fade."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "@property --edge-inline-start {" in css
  assert "@property --edge-inline-end {" in css
  assert (
    "animation-timeline: scroll(self), scroll(self inline), scroll(self inline);" in css
  )
  assert "mask-composite: intersect;" in css


def test_semantic_category_tiles_match_vertical_and_horizontal_spacing() -> None:
  """Settings category tiles use 24px semantic boxes and symmetric 8px/10px spacing."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    f"width: {EXPECTED_TILE_PX}px; height: {EXPECTED_TILE_PX}px; border-radius: 6px;"
    in css
  )
  assert (
    "background: transparent; color: var(--muted); border: 1px solid transparent;"
    in css
  )
  assert (
    "background: var(--accent); color: var(--on-accent); border-color: var(--accent);"
    in css
  )
  assert (
    f".tile svg {{ width: {EXPECTED_ICON_PX}px; height: {EXPECTED_ICON_PX}px; }}" in css
  )
  assert (
    f"gap: {EXPECTED_DESKTOP_TILE_GAP_PX}px;\n"
    f"  padding: {EXPECTED_DESKTOP_TILE_GAP_PX}px 12px {EXPECTED_DESKTOP_TILE_GAP_PX}px "
    f"{EXPECTED_DESKTOP_TILE_GAP_PX - 2}px;"
  ) in css
  assert (
    f".sections button {{ min-height: {MOBILE_TOUCH_MIN_PX}px; "
    f"padding: {EXPECTED_TABLET_TILE_GAP_PX}px 12px {EXPECTED_TABLET_TILE_GAP_PX}px "
    f"{EXPECTED_TABLET_TILE_GAP_PX - 2}px; gap: {EXPECTED_TABLET_TILE_GAP_PX}px; }}"
  ) in css
  assert "#sort-small, #key-name { flex: 1 1 100%; width: 100%; }" in css


def test_dark_bad_contrast_and_maintenance_hint_spacing() -> None:
  """Dark --bad meets WCAG AA contrast on --panel and #update-card keeps inline hint spacing."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  dark_block = css[css.index(':root[data-theme="dark"]') :]
  panel_match = re.search(r"--panel:\s*(#[0-9a-fA-F]{6})", dark_block)
  bad_match = re.search(r"--bad:\s*(#[0-9a-fA-F]{6})", dark_block)
  assert panel_match is not None and bad_match is not None
  low, high = sorted(
    (_wcag_luminance(panel_match.group(1)), _wcag_luminance(bad_match.group(1)))
  )
  assert (high + 0.05) / (low + 0.05) >= MIN_WCAG_AA_CONTRAST
  assert "#update-card > .editbar .hint { margin-left: 0; }" in css
