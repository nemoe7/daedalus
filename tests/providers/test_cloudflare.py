import io

import httpx
import pytest
from PIL import Image

from daedalus import providers
from daedalus.providers.base import ProviderError
from daedalus.providers.cloudflare import smaller

CLOUDFLARE = providers.CloudflareProvider(
  "cloudflare", {"api_base": "https://cloudflare.test", "api_key": "k"}
)


def test_cloudflare() -> None:
  image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}
  messages = [
    {"role": "system", "content": [{"type": "text", "text": "Be terse."}]},
    {"role": "user", "content": "hi"},
    {
      "role": "assistant",
      "content": None,
      "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}
      ],
    },
    {"role": "tool", "tool_call_id": "c", "content": [{"type": "text", "text": "a"}]},
    {"role": "user", "content": [{"type": "text", "text": "x"}, image]},
  ]
  payload = {"model": "m", "messages": messages}
  sent = CLOUDFLARE.body("@cf/m", payload)["messages"]
  assert sent[0]["content"] == "Be terse.", "a text-part list becomes a string"
  assert sent[1]["content"] == "hi"
  assert sent[2]["content"] == "" and sent[2]["tool_calls"], (
    "null content becomes empty"
  )
  assert sent[3]["content"] == "a"
  assert sent[4]["content"][1] == image, "a list with an image stays a list"
  assert payload["messages"][0]["content"][0]["text"] == "Be terse.", (
    "no change in place"
  )
  text = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
  joined = CLOUDFLARE.body("@cf/m", {"messages": [{"role": "user", "content": text}]})
  assert joined["messages"][0]["content"] == "a\nb", "text parts join with a new line"


@pytest.mark.parametrize("side", [2, 3])
def test_image_size_guard(monkeypatch, side: int) -> None:
  """An image above the size guard becomes a provider error."""
  output = io.BytesIO()
  Image.new("RGB", (side, side)).save(output, format="PNG")
  upload = ("in.png", output.getvalue(), "image/png")
  monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 4)
  if side == 2:
    assert smaller(upload) == upload
  else:
    with pytest.raises(ProviderError) as caught:
      smaller(upload)
    assert isinstance(caught.value.__cause__, Image.DecompressionBombError)
  assert Image.MAX_IMAGE_PIXELS == 4


def test_flux2_multipart() -> None:
  """FLUX.2 requests use multipart data, including prompt-only requests, while other models use JSON."""
  payload = {"prompt": "a cat", "size": "1024x768"}
  slug = "@cf/black-forest-labs/flux-2-klein-4b"
  url, options, headers = CLOUDFLARE.image_request(slug, payload)
  assert url == f"https://cloudflare.test/run/{slug}", url
  assert "content-type" not in {key.lower() for key in headers}, headers
  sent = httpx.Request("POST", url, headers=headers, **options)
  assert sent.headers["content-type"].startswith("multipart/form-data"), sent.headers
  body = sent.read().decode()
  for name, value in (("prompt", "a cat"), ("width", "1024"), ("height", "768")):
    assert f'name="{name}"\r\n\r\n{value}\r\n' in body, (name, body)
  _, options, headers = CLOUDFLARE.image_request(
    "@cf/black-forest-labs/flux-1-schnell", payload
  )
  assert options == {"json": {"prompt": "a cat"}}, options


def sent(slug: str, effort: object) -> dict:
  """The body that goes to Workers AI for one client effort."""
  payload = {"messages": [{"role": "user", "content": "hi"}]}
  if effort is not None:
    payload["reasoning_effort"] = effort
  return CLOUDFLARE.body(slug, payload)


def test_the_on_off_toggles_map_the_effort_to_enable_thinking() -> None:
  """The documented on/off models send `enable_thinking`, and never the effort parameter."""
  for slug in (
    "@cf/zai-org/glm-4.7-flash",
    "@cf/google/gemma-4-26b-a4b-it",
    "@cf/nvidia/nemotron-3-120b-a12b",
  ):
    body = sent(slug, "max")
    assert body["chat_template_kwargs"] == {"enable_thinking": True}, slug
    assert "reasoning_effort" not in body, slug
    body = sent(slug, "none")
    assert body["chat_template_kwargs"] == {"enable_thinking": False}, slug
    assert "reasoning_effort" not in body, slug
  assert CLOUDFLARE.effort(sent("@cf/zai-org/glm-4.7-flash", "none")) == "none"
  assert CLOUDFLARE.effort(sent("@cf/zai-org/glm-4.7-flash", "max")) == "max"


def test_the_no_control_rows_drop_the_effort_and_log_it(caplog) -> None:
  """Clear unsupported effort settings for always-reasoning models and log the action."""
  for slug in (
    "@cf/qwen/qwq-32b",
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    "@cf/qwen/qwen3-30b-a3b-fp8",
  ):
    with caplog.at_level("INFO", logger="daedalus"):
      body = sent(slug, "max")
    assert "reasoning_effort" not in body, slug
    assert "chat_template_kwargs" not in body, slug
    assert any(slug in line and "dropped" in line for line in caplog.messages), slug
  assert CLOUDFLARE.effort(sent("@cf/qwen/qwq-32b", "max")) is None


def test_a_model_outside_the_toggles_keeps_the_pass_through() -> None:
  """The other Workers AI rows keep the reasoning_effort pass-through."""
  body = sent("@cf/openai/gpt-oss-120b", "low")
  assert body["reasoning_effort"] == "low"
  assert "chat_template_kwargs" not in body
