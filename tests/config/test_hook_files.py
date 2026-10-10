"""The hook files in `hooks`: the cheapest output order and the example."""

import json
import shutil
from pathlib import Path

import httpx
import pytest

from daedalus import store
from daedalus.providers import hooks

FOLDER = Path(__file__).resolve().parents[2] / "hooks"
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
  module = hooks.load(FOLDER / "or_cheapest_output.py")
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
  """The example file loads, has each hook surface, and changes nothing."""
  root = hooks.ROOT / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  shutil.copy(FOLDER / "example.py", root / "example.py")
  module = hooks.load(root / "example.py")
  assert module is not None
  assert all(callable(getattr(module, name)) for name in hooks.SURFACES.values())
  setup = {
    "p": {"hooks": [{surface: "hooks/example.py"} for surface in hooks.SURFACES]}
  }
  body = {"messages": [{"role": "user", "content": "hi"}]}
  assert hooks.run("on-upstream", setup, "p/m", body, headers={}) == body
  assert hooks.run("on-answer", setup, "p/m", {"a": 1}) == {"a": 1}
  row = {"id": "p/m", "max_input_tokens": 1}
  assert (
    hooks.run("on-catalog", setup, "p/m", row, api_base="https://x", headers={}) == row
  )


def test_pick_line() -> None:
  """The served model line: the tier letter and the slug for `daedalus/auto`, the slug alone for a pool."""
  module = hooks.load(FOLDER / "served_model.py")
  assert module is not None
  context = {"pool": "sophos", "served": "kilo/poolside/laguna-s-2.1:free"}
  auto = module.on_chunk(
    {"choices": [{"finish_reason": "stop"}]}, "daedalus/auto", context
  )
  assert auto["usage"]["daedalus"] == {
    "line": "A \u00b7 kilo/poolside/laguna-s-2.1:free",
    "model": "kilo/poolside/laguna-s-2.1:free",
    "pool": "sophos",
  }
  pool = module.on_chunk(
    {"choices": [{"finish_reason": "stop"}]}, "daedalus/koinos", context
  )
  assert pool["usage"]["daedalus"]["line"] == "kilo/poolside/laguna-s-2.1:free"


def test_pick_on_every_answer() -> None:
  """A repeated model draws too, and the first answer and a retry draw."""
  module = hooks.load(FOLDER / "served_model.py")
  assert module is not None
  stop = {"choices": [{"finish_reason": "stop"}]}
  repeat = module.on_chunk(
    dict(stop),
    "daedalus/auto",
    {"previous": "kilo/x", "served": "kilo/x", "pool": "koinos"},
  )
  assert repeat["usage"]["daedalus"]["line"] == "C \u00b7 kilo/x"
  assert "usage" not in module.on_chunk(
    {"choices": [{"delta": {}}]}, "daedalus/auto", {"previous": "", "served": "kilo/x"}
  )
  first = module.on_chunk(
    dict(stop), "daedalus/auto", {"previous": "", "served": "kilo/x", "pool": "koinos"}
  )
  assert first["usage"]["daedalus"]["line"] == "C \u00b7 kilo/x"
  retry = module.on_chunk(
    dict(stop),
    "daedalus/auto",
    {"previous": "kilo/x", "served": "kilo/x", "code": "rt1", "pool": "deinos"},
  )
  assert retry["usage"]["daedalus"]["line"] == "B \u00b7 kilo/x"


def test_a_request_point_runs_each_named_file() -> None:
  """The shipped file names no hook, and a request surface runs each file it names."""
  text = (FOLDER.parent / "config" / "daedalus.yml").read_text()
  assert "on-request:" not in text and "disabled:" not in text, (
    "the shipped file names no hook, and its initial source stays"
  )
  entries = {
    "on-request": "hooks/owui_auto_reasoning_effort.py",
    "on-chunk": "hooks/served_model.py",
  }
  target = hooks.ROOT / "hooks" / "served_model.py"
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text("", encoding="utf-8")
  found = hooks.request_files(entries, "on-chunk")
  assert [path.name for path in found] == ["served_model.py"]
  assert hooks.request_files(entries, "on-answer") == []
  assert hooks.request_files({"on-chunk": ""}, "on-chunk") == []


def test_request_paths_add_the_installed_files_of_the_surface(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A request point also runs an installed file whose `surfaces` block names it, with no key."""
  monkeypatch.setattr(hooks, "ROOT", tmp_path)
  folder = tmp_path / "hooks"
  folder.mkdir()
  (folder / "probe.py").write_text(
    "# ---\n# surfaces: [on-prompt]\n# ---\n\ndef on_prompt(value, **context):\n  pass\n",
    encoding="utf-8",
  )
  (folder / "other.py").write_text(
    "# ---\n# surfaces: [on-chunk]\n# ---\n", encoding="utf-8"
  )
  hooks.set_installed("hooks", [])
  found = hooks.request_paths({}, {}, "on-prompt", "daedalus/auto")
  assert [path.name for path in found] == ["probe.py"]
  named = hooks.request_paths(
    {"on-prompt": "hooks/other.py"}, {}, "on-prompt", "daedalus/auto"
  )
  assert [path.name for path in named] == ["other.py", "probe.py"]
