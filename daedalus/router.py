"""Build the model chains of the pools and `daedalus/auto`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from daedalus.classifier import TIER_NAMES, TIERS, Artifact, load_artifact, predict
from daedalus.discovery import matches, specificity

__all__ = ["TIERS", "TIER_NAMES"]

# The one model name the proxy resolves itself. Every other name goes upstream as written.
RESERVED_MODEL: Final = "daedalus/auto"
# Four pools, each a gateway to one tier.
POOLS: Final[Mapping[str, int]] = {
  "daedalus/moros": 1,
  "daedalus/koinos": 2,
  "daedalus/deinos": 3,
  "daedalus/sophos": 4,
}


def tier_models(provider: Mapping[str, Any], tier_name: str) -> list[str]:
  """The models one provider block lists under a tier name."""
  tiers = provider.get("tier") or {}
  names = tiers.get(tier_name) or []
  return [name for name in names if isinstance(name, str) and name]


def claiming_tier(provider: Mapping[str, Any], slug: str) -> str | None:
  """The tier with the most specific pattern for a slug. A tie goes to the higher tier."""
  best: tuple[tuple[int, int], str] | None = None
  for tier in reversed(TIERS):
    name = TIER_NAMES[tier]
    ranks = [specificity(p) for p in tier_models(provider, name) if matches(p, slug)]
    if ranks and (best is None or max(ranks) > best[0]):
      best = (max(ranks), name)
  return None if best is None else best[1]


def candidates(
  config: Mapping[str, Any],
  tier_name: str,
  lines: list[str],
) -> list[str]:
  """Return the provider/slug rows that one tier claims."""
  wanted: list[str] = []
  for provider_name, provider in config.items():
    if not isinstance(provider, dict):
      continue
    patterns = tier_models(provider, tier_name)
    if not patterns:
      continue
    head = f"{provider_name}/"
    for line in lines:
      if (
        line.startswith(head)
        and claiming_tier(provider, line[len(head) :]) == tier_name
      ):
        wanted.append(line)
  return wanted


def fallback_order(tier: int) -> tuple[int, ...]:
  """List the tiers to try, this one, up to tier 4, then down to tier 1."""
  above: Final = tuple(range(tier, TIERS[-1] + 1))
  below: Final = tuple(range(tier - 1, 0, -1))
  return above + below


def chain_models(
  config: Mapping[str, Any],
  lines: list[str],
  order: tuple[int, ...],
) -> list[str]:
  """Every `provider/slug` in a tier chain, in the order the proxy tries them."""
  return [line for group in chain_groups(config, lines, order) for line in group]


def chain_groups(
  config: Mapping[str, Any],
  lines: list[str],
  order: tuple[int, ...],
) -> list[list[str]]:
  """The `provider/slug` rows of each tier in a chain, one list for each tier."""
  return [candidates(config, TIER_NAMES[tier], lines) for tier in order]


def route(
  prompt: str,
  config: Mapping[str, Any],
  lines: list[str],
  artifact: Artifact | None = None,
) -> list[str]:
  """The models for a prompt, in the order the proxy tries them."""
  return chain_models(config, lines, fallback_order(required_tier(prompt, artifact)))


def required_tier(prompt: str, artifact: Artifact | None = None) -> int:
  """The cheapest tier that will do for one prompt."""
  table: Final = load_artifact() if artifact is None else artifact
  return predict(prompt, table).required_tier


def route_pool(
  pool: str,
  config: Mapping[str, Any],
  lines: list[str],
) -> list[str]:
  """The models for a named pool, with no classifier call."""
  tier = POOLS.get(pool)
  if tier is None:
    return []
  return chain_models(config, lines, fallback_order(tier))
