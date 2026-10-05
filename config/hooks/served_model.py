"""The served model: the model that served a pool answer, in the final chunk's `usage.daedalus`."""

import logging
from typing import Any

from daedalus.routing import router

logger = logging.getLogger("daedalus.hooks")

# The reserved model and the separator of the line: `A · kilo/poolside/laguna-s-2.1:free`.
RESERVED = router.RESERVED_MODEL
DOT = " · "


def tier_letter(pool: str) -> str:
  """The letter of a chat pool, `A` for sophos down to `D` for moros, and empty for no pool."""
  name = router.built_in(pool) or pool
  if not name.startswith("daedalus/"):
    name = f"daedalus/{name}"
  tier = router.POOLS.get(name)
  return router.TIER_NAMES[tier].removeprefix("TIER-") if tier else ""


def line_for(model: str, pool: str, served: str) -> str:
  """`{letter} · {slug}` for `daedalus/auto`, the slug alone for a named pool."""
  if model != RESERVED:
    return served
  letter = tier_letter(pool)
  return f"{letter}{DOT}{served}" if letter else served


def on_chunk(
  chunk: dict[str, Any], model: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
  """Write the served model line into the final chunk, when the session model changed or a retry ran.

  :param chunk: one OpenAI chunk on its way to the client
  :param model: the requested model, `daedalus/auto` or a pool
  :param context: `previous`, the model of the last session answer, empty on the first; `attempts`, the failures so far; `code`, the retry code; `pool`, the landed pool; `served`, the landed model
  """
  if model != RESERVED and model not in router.POOLS:
    return chunk
  found = context or {}
  choices = chunk.get("choices") or []
  if not any(
    isinstance(choice, dict) and choice.get("finish_reason") for choice in choices
  ):
    return chunk
  served = str(found.get("served") or chunk.get("model") or "")
  if not served:
    return chunk
  previous = str(found.get("previous") or "")
  if previous == served and not found.get("attempts") and not found.get("code"):
    return chunk
  pool = str(found.get("pool") or "")
  pick = {"line": line_for(model, pool, served), "model": served, "pool": pool}
  usage = chunk.get("usage")
  chunk["usage"] = (
    {**usage, "daedalus": pick} if isinstance(usage, dict) else {"daedalus": pick}
  )
  logger.info("served model %s for %s", pick["line"], model)
  return chunk
