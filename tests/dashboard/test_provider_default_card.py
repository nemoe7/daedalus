"""Providers-page card and save flow of the `_default` global tier block."""

from pathlib import Path

import yaml
from conftest import run_app_js
from fastapi.testclient import TestClient

from daedalus import dashboard

TIER_ONLY = [
  "provider.api_key.label",
  "provider.api_base.label",
  "provider.discovery_url.label",
  "provider.client_keys.label",
  "provider.model_overrides.label",
  "provider.hooks.label",
  "provider.exclude.label",
]


def test_default_card_renders_tiers_only() -> None:
  """The `_default` card shows the tier editor and no provider fields."""
  run_app_js(
    "state, providerCard",
    """
probe.state.providerDefaults = {};
probe.state.env = [];
const html = probe.providerCard('_default', {tier: {'TIER-D': ['zz*']}});
assert(html.includes('tier-row'), 'the tier editor shows');
assert(html.includes('zz*'), 'the tier pattern shows');
for (const label of ['API key', 'API base', 'Discovery URL', 'Client keys',
  'Model overrides', 'Hooks', 'Exclude', 'Provider values']) {
  assert(!html.includes(label), `the card holds no ${label} field`);
}
assert(!html.includes('env:NAME reads'), 'no key note');
""",
  )


def test_default_card_names_its_scope() -> None:
  """The card states that `_default` claims only unclaimed models of its file."""
  run_app_js(
    "state, providerCard",
    """
probe.state.providerDefaults = {};
probe.state.env = [];
const html = probe.providerCard('_default', {tier: {}});
assert(html.includes('tier hint of this file'), html);
""",
  )


def test_save_writes_default_block(client: TestClient) -> None:
  """Save writes 1 `_default` block to 1 local main file."""
  path = str(dashboard.FILES[0])
  blocks = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
  blocks["_default"] = {"tier": {"TIER-D": ["zz*"]}}
  saved = client.put("/ui/api/providers", json={"path": path, "blocks": blocks})
  assert saved.status_code == 200, saved.text
  text = Path(path).read_text(encoding="utf-8")
  assert "_default:" in text and "zz*" in text, text
