"""Build the model chains of the pools and `daedalus/auto`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from daedalus.catalog.discovery import matches, specificity
from daedalus.config import block_for
from daedalus.routing.classifier import (
  TIER_NAMES,
  TIERS,
  Artifact,
  load_artifact,
  predict,
)

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
# The pools of the endpoints that do not chat, and the catalog mode of their models.
MEDIA_POOLS: Final[Mapping[str, str]] = {
  "daedalus/graphos": "audio_transcription",
  "daedalus/photos": "image_generation",
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


def model_setting(config: Mapping[str, Any], model: str, key: str) -> Any:
  """The value of a key in the last `models` entry that matches the model and has it."""
  name, _, slug = model.partition("/")
  block = block_for(config, name, slug)
  found = None
  for pattern, values in (block.get("models") or {}).items() if block else ():
    if isinstance(values, dict) and key in values and matches(str(pattern), slug):
      found = values[key]
  return found


def pooled(config: Mapping[str, Any], model: str) -> bool:
  """Tell if the model can go into the pools: `pool: false` allows only direct requests."""
  return model_setting(config, model, "pool") is not False


def model_wait(config: Mapping[str, Any], model: str, default: float) -> float:
  """The seconds with no bytes from the provider: the `timeout` of the model, or the default."""
  value = model_setting(config, model, "timeout")
  if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
    return default
  return float(value)


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
    head = f"{provider_name}/"
    for line in lines:
      if not line.startswith(head):
        continue
      slug = line[len(head) :]
      # The block that owns the model also sets its tier.
      block = block_for(config, provider_name, slug)
      if block is None or claiming_tier(block, slug) != tier_name:
        continue
      if pooled(config, line):
        wanted.append(line)
  return wanted


def fallback_order(tier: int) -> tuple[int, ...]:
  """List the tiers to try, this one, up to tier 4, then down to tier 1."""
  above: Final = tuple(range(tier, TIERS[-1] + 1))
  below: Final = tuple(range(tier - 1, 0, -1))
  return above + below


def chain_groups(
  config: Mapping[str, Any],
  lines: list[str],
  order: tuple[int, ...],
) -> list[list[str]]:
  """The `provider/slug` rows of each tier in a chain, one list for each tier."""
  return [candidates(config, TIER_NAMES[tier], lines) for tier in order]


def required_tier(prompt: str, artifact: Artifact | None = None) -> int:
  """The cheapest tier that will do for one prompt."""
  table: Final = load_artifact() if artifact is None else artifact
  return predict(prompt, table).required_tier
