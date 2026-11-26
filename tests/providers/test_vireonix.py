from daedalus import providers

SETUP = {"vireonix": {"tier": {"TIER-B": ["auto"]}}}


def test_vireonix_request() -> None:
  provider, slug = providers.provider_for("vireonix/auto", SETUP)
  assert type(provider).__name__ == "VireonixProvider"
  assert slug == "auto"
  url, body, headers = provider.request(
    "auto", {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
  )
  assert url == "https://vireonix.ai/v1/chat/completions", url
  assert body == {
    "model": "auto",
    "messages": [{"role": "user", "content": "hi"}],
  }, body
  assert not any(key.lower() == "authorization" for key in headers)


def test_vireonix_columns() -> None:
  row = {
    "id": "auto",
    "display_name": "Auto",
    "context_window": 1000000,
    "burn_rate": 1,
    "input_tokens_per_hour": 20000000,
    "output_tokens_per_hour": 200000,
  }
  assert providers.VireonixProvider.columns(row) == {
    "name": "Auto",
    "max_input_tokens": 1000000,
  }
