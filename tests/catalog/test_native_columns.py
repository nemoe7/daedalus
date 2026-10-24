import sys

import pytest

from daedalus.catalog import discovery
from daedalus.providers.base import count


def test_cloudflare() -> None:
  row = {
    "name": "@cf/a/b",
    "task": {"name": "Text Generation"},
    "properties": [
      {"property_id": "context_window", "value": "128000"},
      {"property_id": "function_calling", "value": "true"},
      {"property_id": "reasoning_effort", "value": {"default_effort": "max"}},
    ],
  }
  found = discovery.native_columns("cloudflare", row)
  assert found == {
    "mode": "chat",
    "max_input_tokens": 128000,
    "reasoning_effort": "max",
    "supports_function_calling": True,
  }, found


def test_cloudflare_tasks() -> None:
  tasks = {
    "@cf/chat": "Text Generation",
    "@cf/whisper": "Automatic Speech Recognition",
    "@cf/melotts": "Text-to-Speech",
    "@cf/flux": "Text-to-Image",
    "@cf/bge": "Text Embeddings",
    "@cf/resnet": "Image Classification",
    "@cf/paid": "Text-to-Image",
  }
  rows = [{"name": name, "task": {"name": task}} for name, task in tasks.items()]
  rows[-1]["properties"] = [{"property_id": "require_workers_paid", "value": "true"}]
  rows.append({"name": "@cf/none"})
  provider = {"discovery_match": {"require_workers_paid": False}}
  kept = discovery.provider_rows("cloudflare", provider, {"result": rows})
  assert list(kept) == list(tasks)[:5], kept
  modes = [discovery.native_columns("cloudflare", row)["mode"] for row in kept.values()]
  assert modes == [
    "chat",
    "audio_transcription",
    "audio_speech",
    "image_generation",
    "embedding",
  ], modes
  assert list(discovery.provider_rows("groq", {}, {"data": [{"id": "x"}]})) == ["x"]


def test_gemini() -> None:
  row = {
    "supportedGenerationMethods": ["generateContent"],
    "inputTokenLimit": 1048576,
    "outputTokenLimit": 65536,
  }
  found = discovery.native_columns("gemini", row)
  assert found == {
    "mode": "chat",
    "max_input_tokens": 1048576,
    "max_output_tokens": 65536,
    "max_tokens": 65536,
  }, "no thinking field leaves the column to LiteLLM"
  embed = discovery.native_columns(
    "gemini", {"supportedGenerationMethods": ["embedContent"]}
  )
  assert embed == {"mode": "embedding"}, embed


def test_groq() -> None:
  row = {
    "context_window": 131072,
    "max_completion_tokens": 16384,
    "supported_features": ["tools", "json_mode"],
    "input_modalities": ["text", "image"],
    "output_modalities": ["text"],
  }
  found = discovery.native_columns("groq", row)
  assert found["supports_function_calling"] is True, found
  assert found["supports_response_schema"] is False, "a full list gives false"
  assert found["supports_vision"] is True and found["supports_audio_output"] is False
  bare = discovery.native_columns("groq", {"context_window": 512})
  assert bare == {"max_input_tokens": 512}, "no list gives no flags"


def test_openrouter_shape() -> None:
  row = {
    "context_length": 1000,
    "top_provider": {"context_length": 2000, "max_completion_tokens": 500},
    "supported_parameters": ["tools", "tool_choice", "structured_outputs"],
    "architecture": {
      "input_modalities": ["text", "file"],
      "output_modalities": ["text"],
    },
    "reasoning": {"default_effort": "high"},
  }
  for name in ("kilo", "openrouter"):
    found = discovery.native_columns(name, row)
    assert found["max_input_tokens"] == 2000 and found["max_tokens"] == 500, found
    assert found["supports_tool_choice"] is True, found
    assert found["supports_parallel_function_calling"] is False, found
    assert found["supports_pdf_input"] is True and found["reasoning_effort"] == "high"


def test_openrouter_supported_efforts() -> None:
  """A gateway row lists the efforts of the model, and the empty list stays out."""
  row = {
    "max_context_length": 2000,
    "reasoning": {
      "default_effort": "high",
      "supported_efforts": ["xhigh", "high", "low", "none"],
    },
  }
  found = discovery.native_columns("openrouter", row)
  assert found["reasoning_effort"] == "high", found
  assert found["supported_efforts"] == ["xhigh", "high", "low", "none"], found
  row["reasoning"] = {"supported_efforts": None}
  assert "supported_efforts" not in discovery.native_columns("kilo", row)
  row["reasoning"] = {"supported_efforts": [1, "high"]}
  assert "supported_efforts" not in discovery.native_columns("kilo", row)


