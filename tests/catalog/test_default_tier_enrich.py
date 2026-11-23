"""Enrichment stamps `_default` tiers into store rows."""

from daedalus.catalog import enrichment
from tests.catalog.test_enrichment import PAGE, sequenced


def test_enrich_lets_default_claim_beat_catch_all() -> None:
  """A model claimed only by the provider catch-all takes its `_default` tier."""
  fetch, _ = sequenced(PAGE)
  config = {
    "_default": {"tier": {"TIER-D": ["zz*"]}},
    "openrouter": {"tier": {"TIER-B": ["*"]}},
  }
  rows, _ = enrichment.enrich(["openrouter/zzz"], config, fetch)
  assert rows[0]["tier"] == "TIER-D", rows[0]


def test_enrich_keeps_specific_provider_tier_over_default() -> None:
  """A specific provider pattern beats `_default` at enrichment time."""
  fetch, _ = sequenced(PAGE)
  config = {
    "_default": {"tier": {"TIER-D": ["a"]}},
    "openrouter": {"tier": {"TIER-B": ["a"]}},
  }
  rows, _ = enrichment.enrich(["openrouter/a"], config, fetch)
  assert rows[0]["tier"] == "TIER-B", rows[0]
