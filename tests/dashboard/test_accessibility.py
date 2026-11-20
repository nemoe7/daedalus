import json
from pathlib import Path

import pytest
from conftest import (
  run_app_js,
)


def test_pool_filters_replace_tier_buttons() -> None:
  """Pool links retain valid tiers, while type changes clear tiers without changing search or sort."""
  run_app_js(
    "state, applyModelFilters, markPools, POOL_TIERS, MODES, FLAGS",
    """
sandbox.URLSearchParams = URLSearchParams;
sandbox.history = { replaceState() {} };
node('sort-small').options = [];
const cards = Object.values(probe.POOL_TIERS).map(tier => ({
  dataset: {tier, mode: 'chat'}, on: false, attributes: new Map(),
  classList: {toggle(name, on) { this.owner.on = on; }},
  setAttribute(name, value) { this.attributes.set(name, String(value)); },
  getAttribute(name) { return this.attributes.get(name) ?? null; },
  removeAttribute(name) { this.attributes.delete(name); },
}));
for (const card of cards) card.classList.owner = card;
const queryAll = sandbox.document.querySelectorAll;
sandbox.document.querySelectorAll = selector => selector === '#pools .pool' ? cards : queryAll(selector);
probe.applyModelFilters('');
const defaultTier = probe.state.tier;
const tiers = [...new Set(Object.values(probe.POOL_TIERS))].filter(tier => tier !== defaultTier);
const modes = [...Object.keys(probe.MODES), ...Object.keys(probe.FLAGS), 'all'];
for (const tier of tiers) {
  for (const mode of modes) {
    probe.applyModelFilters(new URLSearchParams({tier, mode}).toString());
    const nonChat = Object.hasOwn(probe.MODES, mode) && mode !== 'chat';
    assert.strictEqual(probe.state.tier, nonChat ? defaultTier : tier);
    assert.strictEqual(node('mode').value, mode);
    for (const card of cards) {
      assert.strictEqual(card.getAttribute('aria-current'), card.on ? String(card.on) : null);
    }
    if (!nonChat) {
      const active = cards.find(card => card.on);
      assert(active && active.dataset.tier === tier);
      probe.applyModelFilters(active.href.split('?')[1]);
      assert.strictEqual(probe.state.tier, defaultTier);
      assert(cards.every(card => !card.on));
      assert(cards.every(card => card.getAttribute('aria-current') === null));
    }
  }
  for (const mode of modes) {
    probe.applyModelFilters(new URLSearchParams({tier, mode: 'chat', sort: 'weight'}).toString());
    const search = 'fixture';
    node('search').value = search;
    const sort = probe.state.sort;
    node('mode').value = mode;
    for (const handler of node('mode').handlers.change) handler({target: node('mode')});
    assert.strictEqual(probe.state.tier, defaultTier);
    assert.strictEqual(probe.state.mode, mode);
    assert.strictEqual(node('search').value, search);
    assert.strictEqual(probe.state.sort, sort);
  }
}
""",
  )
  html = (
    Path(__file__).resolve().parents[2] / "daedalus/dashboard/ui/index.html"
  ).read_text()
  assert 'id="tiers"' not in html


def test_error_notice_dismissal_respects_reduced_motion() -> None:
  """Reduced motion hides the notice without an animation event."""
  run_app_js(
    "showNotice, closeNotice",
    """
const notice = node('notice');
const classes = new Set();
notice.classList = { add: name => classes.add(name), remove: name => classes.delete(name) };
let completeLeave = null;
sandbox.gsap = {
  killTweensOf() {},
  fromTo() {},
  to(_el, toVars) { completeLeave = toVars.onComplete || null; },
};
for (const reduced of [false, true]) {
  completeLeave = null;
  sandbox.matchMedia = query => ({ matches: query === '(prefers-reduced-motion: reduce)' && reduced });
  probe.showNotice(LOCALE['error.dashboard_unreachable_text']);
  assert.strictEqual(notice.hidden, false);
  assert.strictEqual(classes.has('leave'), false);
  probe.closeNotice();
  assert.strictEqual(notice.hidden, reduced);
  assert.strictEqual(classes.has('leave'), !reduced);
  if (!reduced) {
    assert.strictEqual(typeof completeLeave, 'function');
    completeLeave();
    assert.strictEqual(notice.hidden, true);
    assert.strictEqual(classes.has('leave'), false);
  }
}
""",
  )


