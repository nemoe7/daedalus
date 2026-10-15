"""Tests for the `on-prompt` hook of the reasoning effort."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import dashboard, store
from daedalus.providers import hooks
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
CONFIG = {"a": {"api_key": "k", "api_base": "https://a.test/v1"}}
BODY = {
  "model": "daedalus/auto",
  "messages": [{"role": "user", "content": "why is this slow"}],
}


class Provider:
  """A fake provider that keeps each body it receives."""

  def __init__(self) -> None:
    self.bodies: list[dict] = []

  def __call__(self, request: httpx.Request) -> httpx.Response:
    self.bodies.append(json.loads(request.content))
    message = {"role": "assistant", "content": "ok"}
    choice = {"index": 0, "message": message, "finish_reason": "stop"}
    return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


@pytest.fixture
def provider() -> Provider:
  found = Provider()
  kept = upstream.get_client()
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(found)))
  yield found
  upstream.set_client(kept)


def post(
  provider: Provider,
  slot: str | None = "daedalus/auto:TIER-A",
  body: dict | None = None,
  app: str | None = None,
) -> dict:
  """One chat request through a fake chain, and the body the provider received."""
  original = api.get_config, api.chain
  api.get_config = lambda: CONFIG
  api.chain = lambda model, body, config, key="": ([["a/1"]], slot)
  marks = {"OWUI": {"x-openwebui-chat-id": "c1"}, "Kilo": {"x-title": "kilo"}}
  client = TestClient(api.app, headers={**AUTH, **(marks.get(app or "", {}))})
  asked = body or {**BODY, "model": slot if slot in api.router.POOLS else BODY["model"]}
  try:
    response = client.post("/v1/chat/completions", json=asked)
    assert response.status_code == 200, response.text
  finally:
    api.get_config, api.chain = original
  return provider.bodies[-1]


@pytest.fixture
def hook_file() -> str:
  """A hook file of the `on-prompt` surface: it records its values and sets a value."""
  path = hooks.ROOT / "hooks" / "prompt_probe.py"
  path.parent.mkdir(parents=True, exist_ok=True)
  dump = path.with_suffix(".json")
  path.write_text(
    "import json\n"
    "from pathlib import Path\n"
    "\n"
    "def on_prompt(value, **context):\n"
    f"  Path({str(dump)!r}).write_text(json.dumps({{'keys': sorted(context), "
    "'reasoning': context['reasoning'], "
    "'effort': context['effort'], 'tier_name': context['tier_name'], "
    "'slot': context['slot']}))\n"
    "  value['reasoning_effort'] = 'max'\n",
    encoding="utf-8",
  )
  try:
    yield "hooks/prompt_probe.py"
  finally:
    path.unlink(missing_ok=True)
    dump.unlink(missing_ok=True)
    hooks._loaded.pop(path.resolve(), None)


def test_no_hook_file_sets_no_effort(provider: Provider) -> None:
  """With no hook file, the base sets no effort of its own."""
  assert "reasoning_effort" not in post(provider, "daedalus/auto:TIER-A")
  assert "reasoning_effort" not in post(provider, "daedalus/auto:TIER-D")
  assert "reasoning_effort" not in post(provider, "daedalus/koinos")


def test_client_wins(provider: Provider) -> None:
  """A `reasoning_effort` of the client goes upstream unchanged."""
  body = {**BODY, "reasoning_effort": "minimal"}
  assert post(provider, "daedalus/auto:TIER-A", body)["reasoning_effort"] == "minimal"


def test_catalog_wins(provider: Provider, monkeypatch: pytest.MonkeyPatch) -> None:
  """A stored effort of the model goes upstream."""
  monkeypatch.setattr(
    upstream.store, "model_limits", lambda candidate: {"reasoning_effort": "medium"}
  )
  assert post(provider, "daedalus/auto:TIER-A")["reasoning_effort"] == "medium"


def test_hook_file_wins(
  provider: Provider, hook_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The prompt hook receives the expected context and sets the outgoing effort."""
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-prompt": [hook_file]})
  written = post(provider, "daedalus/auto:TIER-B")
  assert written["reasoning_effort"] == "max"
  dump = json.loads((hooks.ROOT / "hooks" / "prompt_probe.json").read_text())
  assert set(dump["keys"]) == {
    "messages",
    "prompt",
    "model",
    "tier",
    "tier_name",
    "slot",
    "reasoning",
    "effort",
    "retry",
    "level",
    "body",
    "request_values",
    "config",
    "key",
    "app",
  }, dump["keys"]
  assert dump["tier_name"] == "TIER-B" and dump["slot"] == "daedalus/auto:TIER-B"
  assert dump["reasoning"] == ["a/1"], dump["reasoning"]
  assert dump["effort"] is None, dump["effort"]


