"""Tests for the OpenRouter image and embedding models."""

from pathlib import Path

import pytest

from daedalus import config, providers
from daedalus.catalog import discovery, enrichment
from daedalus.routing import router

MODEL = "openrouter/recraft/recraft-v3:free"


def listing(url: str, headers: dict[str, str]) -> dict:
  """The default OpenRouter model list: text models only."""
  return {"data": [{"id": "some/chat:free", "pricing": {"prompt": "0"}}]}


def test_shipped_model(monkeypatch: pytest.MonkeyPatch) -> None:
  """The catalog keeps the declared image model, which the text list does not show."""
  monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
  loaded = config.load_config(Path("config/providers/free.yml"))
  shipped = {"openrouter": loaded["openrouter"]}
  rows, skipped = discovery.build_rows(shipped, listing)
  assert MODEL in rows, (rows, skipped)
  found, _ = enrichment.enrich([MODEL], shipped, fetch=lambda url, headers: {})
  assert found[0]["mode"] == "image_generation", found
  assert router.model_order(loaded, MODEL) == 2


def test_image_request(monkeypatch: pytest.MonkeyPatch) -> None:
  """An image request goes to `/images`, with only the fields of the Image API."""
  monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
  loaded = config.load_config(Path("config/providers/free.yml"))
  provider, slug = providers.provider_for(MODEL, loaded)
  url, options, headers = provider.image_request(
    slug,
    {
      "model": "daedalus/photos",
      "prompt": "a cat",
      "size": "1024x1024",
      "response_format": "b64_json",
      "style": "vivid",
    },
  )
  assert url == "https://openrouter.ai/api/v1/images", url
  assert options["json"] == {
    "prompt": "a cat",
    "size": "1024x1024",
    "model": "recraft/recraft-v3:free",
  }, options
  assert headers["Authorization"] == "Bearer sk-or-test", headers


def test_embedding_models(monkeypatch: pytest.MonkeyPatch) -> None:
  """The catalog keeps the declared free embedding models, with the embedding mode."""
  monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
  loaded = config.load_config(Path("config/providers/free.yml"))
  shipped = {"openrouter": loaded["openrouter"]}
  rows, _ = discovery.build_rows(shipped, listing)
  names = [
    "openrouter/liquid/lfm-2.5-embedding-350m:free",
    "openrouter/nvidia/nemotron-3-embed-1b:free",
    "openrouter/nvidia/llama-nemotron-embed-vl-1b-v2:free",
  ]
  assert set(names) <= set(rows), rows
  found, _ = enrichment.enrich(names, shipped, fetch=lambda url, headers: {})
  assert {row["mode"] for row in found} == {"embedding"}, found
