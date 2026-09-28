"""Tests for the Pollinations image model."""

from pathlib import Path

import pytest

from daedalus import config, providers
from daedalus.catalog import discovery, enrichment
from daedalus.routing import router

MODEL = "pollinations/tongyi-mai/z-image-turbo"
# The cheap image models and their rpm, in the sorted catalog order.
IMAGES = {
  "pollinations/black-forest-labs/flux.1-schnell": 60,
  "pollinations/black-forest-labs/flux.2-klein-4b": 60,
  "pollinations/lykon/dreamshaper-8-lcm": 300,
  MODEL: 60,
}


def listing(url: str, headers: dict[str, str]) -> dict:
  """A Pollinations model list with the cheap image models and 2 paid models."""
  assert url == "https://gen.pollinations.ai/v1/models", url
  assert headers["Authorization"] == "Bearer sk_test", headers
  return {
    "data": [
      *({"id": name.removeprefix("pollinations/")} for name in IMAGES),
      {"id": "openai/gpt-5.5"},
      {"id": "openai/gpt-image-2"},
    ]
  }


def test_shipped_block(monkeypatch: pytest.MonkeyPatch) -> None:
  """The shipped block keeps only the cheap image models, with their rpm, at the order of Cloudflare."""
  monkeypatch.setenv("POLLINATIONS_API_KEY", "sk_test")
  loaded = config.load_config(Path("config/providers/free.yml"))
  shipped = {"pollinations": loaded["pollinations"]}
  rows, skipped = discovery.build_rows(shipped, listing)
  assert list(rows) == list(IMAGES), (rows, skipped)
  found, problems = enrichment.enrich(rows, shipped, fetch=lambda url, headers: {})
  assert [(r["id"], r["mode"], r["rpm"]) for r in found] == [
    (name, "image_generation", rpm) for name, rpm in IMAGES.items()
  ], (found, problems)
  assert {router.model_order(loaded, name) for name in IMAGES} == {2}
  assert router.model_order(loaded, "cloudflare/@cf/openai/whisper") == 2
  assert router.model_order(loaded, "groq/whisper-large-v3") == 1
  provider, slug = providers.provider_for(MODEL, shipped)
  url, options, headers = provider.image_request(
    slug, {"prompt": "a lighthouse", "size": "1024x1024", "model": "daedalus/photos"}
  )
  assert url == "https://gen.pollinations.ai/v1/images/generations", url
  assert options["json"] == {
    "prompt": "a lighthouse",
    "size": "1024x1024",
    "model": "tongyi-mai/z-image-turbo",
  }, options
  assert headers["Authorization"] == "Bearer sk_test", headers