def test_provider_text_inputs_have_accessible_names() -> None:
  """Provider inputs use their field labels as accessible names without placeholders."""
  run_app_js(
    "state, providerCard",
    """
probe.state.providerDefaults = {};
probe.state.env = [];
for (const kind of ['env', 'db']) {
  const html = probe.providerCard('cloudflare', {
    api_key: `${kind}:FIXTURE_KEY`, account_id: `${kind}:FIXTURE_KEY`,
    api_base: 'https://fixture.invalid/v1',
  });
  const inputs = html.match(/<input[^>]* readonly[^>]*>/g) || [];
  assert(inputs.length > 0, 'the provider form has text inputs');
  for (const input of inputs) {
    const label = input.match(/data-value="([^"]+)"/)?.[1];
    assert(label && input.includes(`aria-label="${label}"`), input);
  }
}
""",
  )


def test_value_editor_restores_focus_to_rebuilt_field() -> None:
  """Save returns focus to the rebuilt value field only when the rebuild dropped it."""
  run_app_js(
    "editValue",
    """
node('modal').showModal = () => {};
const box = node('modal-input');
box.removeAttribute = (name) => { delete box[name]; };
box.removeEventListener = (type, fn) => { box.handlers[type] = box.handlers[type].filter(item => item !== fn); };
box.checkValidity = () => box.pattern !== '(?!)';
sandbox.Event = class { constructor(type) { this.type = type; } };
sandbox.document.body = { id: 'body' };
const input = node('set-synthetic');
input.dataset.value = 'synthetic';
input.dispatchEvent = () => {};
let focused = 0;
input.focus = () => { focused += 1; };
(async () => {
  for (const [drop, expected] of [[true, 1], [false, 0]]) {
    input.type = 'text';
    input.value = '1';
    focused = 0;
    sandbox.document.activeElement = input;
    let writes = 0;
    const done = probe.editValue(input, () => {
      writes += 1;
      sandbox.document.activeElement = drop ? sandbox.document.body : { id: 'other' };
    });
    box.value = '2';
    box.handlers.input.forEach(fn => fn());
    sandbox.__settle(true);
    await done;
    assert.strictEqual(writes, 1);
    assert.strictEqual(focused, expected, 'focus returns only when the rebuild dropped it');
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
  )


def test_provider_summary_has_help_without_nested_button() -> None:
  """Summary markup includes escaped help text and no nested button."""
  run_app_js(
    "fold",
    r"""
const html = probe.fold('synthetic <label>', 'synthetic <help>', '<input>');
const summary = html.split('</summary>')[0];
assert(!summary.includes('<button'));
assert(/<summary aria-describedby="hint-\d+">/.test(summary));
assert(summary.includes('class="hint" aria-hidden="true"'));
assert(summary.includes('role="tooltip" aria-hidden="true"'));
assert(summary.includes('synthetic &lt;label&gt;'));
assert(summary.includes('synthetic &lt;help&gt;'));
""",
  )


def test_model_names_supply_native_dialog_buttons() -> None:
  """Model-name markup includes a button, an accessible label, and a dialog target."""
  run_app_js(
    "state, selectRows",
    """
probe.state.models = [{ id: 'synthetic/model', mode: 'chat', flags: [], tier: '', order: 1 }];
probe.selectRows();
const html = node('models').innerHTML;
assert(html.includes('class="model-open" type="button"'));
assert(html.includes('aria-label="synthetic/model"'));
assert(html.includes('aria-haspopup="dialog"'));
assert(html.includes('aria-controls="modal"'));
assert(html.includes('data-model="synthetic/model"'));
""",
  )


def test_value_editor_keyboard_activation_uses_shared_editor() -> None:
  """Enter, Space, and click handlers call the shared editor with the write callback."""
  run_app_js(
    "bindValueRows, setEditor: fn => { editValue = fn; }",
    """
const host = node('synthetic-host');
const input = node('synthetic-value');
let opened = 0, prevented = 0;
const write = () => assert.fail('unexpected write');
probe.setEditor((target, callback) => {
  assert.strictEqual(target, input);
  assert.strictEqual(callback, write);
  opened += 1;
});
probe.bindValueRows(host, write);
assert(host.handlers.keydown?.length);
const send = (type, key, repeat = false) => host.handlers[type].forEach(fn => fn({
  target: input, key, repeat, preventDefault: () => { prevented += 1; },
}));
send('keydown', 'ArrowDown');
send('keydown', 'Enter', true);
assert.strictEqual(opened, 0);
for (const key of ['Enter', ' ']) send('keydown', key);
send('click');
assert.strictEqual(opened, 3);
assert.strictEqual(prevented, 3);
input.disabled = true;
send('keydown', 'Enter');
send('click');
input.disabled = false;
input.closest = () => null;
send('keydown', 'Enter');
assert.strictEqual(opened, 3);
assert.strictEqual(prevented, 3);

const providerInput = node('provider-value');
providerInput.closest = (sel) => (
  sel === '#provider-form' ? node('provider-form') : sel === '[data-value]' ? providerInput : null
);
node('settings').handlers.click.forEach(fn => fn({
  target: providerInput, preventDefault: () => assert.fail('settings must ignore provider-form rows'),
}));
assert.strictEqual(opened, 3);
""",
  )


def test_request_chains_open_dialog_and_keep_focus() -> None:
  """The chained row itself opens the dialog, and a redraw returns focus to it."""
  run_app_js(
    "state, renderRequests",
    """
const row = JSON.parse(fs.readFileSync('scripts/pages_fixtures.json', 'utf8')).requests
  .find(item => item.attempts?.length > 1);
assert(row);
const plain = {at: row.at + 1, model: 'p/plain', status: 200,
  attempts: [{model: 'p/plain', result: 'answered', seconds: 1}]};
probe.state.requests = [row, plain];
probe.renderRequests([row, plain]);
const html = () => node('requests').innerHTML;
assert(html().includes(`data-chain-at="${row.at}"`), 'the chained row is itself the control');
assert(html().includes('tabindex="0"'), 'the chained row takes focus');
assert(html().includes(LOCALE['request.chain.show_title']));
assert(!html().includes('request-toggle'), 'no separate chevron button remains');
assert(!html().includes('caret'), 'no caret glyph remains');
assert(!html().includes('only-sm'), 'no phone-only duplicate of the time remains');
assert(!html().includes(`data-chain-at="${plain.at}"`), 'a chain-less row stays inert');
assert(!html().includes('aria-expanded'), 'no disclosure state remains');
let focused = 0;
let prevented = 0;
const stop = () => { prevented += 1; };
const tr = {
  dataset: {chainAt: String(row.at)},
  closest: selector => selector === '[data-chain-at]' || selector === 'tr[data-chain-at]' ? tr : null,
  focus: () => { focused += 1; sandbox.document.activeElement = tr; },
};
const click = (target) => node('requests').handlers.click.forEach(fn => fn({target}));
const key = (target, name) => node('requests').handlers.keydown.forEach(fn => fn({
  target, key: name, preventDefault: stop,
}));
click(tr);
assert(node('modal').shown, 'clicking the row opens the dialog');
assert(node('chain-modal-body').innerHTML.includes('<li class="step'), 'the dialog lists every attempt');
assert(node('modal-title').textContent.length > 0, 'the dialog carries a title');
const iconRow = {at: 99, model: 'groq/synthetic', status: 500, attempts: [
  {model: 'groq/synthetic', result: 'error', seconds: 1},
  {model: 'openai/synthetic', result: 'answered', seconds: 1}]};
probe.state.requests = [iconRow];
probe.renderRequests([iconRow]);
const tr2 = {
  dataset: {chainAt: '99'},
  closest: selector => selector === '[data-chain-at]' || selector === 'tr[data-chain-at]' ? tr2 : null,
  focus: () => {},
};
click(tr2);
const steps = node('chain-modal-body').innerHTML;
assert(steps.includes('ui/icons/groq.svg') && steps.includes('ui/icons/openai.svg'),
  'every chain step shows the vendored mark');
assert(!steps.includes('<b>groq/synthetic</b>'), 'no raw slug remains in a step');
const bare429 = {at: 101, model: 'mistral/mistral-embed-2312', via: null, status: 429, seconds: 0.002, attempts: null};
probe.state.requests = [bare429];
probe.renderRequests([bare429]);
assert(html().includes('data-chain-at="101"'), 'a failed row without attempts still opens a chain');
const tr3 = {
  dataset: {chainAt: '101'},
  closest: selector => selector === '[data-chain-at]' || selector === 'tr[data-chain-at]' ? tr3 : null,
  focus: () => {},
};
node('modal').shown = false;
click(tr3);
assert(node('modal').shown, 'clicking a failed row without attempts opens the dialog');
assert(node('chain-modal-body').innerHTML.includes('ui/icons/mistral.svg'),
  'the fallback step shows the requested model mark');
assert(node('chain-modal-body').innerHTML.includes(LOCALE['request.status.rate_limited_text']),
  'the fallback step shows the rate-limit status note');
probe.state.requests = [row, plain];
node('modal').shown = false;
key(tr, 'Enter');
assert(node('modal').shown, 'enter on the row opens the dialog');
node('modal').shown = false;
key(tr, ' ');
assert(node('modal').shown, 'space on the row opens the dialog');
assert.strictEqual(prevented, 2, 'the row keys swallow the page scroll');
node('modal').shown = false;
click({closest: () => null});
key({closest: () => null}, 'Enter');
assert(!node('modal').shown, 'a chain-less surface opens nothing');
sandbox.document.activeElement = tr;
node('requests').querySelectorAll = () => [tr];
probe.renderRequests([]);
probe.renderRequests([row, plain]);
assert.strictEqual(focused, 2, 'each redraw returns focus to the chained row');
""",
  )


@pytest.mark.parametrize(
  "phone,focused,expected",
  [
    (True, "header", "small"),
    (True, "small", "small"),
    (True, "outside", "outside"),
    (False, "small", "header"),
    (False, "header", "header"),
    (False, "outside", "outside"),
  ],
)
def test_sort_header_focus_follows_layout(phone, focused, expected) -> None:
  run_app_js(
    "state, renderSortHeads",
    f"const layout = {json.dumps([phone, focused, expected])};\n"
    + r"""
let [phone, focused, expected] = layout;
const keys = ['id', 'tier', 'weight'];
const buttons = keys.map(key => ({ id: key, tabIndex: 0, disabled: false,
  focus() { sandbox.document.activeElement = this; } }));
const heads = keys.map((key, index) => ({
  dataset: { sort: key }, hidden: false, attrs: {},
  setAttribute(name, value) { this.attrs[name] = value; },
  querySelector: () => buttons[index],
  contains: element => element === buttons[index],
}));
const small = node('sort-small');
small.options = keys.map(value => ({ value }));
small.focus = () => { sandbox.document.activeElement = small; };
const outside = node('search');
const focus = { header: buttons[2], small, outside };
sandbox.document.activeElement = focus[focused];
sandbox.document.querySelectorAll = selector => selector === '#model-head th' ? heads : [];
sandbox.matchMedia = query => ({ matches: query === '(max-width: 30em)' && phone,
  addEventListener() {} });
probe.state.sort = { key: 'weight', dir: -1 };
probe.renderSortHeads();
assert(buttons.every(button => button.tabIndex === (phone ? -1 : 0)));
assert(buttons.every(button => button.disabled === false));
assert(heads.every(head => !head.hidden && head.attrs['aria-hidden'] !== 'true'));
assert.deepStrictEqual(heads.map(head => head.attrs['aria-sort']), ['none', 'none', 'descending']);
assert.strictEqual(small.value, 'weight');
assert.strictEqual(sandbox.document.activeElement, focus[expected]);
phone = !phone;
probe.renderSortHeads();
assert(buttons.every(button => button.tabIndex === (phone ? -1 : 0)));
assert.strictEqual(sandbox.document.activeElement,
  expected === 'outside' ? outside : phone ? small : buttons[2]);
""",
  )


def test_balance_cards_use_localized_semantic_values() -> None:
  """Both balance renderers use localized labels and preserve numeric formats."""
  run_app_js(
    "overviewLimits, renderLimits, progressMeter",
    """
const specs = [
  ['credit', 2.67, 10, .267],
  ['usage', 1.01, null, null],
  ['free_requests', 2.5, 1000, .0025],
  ['balance', -0, null, null],
  ['pollen', 1234.56, null, null],
  ['neurons', 9000, 10000, .9],
  ['hourly_requests', '1987', '2000', .9935],
  ['hourly_requests', '9007199254740993', '9007199254740995', 1],
];
for (const [kind] of specs) LOCALE[`limits.balance.${kind}.label`] = `label-${kind}`;
LOCALE['limits.value.of_text'] = '{remaining} / {limit}';
LOCALE['limits.value.remaining_text'] = 'reste({remaining}; {limit})';
LOCALE['limits.value.money_text'] = 'monnaie({value})';
const decimal = value => value.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const expected = [
  `monnaie(${decimal(2.67)}) / monnaie(${decimal(10)})`,
  `monnaie(${decimal(1.01)})`,
  `reste(${(2).toLocaleString()}; 1K)`,
  `monnaie(${decimal(-0)})`,
  decimal(1234.56),
  'reste(9K; 10K)',
  `${(1987).toLocaleString()} / ${(2000).toLocaleString()}`,
  `${9007199254740993n.toLocaleString()} / ${9007199254740995n.toLocaleString()}`,
];
const items = specs.map(([kind, value, limit, share]) => ({kind, value, limit, share}));
const data = {checked: null, lanes: [], providers: [{name: 'provider<&>', items}]};
const overview = probe.overviewLimits(data);
probe.renderLimits(data);
for (const markup of [overview, node('balances').innerHTML]) {
  assert(markup.includes('provider&lt;&amp;&gt;'));
  for (let i = 0; i < items.length; i++) {
    assert(markup.includes(`label-${items[i].kind}`));
    assert(markup.includes(expected[i]), expected[i]);
  }
}
// The side cards read as a value alone; the overview snippet keeps its share bar.
for (let i = 0; i < items.length; i++) {
  if (items[i].share !== null) assert(overview.includes(probe.progressMeter(items[i].share)));
}
assert.strictEqual(node('balances').hidden, false);
LOCALE['limits.balance.credit.label'] = '<changed&>';
LOCALE['limits.value.money_text'] = '<{value}>';
for (const markup of [probe.overviewLimits(data), (probe.renderLimits(data), node('balances').innerHTML)]) {
  assert(markup.includes('&lt;changed&amp;&gt;'));
  assert(markup.includes(`&lt;${decimal(2.67)}&gt;`));
  assert(!markup.includes('label-credit'));
}
probe.renderLimits({checked: null, lanes: [], providers: []});
assert.strictEqual(node('balances').hidden, true);
probe.renderLimits({checked: null, lanes: [], providers: [
  {name: 'openrouter', items: []},
  {name: 'kilo', items: []},
  {name: 'pollinations', items: []},
  {name: 'cloudflare', items: []},
]});
for (const provider of ['openrouter', 'kilo', 'pollinations', 'cloudflare']) {
  assert(node('balances').innerHTML.includes(
    `<h3><img class="mark" src="ui/icons/${provider}.svg" alt="" aria-hidden="true"> ${provider}</h3>`
  ));
}
""",
  )


def test_time_format_defaults_to_24h_and_switches_to_12h_across_views() -> None:
  """Timestamps default to 24-hour clocks and switch when the operator selects 12h."""
  run_app_js(
    "state, stamp, clockTime, timeText, applyTimeFormat, renderKeys, openChain",
    r"""
const morning = new Date(2026, 9, 25, 0, 5, 9).getTime() / 1000;
const afternoon = new Date(2026, 9, 25, 14, 30, 46).getTime() / 1000;
assert.strictEqual(vm.runInContext("hourCycle", sandbox), "h23");
assert.strictEqual(sandbox.document.documentElement.dataset.timeFormat, "24h");
assert.strictEqual(probe.stamp(morning), "2026-10-25 00:05:09");
assert.strictEqual(probe.stamp(afternoon), "2026-10-25 14:30:46");

const timeMarkup = probe.timeText(afternoon, { tag: "time", variant: "muted" });
const relativeLabel = probe.timeText(afternoon).replace(/^<span[^>]*>|<\/span>$/g, "");
const afternoonIso = new Date(afternoon * 1000).toISOString();
assert.strictEqual(
  timeMarkup,
  '<time class="muted" datetime="' + afternoonIso + '" title="2026-10-25 14:30:46">' + relativeLabel + "</time>",
);

probe.state.keys = [{ name: "ops", start: "dae_1", created: afternoon, used: afternoon }];
probe.renderKeys(probe.state.keys);
assert(node("keys").innerHTML.includes("2026-10-25 14:30:46"));

probe.state.requests = [{
  at: afternoon,
  model: "daedalus/auto",
  via: "demo/m1",
  status: 200,
  fallbacks: 1,
  attempts: [
    { model: "demo/m0", result: "429", seconds: 0.2, error: "rate limit" },
    { model: "demo/m1", result: "answered", seconds: 0.4, error: "" },
  ],
}];
node("modal").showModal = function () { this.open = true; };
probe.openChain(String(afternoon));
assert(node("chain-modal-body").innerHTML.includes("2026-10-25 14:30:46"));

probe.applyTimeFormat("12h");
assert.strictEqual(vm.runInContext("hourCycle", sandbox), "h12");
assert.strictEqual(sandbox.document.documentElement.dataset.timeFormat, "12h");
assert.strictEqual(probe.stamp(morning), "2026-10-25 12:05:09 AM");
assert.strictEqual(probe.stamp(afternoon), "2026-10-25 02:30:46 PM");
assert(node("keys").innerHTML.includes("2026-10-25 02:30:46 PM"));
assert(node("chain-modal-body").innerHTML.includes("2026-10-25 02:30:46 PM"));
""",
  )


def test_clause4_theme_segmented_control_and_optimistic_writes() -> None:
  """Theme renders a 3-way radiogroup, and settings writes update optimistically with rollback."""
  run_app_js(
    "state, SETTINGS, renderSettings, settingsChanges, selectThemeChoice, openDiffModal, refreshRelativeTimes",
    r"""
const defaults = {};
for (const [group, , fields] of probe.SETTINGS) {
  defaults[group] = {};
  for (const [key, , unit] of fields) {
    const defChoice = key === "theme" ? "system" : "24h";
    const defScalar = unit === "name" ? key : unit ? 10 : true;
    defaults[group][key] = unit === "list" ? ["x"] : unit === "choice" ? defChoice : defScalar;
  }
}
defaults.hooks = { sources: [], disabled: [] };
probe.state.settings = {
  path: "config/daedalus.yml",
  values: JSON.parse(JSON.stringify(defaults)),
  defaults,
  file: { personalization: { theme: "system", time_format: "24h" } },
  text: "personalization:\n  theme: system\n",
  hook_rows: [],
};
probe.renderSettings();
const html = node("settings").innerHTML;
assert(html.includes('class="theme-seg" id="set-personalization-theme" role="radiogroup"'));
assert(!html.includes('id="set-personalization-theme" role="radiogroup" aria-label="Theme" data-value='),
  "theme-seg avoids data-value so bindValueRows never opens the text modal");
for (const choice of ["system", "light", "dark"]) {
  assert(html.includes(`data-theme-choice="${choice}"`), choice);
}

const radios = ["system", "light", "dark"].map((choice) => ({
  dataset: { themeChoice: choice },
  attrs: { "aria-checked": choice === "system" ? "true" : "false" },
  tabIndex: choice === "system" ? 0 : -1,
  setAttribute(k, v) { this.attrs[k] = v; },
  closest(sel) { return sel === ".theme-seg" ? seg : null; },
}));
const seg = node("set-personalization-theme");
seg.dataset = { themeValue: "system" };
seg.querySelectorAll = () => radios;
seg.closest = () => null;
const store = { getItem: () => null, removeItem: () => {}, setItem: () => {} };
sandbox.sessionStorage = store;
sandbox.localStorage = store;
let sawOptimisticDark = false;
sandbox.fetch = async (url, opts) => {
  if (opts?.method === "PUT") {
    sawOptimisticDark =
      sandbox.document.documentElement.dataset.theme === "dark"
      && seg.dataset.themeValue === "dark";
    return { ok: false, status: 500, json: async () => ({ error: { message: "boom" } }) };
  }
  return { ok: true, status: 200, json: async () => probe.state.settings };
};
for (const id of ["set-affinity-change_on_draw", "set-balance-weights", "set-balance-pacing",
  "set-optimization-enabled"]) node(id).checked = true;
node("set-affinity-mode").value = "session";
node("set-personalization-time_format").value = "24h";
(async () => {
  await probe.selectThemeChoice(radios[2]);
  assert.strictEqual(sawOptimisticDark, true, "optimistic state updates before PUT resolves");
  assert.strictEqual(sandbox.document.documentElement.dataset.theme, "light", "rollback restores prior theme");
  assert(node("settings").innerHTML.includes('data-theme-value="system"'), "rollback restores themeValue");
  const at = Date.now() / 1000 - 300;
  node("modal").open = true;
  node("modal").showModal = () => {};
  probe.openDiffModal({ reason: "startup", at, added: [], removed: [], changed: [] });
  assert(node("modal-title").textContent.includes("ago"), "diff modal title uses relative age");
  assert(node("modal-title").title.includes("-"), "diff modal title keeps exact stamp on hover");
  probe.refreshRelativeTimes();
})().catch((err) => { console.error(err); process.exit(1); });
""",
  )
