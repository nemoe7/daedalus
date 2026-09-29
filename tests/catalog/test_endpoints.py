"""The endpoint order of `cheapest_output`: the cheapest output price first, from each catalog build."""

import httpx
import pytest

from daedalus import store
from daedalus.catalog import endpoints

ROWS = [
  {"tag": "cloud", "pricing": {"prompt": "0.0000003", "completion": "0.000001"}},
  {
    "tag": "cheap/fp4",
    "pricing": {"prompt": "0.000000045", "completion": "0.00000014"},
  },
  {"tag": "tie-b", "pricing": {"prompt": "0.00000015", "completion": "0.0000005"}},
  {"tag": "tie-a", "pricing": {"prompt": "0.000000025", "completion": "0.0000005"}},
  {"tag": "no-price", "pricing": {}},
  {"tag": "cheap/fp4", "pricing": {"prompt": "0", "completion": "0.000002"}},
  {"pricing": {"completion": "0"}},
  "not a row",
]


def test_cheapest_first() -> None:
  """Output price first, input price on a tie, no price last, each tag once."""
  assert endpoints.cheapest_first(ROWS) == [
    "cheap/fp4",
    "tie-a",
    "tie-b",
    "cloud",
    "no-price",
  ]


def config() -> dict:
  return {
    "openrouter": {
      "api_key": "k",
      "models": {
        "z-ai/*": {"cheapest_output": True},
        "z-ai/glm-old": {"cheapest_output": False},
        "other/model": {"cheapest_output": "yes"},
      },
    },
    "groq": {"api_key": "k", "models": {"m": {"cheapest_output": True}}},
  }


def test_endpoint_orders(monkeypatch: pytest.MonkeyPatch) -> None:
  """Only models with `cheapest_output: true` on a provider with an endpoint list get an order."""
  asked: list[tuple[str, str]] = []

  def fetch(url: str, headers: dict[str, str]) -> dict:
    asked.append((url, headers["Authorization"]))
    if "broken" in url:
      raise httpx.ConnectError("down")
    return {"data": {"endpoints": ROWS[:2]}}

  monkeypatch.setattr(endpoints, "fetch_json", fetch)
  models = [
    "openrouter/z-ai/glm-5.3-flash",
    "openrouter/z-ai/glm-old",
    "openrouter/z-ai/broken",
    "openrouter/other/model",
    "groq/m",
  ]
  orders, missed = endpoints.endpoint_orders(config(), models)
  assert orders == {"openrouter/z-ai/glm-5.3-flash": ["cheap/fp4", "cloud"]}
  assert missed == ["openrouter/z-ai/broken"], "a failed fetch keeps the old order"
  assert asked[0] == (
    "https://openrouter.ai/api/v1/models/z-ai/glm-5.3-flash/endpoints",
    "Bearer k",
  )
  assert len(asked) == 2, (
    "no fetch for false, a non-bool value or a provider without a list"
  )


def test_store_orders() -> None:
  """A new build replaces the orders, and a model in `keep` keeps its old order."""
  store.write_orders({"a/x": ["one", "two"], "a/y": ["three"], "a/z": ["four"]})
  assert store.provider_order("a/x") == ["one", "two"]
  store.write_orders({"a/x": ["two"]}, keep=["a/y"])
  assert store.provider_order("a/x") == ["two"]
  assert store.provider_order("a/y") == ["three"], "kept after a failed fetch"
  assert store.provider_order("a/z") == [], "gone when the key is gone"
  assert store.provider_order("a/none") == []
