"""Build the model chains of the pools, praktos and `daedalus/auto`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from daedalus.catalog import any_match
from daedalus.classifier import TIER_NAMES, TIERS, Artifact, load_artifact, predict

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
# The pool for tool calls. It has no tier and holds TIER-A, then TIER-B members.
PRAKTOS: Final = "daedalus/praktos"
PRAKTOS_TIERS: Final = (4, 3)


def tier_models(provider: Mapping[str, Any], tier_name: str) -> list[str]:
  """The models one provider block lists under a tier name."""
  tiers = provider.get("tier") or {}
  names = tiers.get(tier_name) or []
  return [name for name in names if isinstance(name, str) and name]


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
      if line.startswith(head) and any_match(patterns, line[len(head) :]):
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


def route_praktos(config: Mapping[str, Any], lines: list[str]) -> list[str]:
  """The tool-capable models, TIER-A first, then TIER-B."""
  return chain_models(config, lines, PRAKTOS_TIERS)
