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