def test_mistral() -> None:
  row = {
    "max_context_length": 256000,
    "capabilities": {
      "completion_chat": True,
      "function_calling": False,
      "vision": True,
    },
  }
  found = discovery.native_columns("mistral", row)
  assert found == {
    "mode": "chat",
    "max_input_tokens": 256000,
    "supports_function_calling": False,
    "supports_vision": True,
  }, found


def test_unknown() -> None:
  assert discovery.native_columns("z-ai", {"context_length": 5}) == {}
  assert discovery.native_columns("groq", {"context_window": "12k"}) == {}


def test_build_rows() -> None:
  payload = {"data": [{"id": "m1", "context_window": 8192}, {"id": "m2"}]}
  config = {"groq": {"api_key": "k", "exclude": ["m2"]}}
  lines, skipped = discovery.build_rows(config, lambda *_: payload)
  assert skipped == [], skipped
  assert lines == {"groq/m1": {"max_input_tokens": 8192}}, lines


@pytest.mark.parametrize(
  ("value", "expected"),
  [
    (8192, 8192),
    ("8192", 8192),
    ("８１９２", 8192),
    ("008192", 8192),
    (0, None),
    (-1, None),
    ("0", None),
    ("-1", None),
    ("", None),
    ("1.0", None),
    (8192.0, None),
    (True, None),
    (False, None),
    (None, None),
    ("NaN", None),
    ({}, None),
  ],
)
def test_token_count_inputs(value, expected) -> None:
  assert count(value) == expected


@pytest.fixture(params=["digit-like", "long-string", "long-integer"])
def unparseable_count(request):
  if request.param == "digit-like":
    yield "²"
    return
  if not hasattr(sys, "set_int_max_str_digits"):
    pytest.skip("This Python runtime has no integer conversion limit.")
  previous = sys.get_int_max_str_digits()
  try:
    sys.set_int_max_str_digits(640)
    yield "9" * 641 if request.param == "long-string" else 10**641
  finally:
    sys.set_int_max_str_digits(previous)


def test_token_count_conversion_failure(unparseable_count) -> None:
  assert count(unparseable_count) is None


def test_discovery_token_conversion_failure(unparseable_count) -> None:
  payload = {
    "data": [
      {
        "id": "example",
        "context_window": unparseable_count,
        "max_completion_tokens": 4096,
        "supported_features": ["tools"],
      }
    ]
  }
  providers = {
    "groq": {"api_key": "placeholder", "discovery_url": "https://groq.test/models"}
  }
  rows, skipped = discovery.build_rows(providers, lambda *_: payload)
  assert not skipped
  found = rows["groq/example"]
  assert "max_input_tokens" not in found
  assert found["max_output_tokens"] == found["max_tokens"] == 4096
  assert found["supports_function_calling"] is True


def test_display_name_extraction_and_humanizer() -> None:
  """Upstream display names strip gateway affixes and echo slugs fall back to humanize_slug."""
  from daedalus.providers.base import clean_display_name, humanize_slug

  assert (
    clean_display_name("Baseten: Mammoth 1 (free)", "baseten/mammoth-1:free")
    == "Mammoth 1"
  )
  assert (
    clean_display_name(
      "Canopy Labs Orpheus V1 English", "canopylabs/orpheus-v1-english"
    )
    == "Orpheus V1 English"
  )
  assert clean_display_name("Qwen/Qwen3.8-27B", "qwen/qwen3.8-27b") is None
  assert clean_display_name("@cf/meta/llama-3.1-8b", "@cf/meta/llama-3.1-8b") is None
  assert clean_display_name("codestral-2508", "codestral-2508") is None
  assert humanize_slug("@cf/openai/gpt-oss-120b") == "GPT OSS 120B"
  assert humanize_slug("labs-leanstral-1-5-1") == "Labs Leanstral 1.5.1"
  assert (
    discovery.native_columns(
      "gemini",
      {
        "name": "models/gemini-3.8-flash",
        "displayName": "Gemini 3.8 Flash",
        "supportedGenerationMethods": ["generateContent"],
      },
    )["name"]
    == "Gemini 3.8 Flash"
  )
  assert (
    discovery.native_columns(
      "pollinations",
      {"id": "black-forest-labs/flux.1-schnell", "title": "FLUX.1 Schnell"},
    )["name"]
    == "FLUX.1 Schnell"
  )
