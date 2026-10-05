"""A daedalus hook: OpenRouter tries the endpoints with the cheapest output price first.

Name this file 2 times in the hooks list of an OpenRouter model, provider or file:

  hooks:
    - on-catalog: hooks/or_cheapest_output.py
    - on-upstream: hooks/or_cheapest_output.py

At each catalog build, on_catalog reads the endpoint list of each model with the hook.
It sorts the list by output price after the discount, and the input price breaks a tie.
on_upstream then sends the order as provider.order. A client provider object has priority.
"""

import json
import logging
import math
from pathlib import Path
from typing import Any

import httpx

from daedalus import store

logger = logging.getLogger("daedalus.hooks")

TIMEOUT_SECONDS = 20


def orders_file() -> Path:
  """The saved orders, beside the model store, so that they stay after a restart."""
  return Path(store.MODELS_DB).parent / "cheapest_output.json"


def read_orders() -> dict[str, list[str]]:
  """The saved order of each model, or an empty dict."""
  try:
    found = json.loads(orders_file().read_text())
  except (OSError, ValueError):
    return {}
  return found if isinstance(found, dict) else {}


def save_order(model: str, order: list[str]) -> None:
  """Save the order of one model. The file changes in 1 step."""
  orders = {**read_orders(), model: order}
  target = orders_file()
  target.parent.mkdir(parents=True, exist_ok=True)
  temporary = target.with_suffix(".tmp")
  temporary.write_text(json.dumps(orders, indent=1))
  temporary.replace(target)


def price(value: Any) -> float:
  """One price as a number. A price that is not there sorts last."""
  try:
    number = float(value)
  except (TypeError, ValueError):
    return math.inf
  return number if number >= 0 else math.inf


def cheapest_first(rows: Any) -> list[str]:
  """The provider slugs, from the cheapest output price up. The input price breaks a tie.

  A slug has no variant, such as /fp4. A base slug matches each endpoint of the provider,
  and the provider keeps the place of its cheapest endpoint.
  """

  def cost(row: dict) -> tuple[float, float]:
    pricing = row.get("pricing") or {}
    return price(pricing.get("completion")), price(pricing.get("prompt"))

  endpoints = [row for row in rows or [] if isinstance(row, dict) and row.get("tag")]
  return list(
    dict.fromkeys(str(row["tag"]).split("/")[0] for row in sorted(endpoints, key=cost))
  )


def on_catalog(row: dict, model: str, api_base: str, headers: dict) -> None:
  """Read the endpoint list of the model, and save its order. A failed read keeps the old order."""
  url = f"{api_base}/models/{model.partition('/')[2]}/endpoints"
  try:
    response = httpx.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json().get("data")
  except (httpx.HTTPError, ValueError, AttributeError) as exc:
    logger.warning("kept the old endpoint order of %s: %s", model, exc)
    return
  order = cheapest_first(data.get("endpoints") if isinstance(data, dict) else None)
  if order:
    save_order(model, order)
    logger.info("the endpoint order of %s: %s", model, ", ".join(order))


def on_upstream(body: dict, model: str, headers: dict) -> None:
  """Send the saved order as provider.order, unless the client sent its own provider object."""
  order = read_orders().get(model)
  if order and "provider" not in body:
    body["provider"] = {"order": order}
