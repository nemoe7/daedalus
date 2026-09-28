"""Tests for the Pollinations image model."""

from pathlib import Path

import pytest

from daedalus import config, providers
from daedalus.catalog import discovery, enrichment

MODEL = "pollinations/tongyi-mai/z-image-turbo"


def listing(url: str, headers: dict[str, str]) -> dict:
  """A Pollinations model list with the image model and 2 paid models."""
  assert url == "https://gen.pollinations.ai/v1/models", url
  assert headers["Authorization"] == "Bearer sk_test", headers
  return {
    "data": [
      {"id": "tongyi-mai/z-image-turbo"},
      {"id": "openai/gpt-5.5"},
      {"id": "openai/gpt-image-2"},
    ]
  }


def test_shipped_block(monkeypatch: pytest.MonkeyPatch) -> None:
  """The shipped block keeps only Z-Image Turbo, as an image model with its rpm."""
  monkeypatch.setenv("POLLINATIONS_API_KEY", "sk_test")
  loaded = config.load_config(Path("config/providers/free.yml"))
  shipped = {"pollinations": loaded["pollinations"]}
  rows, skipped = discovery.build_rows(shipped, listing)
  assert list(rows) == [MODEL], (rows, skipped)
  found, problems = enrichment.enrich(rows, shipped, fetch=lambda url, headers: {})
  assert [(r["id"], r["mode"], r["rpm"]) for r in found] == [
    (MODEL, "image_generation", 60)
  ], (found, problems)
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
