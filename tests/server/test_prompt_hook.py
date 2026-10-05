"""Tests for the `on-prompt` hook and the tier map of the reasoning effort."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

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
  """A hook file of the `on-prompt` point: it records its surfaces and sets a value."""
  path = hooks.CONFIG_DIR / "hooks" / "prompt_probe.py"
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


def test_tier_map(provider: Provider) -> None:
  """The tier of the chain sets the effort: `TIER-A` high, `TIER-D` none."""
  assert post(provider, "daedalus/auto:TIER-A")["reasoning_effort"] == "high"
  assert post(provider, "daedalus/auto:TIER-D")["reasoning_effort"] == "none"
  assert post(provider, "daedalus/koinos")["reasoning_effort"] == "low"


def test_client_wins(provider: Provider) -> None:
  """A `reasoning_effort` of the client keeps the last word over the tier map."""
  body = {**BODY, "reasoning_effort": "minimal"}
  assert post(provider, "daedalus/auto:TIER-A", body)["reasoning_effort"] == "minimal"


def test_catalog_wins(provider: Provider, monkeypatch: pytest.MonkeyPatch) -> None:
  """A stored effort of the model wins over the tier map."""
  monkeypatch.setattr(
    upstream.store, "model_limits", lambda candidate: {"reasoning_effort": "medium"}
  )
  assert post(provider, "daedalus/auto:TIER-A")["reasoning_effort"] == "medium"


def test_hook_file_wins(
  provider: Provider, hook_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A hook file answers above the catalog default, and it sees each surface."""
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-prompt": [hook_file]})
  written = post(provider, "daedalus/auto:TIER-B")
  assert written["reasoning_effort"] == "max"
  dump = json.loads((hooks.CONFIG_DIR / "hooks" / "prompt_probe.json").read_text())
  assert set(dump["keys"]) == {
    "messages",
    "prompt",
    "model",
    "tier",
    "tier_name",
    "slot",
    "reasoning",
    "effort",
    "body",
    "config",
    "key",
    "app",
  }, dump["keys"]
  assert dump["tier_name"] == "TIER-B" and dump["slot"] == "daedalus/auto:TIER-B"
  assert dump["reasoning"] == ["a/1"], dump["reasoning"]
  assert dump["effort"] is None, dump["effort"]


def test_no_reasoning_model(
  provider: Provider, hook_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A chain with no reasoning model gets no call and no effort."""
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-prompt": [hook_file]})
  monkeypatch.setattr(upstream.store, "reasoning_flags", lambda: {"a/1": False})
  written = post(provider, "daedalus/auto:TIER-A")
  assert "reasoning_effort" not in written, written
  assert not (hooks.CONFIG_DIR / "hooks" / "prompt_probe.json").exists(), (
    "the hook did not run"
  )


def test_the_shipped_ladder_file_sets_the_effort(
  provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The shipped `auto_reasoning.py` sets the level from the heuristics read of the prompt."""
  shipped = (
    Path(__file__).resolve().parents[2] / "config" / "hooks" / "auto_reasoning.py"
  )
  target = hooks.CONFIG_DIR / "hooks" / "auto_reasoning.py"
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
  hooks._loaded.pop(target.resolve(), None)
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-prompt": ["hooks/auto_reasoning.py"]})
  monkeypatch.setattr(
    upstream.store, "model_limits", lambda candidate: {"reasoning_effort": "minimal"}
  )
  written = post(provider, "daedalus/auto:TIER-B", app="OWUI")
  tier = int(api.router.required_tier(BODY["messages"][0]["content"]))
  assert written["reasoning_effort"] == api.EFFORT_OF_TIER[api.router.TIER_NAMES[tier]]
  assert written["reasoning_effort"] != "minimal", (
    "the hook answers above the catalog default"
  )


def test_the_shipped_ladder_file_skips_another_client(
  provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The shipped `auto_reasoning.py` leaves the effort of Kilo and of a generic client alone."""
  shipped = (
    Path(__file__).resolve().parents[2] / "config" / "hooks" / "auto_reasoning.py"
  )
  target = hooks.CONFIG_DIR / "hooks" / "auto_reasoning.py"
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
  hooks._loaded.pop(target.resolve(), None)
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-prompt": ["hooks/auto_reasoning.py"]})
  monkeypatch.setattr(
    upstream.store, "model_limits", lambda candidate: {"reasoning_effort": "minimal"}
  )
  assert (
    post(provider, "daedalus/auto:TIER-B", app="Kilo")["reasoning_effort"] == "minimal"
  )
  assert post(provider, "daedalus/auto:TIER-B")["reasoning_effort"] == "minimal"


def test_with_defaults_floor(monkeypatch: pytest.MonkeyPatch) -> None:
  """The floor fills the effort only when neither the client nor the catalog gives one."""
  body = {"model": "m"}
  assert upstream.with_defaults("a/1", body, "high")["reasoning_effort"] == "high"
  assert upstream.with_defaults("a/1", body) == body, "no floor, no effort"
  asked = {"model": "m", "reasoning_effort": "low"}
  assert upstream.with_defaults("a/1", asked, "high")["reasoning_effort"] == "low"
  monkeypatch.setattr(
    upstream.store, "model_limits", lambda candidate: {"reasoning_effort": "medium"}
  )
  assert upstream.with_defaults("a/1", body, "high")["reasoning_effort"] == "medium"
