"""The upstream endpoints of each model with `cheapest_output`, from the cheapest output price up."""

import logging
import math
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from daedalus.catalog.discovery import fetch_json, matches
from daedalus.config import block_for
from daedalus.providers import ProviderError, provider_for

logger = logging.getLogger("daedalus.catalog")

KEY = "cheapest_output"


def price(value: Any) -> float:
  """One price as a number. A price that is not there sorts last."""
  try:
    number = float(value)
  except (TypeError, ValueError):
    return math.inf
  return number if number >= 0 else math.inf


def cheapest_first(rows: Iterable[Any]) -> list[str]:
  """The endpoint tags, from the cheapest output price up. The input price breaks a tie."""

  def cost(row: dict) -> tuple[float, float]:
    pricing = row.get("pricing") or {}
    return price(pricing.get("completion")), price(pricing.get("prompt"))

  ranked = sorted(
    (row for row in rows if isinstance(row, dict) and row.get("tag")), key=cost
  )
  return list(dict.fromkeys(str(row["tag"]) for row in ranked))


def wanted(config: Mapping[str, Any], model: str) -> bool:
  """Tell if the last `models` entry of the model that has `cheapest_output` sets it to true."""
  name, _, slug = model.partition("/")
  block = block_for(config, name, slug)
  found = None
  for pattern, values in (block.get("models") or {}).items() if block else ():
    if isinstance(values, dict) and KEY in values and matches(str(pattern), slug):
      found = values[KEY]
  return found is True


def endpoint_orders(
  config: Mapping[str, Any], models: Iterable[str]
) -> tuple[dict[str, list[str]], list[str]]:
  """The endpoint order of each model with `cheapest_output`, and the models whose list fetch failed."""
  orders: dict[str, list[str]] = {}
  missed: list[str] = []
  for model in models:
    if not wanted(config, model):
      continue
    try:
      provider, slug = provider_for(model, config)
    except ProviderError as exc:
      logger.warning("no endpoint order for %s: %s", model, exc)
      continue
    url = provider.endpoints_url(slug)
    if url is None:
      logger.warning(
        "no endpoint order for %s: the provider has no endpoint list", model
      )
      continue
    try:
      payload = fetch_json(url, provider.headers())
    except (httpx.HTTPError, ValueError) as exc:
      logger.warning("kept the old endpoint order of %s: %s", model, exc)
      missed.append(model)
      continue
    data = payload.get("data")
    found = cheapest_first(
      data.get("endpoints") or [] if isinstance(data, dict) else []
    )
    if found:
      orders[model] = found
  return orders, missed
