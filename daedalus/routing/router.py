"""Build the model chains of the pools and `daedalus/auto`."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Final

from daedalus import store
from daedalus.catalog.discovery import matches, specificity
from daedalus.config import block_for, client_key, file_block, main_block
from daedalus.routing import lanes
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
# The pools of the non-chat endpoints, and the catalog mode of their models.
MEDIA_POOLS: Final[Mapping[str, str]] = {
  "daedalus/graphos": "audio_transcription",
  "daedalus/photos": "image_generation",
}
# The pool names that clients use, from the `pools` settings. Only renamed pools are here.
RENAMED: Mapping[str, str] = {}
# The `headroom.enabled` setting. A block or a model entry alone turns the switch off for 1 model.
HEADROOM: bool = True


def set_headroom(enabled: bool) -> None:
  """Use the `headroom.enabled` setting of the settings file."""
  global HEADROOM
  HEADROOM = enabled is not False


def set_pool_names(names: Mapping[str, str]) -> None:
  """Use the settings pool names: each key is a built-in name after `daedalus/`, each value its client name."""
  global RENAMED
  RENAMED = {
    f"daedalus/{old}": f"daedalus/{new}" for old, new in names.items() if old != new
  }


def pool_name(name: str) -> str:
  """The client name of a built-in pool name. A name without `daedalus/`, such as koinos, gives a name without it."""
  if "/" in name:
    return RENAMED.get(name, name)
  return RENAMED.get(f"daedalus/{name}", name).removeprefix("daedalus/")


def built_in(name: str) -> str | None:
  """The built-in name of a model name from a client, or None for a pool name that the settings replaced."""
  for old, new in RENAMED.items():
    if name == new:
      return old
  return None if name in RENAMED else name


def listed(model: str) -> bool:
  """Tell if a request may answer: the store lists the id, or it names a built-in pool."""
  if model in POOLS or model == RESERVED_MODEL or model in MEDIA_POOLS:
    return True
  return store.catalog_name(model) == model


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


# The reasoning efforts each provider accepts, from its docs. A config key wins over the code.
DEFAULT_EFFORTS: Final[Mapping[str, tuple[str, ...]]] = {
  "mistral": ("none", "high"),
  "pollinations": ("none",),
  "z-ai": ("none",),
}
# The ladder of a provider with no coded list: the 4 levels of the heuristics read.
FALLBACK_EFFORTS: Final[tuple[str, ...]] = ("none", "low", "medium", "high")
# The cap of any ladder step, so a step never passes the 6th effort of a list.
EFFORT_CAP: Final = 5


def efforts(config: Mapping[str, Any], model: str) -> list[str]:
  """The ordered reasoning efforts of a model: its key, its block, then the coded default."""
  name, _, slug = model.partition("/")
  found = model_setting(config, model, "supported_reasoning_efforts")
  if found is None:
    found = (block_for(config, name, slug) or {}).get("supported_reasoning_efforts")
  if (
    not isinstance(found, list)
    or not found
    or not all(isinstance(value, str) for value in found)
  ):
    return list(DEFAULT_EFFORTS.get(name, FALLBACK_EFFORTS))
  return list(found)


def effort_at(efforts: list[str], step: int) -> str:
  """The effort of one ladder step: 0 the lowest, capped at 5 and at the list end."""
  return efforts[max(0, min(step, EFFORT_CAP, len(efforts) - 1))]


def headroom_allowed(config: Mapping[str, Any], model: str) -> bool:
  """Tell if the messages of a model take the Headroom compression.

  The narrow level wins: a `models` entry, the file block of the provider, its block, the setting.
  """
  name, _, _slug = model.partition("/")
  provider = config.get(name)
  found = model_setting(config, model, "headroom")
  if found is None:
    found = (file_block(provider) or {}).get("headroom")
  if found is None:
    found = (main_block(provider) or {}).get("headroom")
  if found is None:
    return HEADROOM
  return found is not False


def streams_allowed(config: Mapping[str, Any], model: str) -> bool:
  """Tell if a provider or a model may answer with a stream. A missing flag is true."""
  name, _, slug = model.partition("/")
  block = block_for(config, name, slug)
  if isinstance(block, Mapping) and block.get("streams") is False:
    return False
  return model_setting(config, model, "streams") is not False


def keyed(config: Mapping[str, Any], model: str) -> bool:
  """Tell if the block that owns the model has an API key."""
  name, _, slug = model.partition("/")
  block = block_for(config, name, slug)
  key = block.get("api_key") if block else None
  return isinstance(key, str) and bool(key)


def lane(config: Mapping[str, Any], model: str, client: str | None) -> str:
  """The cooldown and pacing key of a model: its own lane when the client has its own provider key."""
  name, _, slug = model.partition("/")
  if client and client_key(block_for(config, name, slug), client):
    return lanes.join(model, client)
  return model


def pooled(config: Mapping[str, Any], model: str) -> bool:
  """Tell if the model can go into the pools: it needs an API key, and `pool: false` allows only direct requests."""
  return keyed(config, model) and model_setting(config, model, "pool") is not False


def model_order(config: Mapping[str, Any], model: str) -> int:
  """The `order` of a model: its `models` entry, then its provider block, then 1."""
  value = model_setting(config, model, "order")
  if value is None:
    name, _, slug = model.partition("/")
    block = block_for(config, name, slug)
    value = block.get("order") if block else None
  if isinstance(value, bool) or not isinstance(value, int) or value < 1:
    return 1
  return value


# The order of each model for the config in `_ORDER_CONFIG`.
_ORDER_CACHE: dict[str, int] = {}
_ORDER_CONFIG: list[Mapping[str, Any] | None] = [None]


def cached_order(config: Mapping[str, Any], model: str) -> int:
  """The `order` of a model, from a cache that a new config clears."""
  if _ORDER_CONFIG[0] is not config:
    _ORDER_CACHE.clear()
    _ORDER_CONFIG[0] = config
  if model not in _ORDER_CACHE:
    _ORDER_CACHE[model] = model_order(config, model)
  return _ORDER_CACHE[model]


def by_order(config: Mapping[str, Any], groups: list[list[str]]) -> list[list[str]]:
  """Each group split by the model order, the lowest order first."""
  split: list[list[str]] = []
  for group in groups:
    orders = [cached_order(config, m) for m in group]
    for level in sorted(set(orders)):
      split.append(
        [m for m, found in zip(group, orders, strict=True) if found == level]
      )
  return split


def model_wait(config: Mapping[str, Any], model: str, default: float) -> float:
  """The seconds with no data from the provider: the `timeout` of the model, or the default."""
  value = model_setting(config, model, "timeout")
  if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
    return default
  return float(value)


# The tier rows of the last config, for each model list. A new config object clears it.
_TIER_CACHE: dict[tuple[str, ...], dict[str, list[str]]] = {}
_CACHED_CONFIG: list[Mapping[str, Any] | None] = [None]
# The model lists that the cache keeps, for example with and without the tool filter.
CACHE_SIZE: Final = 8


def sorted_lines(config: Mapping[str, Any], lines: list[str]) -> dict[str, list[str]]:
  """The pooled provider/slug rows of each tier name, in provider order, then line order."""
  found: dict[str, list[str]] = {}
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
      tier = None if block is None else claiming_tier(block, slug)
      if tier is not None and pooled(config, line):
        found.setdefault(tier, []).append(line)
  return found


def tier_lines(config: Mapping[str, Any], lines: list[str]) -> dict[str, list[str]]:
  """The pooled rows of each tier name, from the cache when the config and lines are the same."""
  if _CACHED_CONFIG[0] is not config:
    _TIER_CACHE.clear()
    _CACHED_CONFIG[0] = config
  key = tuple(lines)
  found = _TIER_CACHE.get(key)
  if found is None:
    if len(_TIER_CACHE) >= CACHE_SIZE:
      _TIER_CACHE.clear()
    found = _TIER_CACHE[key] = sorted_lines(config, lines)
  return found


def candidates(
  config: Mapping[str, Any],
  tier_name: str,
  lines: list[str],
) -> list[str]:
  """Return the provider/slug rows that one tier claims."""
  return list(tier_lines(config, lines).get(tier_name, ()))


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


# The tier table, loaded on the first request. The file does not change at run time.
_ARTIFACT: list[Artifact] = []
# The tiers of the last prompts, by digest. The requests of 1 tool loop repeat the prompt.
_TIER_OF: OrderedDict[bytes, int] = OrderedDict()
PROMPTS_KEPT: Final = 64
# The bar of the `routing.threshold` setting. None keeps the value of the artifact.
THRESHOLD: float | None = None


def set_threshold(value: float | None) -> None:
  """Use the bar of `routing.threshold` for the next reads. None keeps the artifact value."""
  global THRESHOLD
  if THRESHOLD != value:
    # The cached tiers were read against the old bar.
    _TIER_OF.clear()
  THRESHOLD = value


def required_tier(prompt: str, artifact: Artifact | None = None) -> int:
  """The cheapest tier that will do for one prompt."""
  if artifact is not None:
    return predict(prompt, artifact, threshold=THRESHOLD).required_tier
  digest = hashlib.blake2b(prompt.encode("utf-8", "surrogatepass"), digest_size=16)
  key = digest.digest()
  if key in _TIER_OF:
    _TIER_OF.move_to_end(key)
    return _TIER_OF[key]
  if not _ARTIFACT:
    _ARTIFACT.append(load_artifact())
  tier = _TIER_OF[key] = predict(
    prompt, _ARTIFACT[0], threshold=THRESHOLD
  ).required_tier
  if len(_TIER_OF) > PROMPTS_KEPT:
    _TIER_OF.popitem(last=False)
  return tier
