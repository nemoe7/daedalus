# ---
# version: 1.0.7
# surfaces: [on-chunk]
# scope: client
# targets: [OWUI]
# author: nemoe7
# title: Served model
# description: The model that served a pool answer, in the final chunk's usage.daedalus.
# license: nemoe7 Noncommercial and Public-Interest License 1.2.0
# ---

import logging
from typing import Any

from daedalus import store
from daedalus.routing import router

logger = logging.getLogger("daedalus.hooks")

RESERVED = router.RESERVED_MODEL


def line_for(served: str) -> str:
  """Return the catalog display name of `served`, or its slug without the provider prefix."""
  names = store.model_names()
  found = names.get(served) or names.get(store.catalog_name(served) or "")
  if found:
    return found
  _, slash, slug = served.partition("/")
  return slug if slash and slug else served


def on_chunk(
  chunk: dict[str, Any], model: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
  """Write the served model line into the final chunk of a pool request.

  :param chunk: one OpenAI chunk on its way to the client
  :param model: the requested model, `daedalus/auto` or a pool
  :param context: `pool` names the pool. `served` names the model.
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
  pool = str(found.get("pool") or "")
  selection = {"line": line_for(served), "model": served, "pool": pool}
  usage = chunk.get("usage")
  chunk["usage"] = (
    {**usage, "daedalus": selection}
    if isinstance(usage, dict)
    else {"daedalus": selection}
  )
  logger.info("served model %s for %s", selection["line"], model)
  return chunk
