"""The optional auto-effort hook consumes per-request controls before upstream calls."""

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize(
  "controls", [None, {}, True, {"sensitivity": 0.9, "minimum_effort": "high"}]
)
@pytest.mark.parametrize("chat", [False, True])
def test_request_hook_removes_private_controls_without_needing_prompt_text(
  controls, chat
):
  """Controls cannot reach upstream even without a chat header or a nonempty prompt."""
  path = Path(__file__).resolve().parents[2] / "hooks" / "owui_auto_reasoning_effort.py"
  spec = importlib.util.spec_from_file_location("auto_effort_controls", path)
  assert spec and spec.loader
  hook = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(hook)
  body = {"model": "p/model", "messages": [], "_daedalus_auto_effort": controls}
  value = {"body": body, "digest": "turn", "count": 3}
  result = hook.on_request(value, "p/model", {hook.HEADER: "chat"} if chat else {})
  assert result is not None
  assert "_daedalus_auto_effort" not in result["body"]
  assert result["body"]["model"] == "p/model"
  assert result["body"]["messages"] == []
  if chat:
    assert result["key"] == "chat\x00turn"
    assert result["code"] == "rt3"


@pytest.mark.anyio
async def test_filter_sends_controls_without_changing_explicit_effort():
  """The Filter passes its valves to the hook without selecting client effort itself."""
  path = (
    Path(__file__).resolve().parents[2]
    / "integrations/openwebui/functions/auto_reasoning_effort.py"
  )
  spec = importlib.util.spec_from_file_location("auto_effort_filter", path)
  assert spec and spec.loader
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  filter = module.Filter()
  filter.valves = filter.Valves(sensitivity=0.3, minimum_effort="medium")
  body = {
    "model": "daedalus/auto",
    "messages": [{"role": "user", "content": "hello"}],
    "reasoning_effort": "low",
  }
  result = await filter.inlet(body)
  assert result["_daedalus_auto_effort"] == {
    "sensitivity": 0.3,
    "minimum_effort": "medium",
  }
  assert result["reasoning_effort"] == "low"
  assert result["messages"] == [{"role": "user", "content": "hello"}]
  for values in (
    {"sensitivity": -0.1},
    {"sensitivity": 1.1},
    {"minimum_effort": "invalid"},
  ):
    with pytest.raises(ValueError):
      filter.Valves(**values)


def test_controls_are_request_local_and_preserve_effort_priority(monkeypatch):
  """Sensitivity and floors affect automatic choices without changing retry or client priority."""
  from types import SimpleNamespace

  from daedalus.routing import classifier, router

  path = Path(__file__).resolve().parents[2] / "hooks" / "owui_auto_reasoning_effort.py"
  spec = importlib.util.spec_from_file_location("auto_effort_choices", path)
  assert spec and spec.loader
  hook = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(hook)
  thresholds = []

  def predict(prompt, artifact, threshold=None):
    thresholds.append(threshold)
    return SimpleNamespace(required_tier=1 if threshold < 0.5 else 4)

  monkeypatch.setattr(classifier, "predict", predict)
  before = router.THRESHOLD, dict(router._TIER_OF)
  for sensitivity, minimum, supported, expected in (
    (0.2, "none", ["none", "low", "medium", "high"], "none"),
    (0.9, "none", ["none", "low", "medium", "high"], "high"),
    (0.2, "medium", ["none", "low", "high"], "high"),
    (0.2, "high", ["none", "low"], "low"),
  ):
    request_values = {
      "body": {
        "_daedalus_auto_effort": {"sensitivity": sensitivity, "minimum_effort": minimum}
      },
      "digest": "same-turn",
    }
    request_values = hook.on_request(request_values, "p/x", {hook.HEADER: "chat"})
    request_values["count"] = 1
    request_values = hook.on_request(request_values, "p/x", {hook.HEADER: "chat"})
    context = {
      "model": "p/x",
      "config": {"p": {"supported_reasoning_efforts": supported}},
      "request_values": request_values,
    }
    value = {}
    hook.on_prompt(value, prompt="same prompt", **context)
    assert value["reasoning_effort"] == expected
  assert thresholds == [0.2, 0.9, 0.2, 0.2]
  assert (router.THRESHOLD, dict(router._TIER_OF)) == before
  context["config"]["p"]["supported_reasoning_efforts"] = [
    "none",
    "low",
    "medium",
    "high",
  ]
  for extra, expected in (
    ({"effort": "low"}, {}),
    (
      {"messages": [{"role": "tool", "content": "ok"}], "level": "low"},
      {"reasoning_effort": "low"},
    ),
    ({"retry": 1, "level": "low"}, {"reasoning_effort": "medium"}),
  ):
    value = {}
    hook.on_prompt(value, prompt="same prompt", **context, **extra)
    assert value == expected

  monkeypatch.setattr(router, "required_tier", lambda text: 2)
  for sensitivity in (True, "0.4", -1, 2, float("nan")):
    value = {}
    context["request_values"] = {
      "_daedalus_auto_effort": {"sensitivity": sensitivity, "minimum_effort": []}
    }
    hook.on_prompt(value, prompt="same prompt", **context)
    assert value == {"reasoning_effort": "low"}
