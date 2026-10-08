import httpx
import yaml

from daedalus import providers
from daedalus.catalog.enrichment import config_params
from daedalus.routing import router

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


def test_flux2_multipart() -> None:
  """A FLUX.2 image request is multipart, also with a prompt only. Other models get JSON."""
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


def test_new_models() -> None:
  """The 2 new instruct models join TIER-D, and the Clef models stay out of the chat mode."""
  with open("config/providers/free.yml", encoding="utf-8") as handle:
    cloudflare = yaml.safe_load(handle)["cloudflare"]
  for slug in ("@cf/swiss-ai/apertus-v1.5-8b", "@cf/utter-project/eurollm-9b-it"):
    assert router.claiming_tier(cloudflare, slug) == "TIER-D", slug
  for slug in ("@cf/cloudflare/clef", "@cf/cloudflare/clef-flash"):
    assert router.claiming_tier(cloudflare, slug) is None, (
      "a decision model stays out of the pools"
    )
    assert config_params(cloudflare, slug).get("mode") == "decisions", slug


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


def test_the_toggle_ladders_are_in_the_shipped_config() -> None:
  """The 3 on/off models of the shipped config name the documented on/off ladder."""
  with open("config/providers/free.yml", encoding="utf-8") as handle:
    cloudflare = yaml.safe_load(handle)["cloudflare"]
  for slug in (
    "@cf/zai-org/glm-4.7-flash",
    "@cf/google/gemma-4-26b-a4b-it",
    "@cf/nvidia/nemotron-3-120b-a12b",
  ):
    found = router.model_setting(
      {"cloudflare": cloudflare}, f"cloudflare/{slug}", "supported_reasoning_efforts"
    )
    assert found == ["none", "max"], (slug, found)


def test_the_no_control_rows_drop_the_effort_and_log_it(caplog) -> None:
  """The always-reasoning models document no control: the effort drops, and the drop is logged."""
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


def test_the_no_control_ladders_are_in_the_shipped_config() -> None:
  """The 3 no-control models of the shipped config name the max ladder alone."""
  with open("config/providers/free.yml", encoding="utf-8") as handle:
    cloudflare = yaml.safe_load(handle)["cloudflare"]
  for slug in (
    "@cf/qwen/qwq-32b",
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    "@cf/qwen/qwen3-30b-a3b-fp8",
  ):
    found = router.model_setting(
      {"cloudflare": cloudflare}, f"cloudflare/{slug}", "supported_reasoning_efforts"
    )
    assert found == ["max"], (slug, found)


def test_a_model_outside_the_toggles_keeps_the_pass_through() -> None:
  """The other Workers AI rows keep the reasoning_effort pass-through."""
  body = sent("@cf/openai/gpt-oss-120b", "low")
  assert body["reasoning_effort"] == "low"
  assert "chat_template_kwargs" not in body