def test_the_row_carries_the_hook_level(
  provider: Provider, hook_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
  """History records the effort set by the prompt hook."""
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-prompt": [hook_file]})
  post(provider, "daedalus/auto:TIER-B")
  row = dashboard.HISTORY.latest(1)[0]
  assert row["effort"] == "max", row


def test_no_reasoning_model(
  provider: Provider, hook_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A chain with no reasoning model gets no call and no effort."""
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-prompt": [hook_file]})
  monkeypatch.setattr(upstream.store, "reasoning_flags", lambda: {"a/1": False})
  written = post(provider, "daedalus/auto:TIER-A")
  assert "reasoning_effort" not in written, written
  assert not (hooks.ROOT / "hooks" / "prompt_probe.json").exists(), (
    "the hook did not run"
  )


def ladder_file(monkeypatch: pytest.MonkeyPatch) -> None:
  """Write the shipped hook into the hook folder, and name it in the prompt surface."""
  shipped = (
    Path(__file__).resolve().parents[2] / "hooks" / "owui_auto_reasoning_effort.py"
  )
  target = hooks.ROOT / "hooks" / "owui_auto_reasoning_effort.py"
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
  hooks._loaded.pop(target.resolve(), None)
  monkeypatch.setattr(
    api, "REQUEST_HOOKS", {"on-prompt": ["hooks/owui_auto_reasoning_effort.py"]}
  )


def test_the_shipped_ladder_file_sets_the_effort(
  provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The shipped `owui_auto_reasoning_effort.py` sets the level from the heuristics read of the prompt."""
  ladder_file(monkeypatch)
  monkeypatch.setattr(
    upstream.store, "model_limits", lambda candidate: {"reasoning_effort": "minimal"}
  )
  written = post(provider, "daedalus/auto:TIER-B", app="OWUI")
  tier = int(api.router.required_tier(BODY["messages"][0]["content"]))
  assert written["reasoning_effort"] == shipped_levels()[tier]
  assert written["reasoning_effort"] != "minimal", (
    "the hook answers above the catalog default"
  )


def test_the_shipped_ladder_file_skips_another_client(
  provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The base runs the shipped ladder file for its client alone, so another client keeps its effort."""
  ladder_file(monkeypatch)
  # No named list: the file joins the surface by its own block, and its client scope gates it.
  monkeypatch.setattr(api, "REQUEST_HOOKS", {})
  monkeypatch.setattr(
    upstream.store, "model_limits", lambda candidate: {"reasoning_effort": "minimal"}
  )
  assert (
    post(provider, "daedalus/auto:TIER-B", app="Kilo")["reasoning_effort"] == "minimal"
  )
  assert post(provider, "daedalus/auto:TIER-B")["reasoning_effort"] == "minimal"


def shipped_levels() -> dict[int, str]:
  """The level table of the shipped ladder file, read from the file itself."""
  path = Path(__file__).resolve().parents[2] / "hooks" / "owui_auto_reasoning_effort.py"
  spec = importlib.util.spec_from_file_location("shipped_ladder", path)
  assert spec and spec.loader, path
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module.LEVELS


def shipped_module():
  """The shipped ladder file, loaded from the file itself."""
  path = Path(__file__).resolve().parents[2] / "hooks" / "owui_auto_reasoning_effort.py"
  spec = importlib.util.spec_from_file_location("shipped_ladder", path)
  assert spec and spec.loader, path
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_the_shipped_hook_steps_a_try_again() -> None:
  """A try again steps the level of the last answer 1 up, and never past `high`."""
  module = shipped_module()
  value: dict = {}
  module.on_prompt(value, prompt="why is this slow", app="OWUI", retry=1, level="low")
  assert value["reasoning_effort"] == "medium"
  value = {}
  module.on_prompt(value, prompt="why is this slow", app="OWUI", retry=1, level="high")
  assert value["reasoning_effort"] == "high", "the cap is high"
  value = {}
  module.on_prompt(
    value, prompt="why is this slow", app="OWUI", retry=7, level="medium"
  )
  assert value["reasoning_effort"] == "high"
  value = {}
  module.on_prompt(
    value,
    prompt="why is this slow",
    effort="minimal",
    app="OWUI",
    retry=1,
    level="none",
  )
  assert value["reasoning_effort"] == "low", (
    "the step keeps the value of the client as a floor"
  )


def test_the_shipped_hook_takes_the_read_after_no_reasoned_answer() -> None:
  """A try again after an answer with no level takes the read of the message, and no step."""
  module = shipped_module()
  read = int(api.router.required_tier("why is this slow"))
  value: dict = {}
  module.on_prompt(value, prompt="why is this slow", app="OWUI", retry=1, level=None)
  assert value["reasoning_effort"] == module.LEVELS[read]
  value = {}
  module.on_prompt(
    value, prompt="why is this slow", effort="high", app="OWUI", retry=1, level=None
  )
  assert value["reasoning_effort"] == module.LEVELS[read], "no reasoned answer, no step"


def test_the_shipped_hook_keeps_the_level_of_a_continuing_turn() -> None:
  """An agentic step whose newest message is no user turn keeps the level of the last answer."""
  module = shipped_module()
  steps = [
    {"role": "user", "content": "Plan the migration in steps."},
    {"role": "assistant", "content": "Done with step 1."},
    {"role": "tool", "content": '{"ok": true}'},
  ]
  value: dict = {}
  module.on_prompt(value, messages=steps, app="OWUI", level="medium")
  assert value["reasoning_effort"] == "medium", "the step keeps the level of the thread"
  read = max(int(api.router.required_tier("Done with step 1.\nthanks")), 1)
  value = {}
  module.on_prompt(
    value,
    messages=steps + [{"role": "user", "content": "thanks"}],
    app="OWUI",
    level="medium",
  )
  assert value["reasoning_effort"] == module.LEVELS[read], (
    "a new user turn takes the read, not the level of the thread"
  )
  value = {}
  module.on_prompt(
    value,
    messages=steps,
    app="OWUI",
    level="none",
    model="p/m",
    config={"p": {"models": {"m": {"reasoning": "required"}}}},
  )
  assert value["reasoning_effort"] == "low", (
    "a model that requires reasoning floors the kept level"
  )


def test_the_shipped_hook_keeps_the_client_value() -> None:
  """The shipped hook preserves a client effort and otherwise selects the classified level."""
  module = shipped_module()
  read = int(api.router.required_tier("why is this slow"))
  value: dict = {}
  module.on_prompt(value, prompt="why is this slow", effort="minimal")
  assert value == {}, "a client value keeps the last word"
  value = {}
  module.on_prompt(value, prompt="why is this slow")
  assert value["reasoning_effort"] == module.LEVELS[read]


def test_the_shipped_hook_floors_a_model_that_requires_reasoning() -> None:
  """Required reasoning selects `minimal` in this fixture, while the unrestricted model keeps `none`."""
  module = shipped_module()
  config = {
    "fixture": {
      "supported_reasoning_efforts": ["none", "minimal", "low", "high"],
      "models": {"required": {"reasoning": "required"}},
    }
  }
  model = "fixture/required"
  # A TIER-D read is `none`, but a model that requires reasoning floors one step up.
  value: dict = {}
  module.on_prompt(value, prompt="thanks", app="OWUI", model=model, config=config)
  assert value["reasoning_effort"] == "minimal", value
  # A try again that would read `none` also floors to `minimal`.
  value = {}
  module.on_prompt(
    value, prompt="thanks", app="OWUI", retry=1, level=None, model=model, config=config
  )
  assert value["reasoning_effort"] == "minimal", value
  # A model with no `reasoning: required` keeps the plain read, so `none` stays.
  value = {}
  module.on_prompt(
    value, prompt="thanks", app="OWUI", model="fixture/other", config=config
  )
  assert value["reasoning_effort"] == "none", value


def test_the_shipped_hook_writes_the_retry_key_and_the_code() -> None:
  """The try-again rule names the turn from the chat header, and writes `rtN` on a repeat."""
  module = shipped_module()
  value = {"digest": "d1"}
  assert module.on_request(value, "daedalus/auto", {}) is None, "no header, no turn"
  assert (
    module.on_request(value, "daedalus/auto", {"x-openwebui-chat-id": "c1"}) == value
  )
  assert value["key"] == "c1\x00d1"
  value["count"] = 2
  module.on_request(value, "daedalus/auto", {"x-openwebui-chat-id": "c1"})
  assert value["code"] == "rt2"
  rows = module.on_init()
  assert isinstance(rows, list) and len(rows) == 1
  assert isinstance(rows[0], list) and len(rows[0]) == 2
  assert rows[0][0] == "rtN"
  assert isinstance(rows[0][1], str) and rows[0][1]


def test_a_hook_file_wins_over_the_client(
  provider: Provider, hook_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The `reasoning_effort` of a hook file replaces the value of the client."""
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-prompt": [hook_file]})
  written = post(
    provider, "daedalus/auto:TIER-B", {**BODY, "reasoning_effort": "minimal"}
  )
  assert written["reasoning_effort"] == "max"


def test_the_shipped_hook_reads_the_newest_turn() -> None:
  """The level comes from the newest user turn, not from the turns joined."""
  module = shipped_module()
  older = "Write a Python function that reverses a string."
  newest = "thanks"
  assert api.router.required_tier(older) != api.router.required_tier(newest), (
    "the pair reads differently"
  )
  value: dict = {}
  module.on_prompt(
    value,
    prompt=f"{older}\n{newest}",
    messages=[
      {"role": "user", "content": older},
      {"role": "user", "content": newest},
    ],
    app="OWUI",
  )
  assert (
    value["reasoning_effort"] == module.LEVELS[int(api.router.required_tier(newest))]
  )


def test_the_shipped_hook_reads_a_parted_content() -> None:
  """A newest turn that carries a list of parts reads as its text."""
  module = shipped_module()
  value: dict = {}
  parts = [{"type": "text", "text": "Write a Python function that reverses a string."}]
  module.on_prompt(value, messages=[{"role": "user", "content": parts}], app="OWUI")
  read = int(api.router.required_tier(parts[0]["text"]))
  assert value["reasoning_effort"] == module.LEVELS[read]


def test_the_shipped_hook_caps_the_model_turn() -> None:
  """Join the model and newest user turns, with a cap on the model portion."""
  module = shipped_module()
  answer, newest = "a" * 900, "thanks"
  text = module._newest(
    [
      {"role": "user", "content": "Write a Python function that reverses a string."},
      {"role": "assistant", "content": answer},
      {"role": "user", "content": newest},
    ],
    "",
  )
  assert text == f"{answer[-module.ANSWER_CHARS :]}\n{newest}"
  assert len(text) == module.ANSWER_CHARS + 1 + len(newest)
  assert module._newest([], "the joined prompt") == "the joined prompt", (
    "the prompt stays the fallback"
  )


def test_the_shipped_hook_ladders_the_catalog_efforts(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """A model with a catalog effort list ladders by that list, over the coded set."""
  monkeypatch.setattr(
    api.router, "DEFAULT_EFFORTS", {"fixture": ("none", "minimal", "high")}
  )
  module = shipped_module()
  value: dict = {}
  module.on_prompt(
    value,
    prompt="why is this slow",
    app="OWUI",
    retry=1,
    level="none",
    model="fixture/m",
    config={"fixture": {}},
  )
  assert value["reasoning_effort"] == "minimal", "the coded set of the gateway"
  store.write_store(
    [
      {
        "id": "fixture/m",
        "supported_efforts": ["low", "high"],
      }
    ]
  )
  value = {}
  module.on_prompt(
    value,
    prompt="why is this slow",
    app="OWUI",
    retry=1,
    level="none",
    model="fixture/m",
    config={"fixture": {}},
  )
  assert value["reasoning_effort"] == "high", "one step up on the list of the catalog"


def test_the_shipped_hook_ladders_the_efforts_of_the_model() -> None:
  """The supported_reasoning_efforts key of a model re-indexes the ladder of the hook."""
  module = shipped_module()
  config = {
    "p": {"models": {"m": {"supported_reasoning_efforts": ["none", "medium", "high"]}}}
  }
  value: dict = {}
  module.on_prompt(
    value,
    prompt="why is this slow",
    app="OWUI",
    retry=1,
    level="none",
    model="p/m",
    config=config,
  )
  assert value["reasoning_effort"] == "medium", "one step above none on the model list"
  value = {}
  module.on_prompt(
    value,
    prompt="why is this slow",
    app="OWUI",
    retry=1,
    level="high",
    model="p/m",
    config=config,
  )
  assert value["reasoning_effort"] == "high", (
    "the ladder caps at the list and at the top tier"
  )


def test_an_installed_file_runs_on_prompt_without_a_key(
  provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
  """An installed file with a `surfaces` block sets the level with no `hooks` key in the settings."""
  ladder_file(monkeypatch)
  monkeypatch.setattr(api, "REQUEST_HOOKS", {})
  monkeypatch.setattr(
    upstream.store, "model_limits", lambda candidate: {"reasoning_effort": "minimal"}
  )
  body = {**BODY, "messages": [{"role": "user", "content": "why is this slow, again"}]}
  written = post(provider, "daedalus/auto:TIER-B", body, app="OWUI")
  assert written["reasoning_effort"] != "minimal", "the installed file ran"
