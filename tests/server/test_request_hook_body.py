"""Request hooks replace bodies and pass per-request values to prompt hooks."""

import json

import httpx
import pytest

from daedalus import config, store
from daedalus.server import api, upstream

MASTER = "test-hook-body-master-0001"


@pytest.mark.anyio
@pytest.mark.parametrize(
  "reasoning, text", [(False, "hello"), (True, ""), (True, "hello")]
)
async def test_request_body_and_prompt_context_are_request_local(
  monkeypatch, state_folder, reasoning, text
):
  """Body replacements apply without prompt hooks and values never leak between requests."""
  path = state_folder / "hooks" / "body.py"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text("""
def on_request(value, model, headers):
  body = dict(value["body"])
  marker = body.pop("_private_test")
  body["temperature"] = 0.25 if marker == "first" else 0.75
  return {**value, "body": body, "marker": marker}

def on_prompt(value, request_values, **context):
  assert "_private_test" not in request_values["body"]
  value["reasoning_effort"] = "low" if request_values["marker"] == "first" else "high"
  return value
""")
  monkeypatch.setattr(
    api,
    "REQUEST_HOOKS",
    {"on-request": ["hooks/body.py"], "on-prompt": ["hooks/body.py"]},
  )
  config.set_config({"p": {"api_base": "https://p.test/v1", "api_key": "provider-key"}})
  store.write_store(
    [
      {
        "id": "p/x",
        "supports_reasoning": reasoning,
        "supported_efforts": ["low", "high"],
      }
    ]
  )
  seen = []

  def answer(request):
    seen.append(json.loads(request.content))
    return httpx.Response(
      200,
      json={
        "choices": [
          {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ]
      },
    )

  try:
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as outer:
      upstream.set_client(outer)
      async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {MASTER}"},
      ) as client:
        for marker in ("first", "second"):
          response = await client.post(
            "/v1/chat/completions",
            json={
              "model": "p/x",
              "messages": [{"role": "user", "content": text}],
              "_private_test": marker,
            },
          )
          assert response.status_code == 200, response.text
  finally:
    upstream.set_client(None)
    config.set_config(None)
  assert len(seen) == 2
  assert [body["temperature"] for body in seen] == [0.25, 0.75]
  assert all("_private_test" not in body for body in seen)
  if reasoning and text:
    assert [body["reasoning_effort"] for body in seen] == ["low", "high"]
  else:
    assert all("reasoning_effort" not in body for body in seen)
