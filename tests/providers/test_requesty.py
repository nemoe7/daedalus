from daedalus import providers

MESSAGE = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

FREE = {
  "id": "nvidia/muse-glimmer-30b",
  "input_price": 0,
  "cached_price": 0,
  "output_price": 0,
  "pricing": [
    {
      "prompt_tokens_threshold": 0,
      "input_price": 0,
      "cached_price": 0,
      "output_price": 0,
    }
  ],
  "max_output_tokens": 20480,
  "context_window": 131072,
  "supports_caching": False,
  "supports_vision": False,
  "supports_reasoning": True,
  "reasoning": {"supported_efforts": ["none", "low", "medium", "high", "xhigh", "max"]},
  "supports_tool_calling": True,
  "supports_web_search": False,
  "supports_output_json_schema": False,
}

PAID = {
  **FREE,
  "id": "vertex/claude-sonnet-5-5",
  "pricing": [
    {
      "prompt_tokens_threshold": 0,
      "input_price": 2e-06,
      "cached_price": 1e-07,
      "output_price": 1e-05,
    }
  ],
}


def test_requesty_columns() -> None:
  got = providers.RequestyProvider.columns(FREE)
  assert got["max_input_tokens"] == 131072, got
  assert got["max_output_tokens"] == 20480, got
  assert got["supports_function_calling"] is True, got
  assert got["supports_response_schema"] is False, got
  assert got["supports_reasoning"] is True, got
  assert got["supports_web_search"] is False, got
  assert got["supports_vision"] is False, got
  assert got["supported_efforts"] == [
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
  ], got
  assert "name" not in got, "rows carry no display name"


def test_requesty_free_rows() -> None:
  keep = providers.RequestyProvider.discoverable
  assert keep(FREE)
  assert not keep(PAID), "a priced row drops"
  assert not keep({"id": "x"}), "a row without pricing drops"
  assert not keep({**FREE, "pricing": []}), "an empty band list drops"
  assert not keep(
    {
      **FREE,
      "pricing": [
        {"prompt_tokens_threshold": 0, "input_price": 0, "output_price": 1e-06}
      ],
    }
  ), "a priced band drops"


def test_requesty_prepare() -> None:
  setup = {"requesty": {"api_key": "k"}}
  provider, url, _, headers = providers.prepare(
    "requesty/nvidia/muse-glimmer-30b", MESSAGE, setup
  )
  assert type(provider).__name__ == "RequestyProvider"
  assert url == "https://router.requesty.ai/v1/chat/completions", url
  assert headers["Authorization"] == "Bearer k", headers
