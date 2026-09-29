"""The hook files in `config/hooks`: the cheapest output order and the example."""

import json
import shutil
from pathlib import Path

import httpx
import pytest

from daedalus import store
from daedalus.providers import hooks

FOLDER = Path(__file__).resolve().parents[2] / "config" / "hooks"
ROWS = [
  {"tag": "cloud", "pricing": {"prompt": "0.0000003", "completion": "0.000001"}},
  {
    "tag": "cheap/fp4",
    "pricing": {"prompt": "0.000000045", "completion": "0.00000014"},
  },
  {"tag": "tie-b", "pricing": {"prompt": "0.00000015", "completion": "0.0000005"}},
  {"tag": "tie-a", "pricing": {"prompt": "0.000000025", "completion": "0.0000005"}},
  {"tag": "tie-a/us", "pricing": {"prompt": "0.0000002", "completion": "0.00000075"}},
  {"tag": "no-price", "pricing": {}},
  {"tag": "cheap/fp4", "pricing": {"prompt": "0", "completion": "0.000002"}},
  {"pricing": {"completion": "0"}},
  "not a row",
]
GLM = "openrouter/z-ai/glm-5.3-flash"


@pytest.fixture
def cheapest():
  module = hooks.load(FOLDER / "cheapest_output.py")
  assert module is not None
  module.orders_file().unlink(missing_ok=True)
  return module


def test_cheapest_first(cheapest) -> None:
  """Output price first, input price on a tie, no price last, each provider once, with no variant."""
  assert cheapest.cheapest_first(ROWS) == [
    "cheap",
    "tie-a",
    "tie-b",
    "cloud",
    "no-price",
  ]
  assert cheapest.cheapest_first(None) == []


def test_on_catalog(cheapest, monkeypatch: pytest.MonkeyPatch) -> None:
  """A model with the hook gets its order from the endpoint list. A failed read keeps the old order."""
  asked: list[tuple[str, str]] = []
  down = False

  def get(url: str, headers: dict, timeout: float) -> httpx.Response:
    asked.append((url, headers["Authorization"]))
    if down:
      raise httpx.ConnectError("down")
    return httpx.Response(
      200, json={"data": {"endpoints": ROWS[:2]}}, request=httpx.Request("GET", url)
    )

  monkeypatch.setattr(cheapest.httpx, "get", get)
  base, headers = "https://openrouter.ai/api/v1", {"Authorization": "Bearer k"}
  assert cheapest.on_catalog({}, GLM, base, headers) is None
  assert asked == [(f"{base}/models/z-ai/glm-5.3-flash/endpoints", "Bearer k")]
  assert cheapest.orders_file().parent == Path(store.MODELS_DB).parent
  saved = json.loads(cheapest.orders_file().read_text())
  assert saved == {GLM: ["cheap", "cloud"]}
  down = True
  cheapest.on_catalog({}, GLM, base, headers)
  assert cheapest.read_orders() == saved, "the old order stays"


def test_on_upstream(cheapest) -> None:
  """The saved order goes out as provider.order, and a client provider object has priority."""
  cheapest.save_order(GLM, ["cheap", "cloud"])
  body: dict = {"messages": []}
  cheapest.on_upstream(body, GLM, {})
  assert body["provider"] == {"order": ["cheap", "cloud"]}
  own = {"provider": {"sort": "latency"}}
  cheapest.on_upstream(own, GLM, {})
  assert own == {"provider": {"sort": "latency"}}
  other: dict = {}
  cheapest.on_upstream(other, "openrouter/no/order", {})
  assert other == {}, "no saved order, no provider object"


def test_example() -> None:
  """The example file loads, has each hook point, and changes nothing."""
  root = hooks.CONFIG_DIR / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  shutil.copy(FOLDER / "example.py", root / "example.py")
  module = hooks.load(root / "example.py")
  assert module is not None
  assert all(callable(getattr(module, name)) for name in hooks.POINTS.values())
  setup = {"p": {"hooks": [{point: "hooks/example.py"} for point in hooks.POINTS]}}
  body = {"messages": [{"role": "user", "content": "hi"}]}
  assert hooks.run("on-upstream", setup, "p/m", body, headers={}) == body
  assert hooks.run("on-answer", setup, "p/m", {"a": 1}) == {"a": 1}
  row = {"id": "p/m", "max_input_tokens": 1}
  assert (
    hooks.run("on-catalog", setup, "p/m", row, api_base="https://x", headers={}) == row
  )
