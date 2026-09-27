from daedalus.catalog import discovery


def check_cloudflare() -> None:
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


def check_gemini() -> None:
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


def check_groq() -> None:
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


def check_openrouter_shape() -> None:
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


def check_mistral() -> None:
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


def check_unknown() -> None:
  assert discovery.native_columns("z-ai", {"context_length": 5}) == {}
  assert discovery.native_columns("groq", {"context_window": "12k"}) == {}


def check_build_rows() -> None:
  payload = {"data": [{"id": "m1", "context_window": 8192}, {"id": "m2"}]}
  config = {"groq": {"api_key": "k", "exclude": ["m2"]}}
  lines, skipped = discovery.build_rows(config, lambda *_: payload)
  assert skipped == [], skipped
  assert lines == {"groq/m1": {"max_input_tokens": 8192}}, lines


def main() -> None:
  check_cloudflare()
  check_gemini()
  check_groq()
  check_openrouter_shape()
  check_mistral()
  check_unknown()
  check_build_rows()
  print("ok: provider columns")


if __name__ == "__main__":
  main()
