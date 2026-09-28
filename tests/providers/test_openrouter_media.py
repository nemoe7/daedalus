"""Tests for the OpenRouter media models: discovery of each output type, speech and images."""

from pathlib import Path

import pytest

from daedalus import config, providers
from daedalus.catalog import discovery
from daedalus.providers.base import openrouter_columns

FREE = {"prompt": "0", "completion": "0"}


def row(slug: str, outputs: list[str], pricing: dict | None = None) -> dict:
  """One OpenRouter model row with the output modalities."""
  return {
    "id": slug,
    "pricing": pricing or FREE,
    "architecture": {"input_modalities": ["text"], "output_modalities": outputs},
  }


def listing(url: str, headers: dict[str, str]) -> dict:
  """The OpenRouter model list of each output type, if the URL asks for all of them."""
  assert "output_modalities=all" in url, url
  return {
    "data": [
      row("some/chat:free", ["text"]),
      row("nvidia/nemotron-3-embed-1b:free", ["embeddings"]),
      row("deepgram/flux-tts:free", ["speech"]),
      row("fish-audio/s2-pro", ["speech"], {"prompt": "0.000015"}),
      row("recraft/recraft-v3:free", ["image"]),
    ]
  }


def modes(block: dict) -> tuple[dict[str, str | None], list[str]]:
  """The mode of each kept catalog line of one OpenRouter block."""
  lines, skipped = discovery.build_rows({"openrouter": block}, listing)
  return {name: columns.get("mode") for name, columns in lines.items()}, skipped


def test_output_mode() -> None:
  """The mode comes from the output modalities, and a model that makes text has no mode."""
  modes = {
    "speech": "audio_speech",
    "image": "image_generation",
    "embeddings": "embedding",
    "video": "video_generation",
    "decisions": "decisions",
  }
  for output, mode in modes.items():
    assert openrouter_columns(row("a/b", [output]))["mode"] == mode, output
  assert "mode" not in openrouter_columns(row("a/b", ["image", "text"]))
  assert "mode" not in openrouter_columns({"id": "a/b"})


def test_free_media(monkeypatch: pytest.MonkeyPatch) -> None:
  """Discovery keeps the free embedding and speech models, but no paid or image model."""
  monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
  loaded = config.load_config(Path("config/providers/free.yml"))
  block = {
    key: value for key, value in loaded["openrouter"].items() if key != config.FILE_KEY
  }
  lines, skipped = modes(block)
  assert not skipped, skipped
  assert lines == {
    "openrouter/deepgram/flux-tts:free": "audio_speech",
    "openrouter/nvidia/nemotron-3-embed-1b:free": "embedding",
    "openrouter/some/chat:free": None,
  }, lines


def test_speech_request(monkeypatch: pytest.MonkeyPatch) -> None:
  """A speech request asks for mp3 unless the client asks for pcm."""
  monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
  loaded = config.load_config(Path("config/providers/free.yml"))
  provider, slug = providers.provider_for("openrouter/deepgram/flux-tts:free", loaded)
  for asked, sent in ((None, "mp3"), ("wav", "mp3"), ("pcm", "pcm"), ("mp3", "mp3")):
    payload = {"input": "hi", "voice": "flux-alexis-en", "response_format": asked}
    url, options, _ = provider.speech_request(slug, payload)
    assert url == "https://openrouter.ai/api/v1/audio/speech", url
    assert options["json"]["response_format"] == sent, (asked, options)
    assert options["json"]["voice"] == "flux-alexis-en", options


def test_image_request(monkeypatch: pytest.MonkeyPatch) -> None:
  """An image request goes to `/images`, with only the fields of the Image API."""
  monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
  loaded = config.load_config(Path("config/providers/free.yml"))
  provider, slug = providers.provider_for("openrouter/some/image", loaded)
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
    "model": "some/image",
  }, options
  assert headers["Authorization"] == "Bearer sk-or-test", headers
