import os

from daedalus import config, discovery, providers

FIELDS = ("api_base", "api_type", "discovery_url")
MESSAGE = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def main() -> None:
  loaded = config.load_config()
  assert set(providers.PROVIDERS) == set(loaded), loaded.keys()
  for name, settings in loaded.items():
    assert not set(FIELDS) & set(settings), name
    merged = providers.settings(name, settings)
    assert all(merged.get(field) is not None for field in FIELDS), (name, merged)

  groq = providers.settings("groq", {"api_key": "k"})
  assert groq["api_base"] == "https://api.groq.com/openai/v1", groq
  assert groq["discovery_url"] == "https://api.groq.com/openai/v1/models", groq
  moved = providers.settings(
    "groq", {"api_base": "https://proxy.test/v1", "api_key": ""}
  )
  assert moved["api_base"] == "https://proxy.test/v1", moved
  assert "api_key" not in moved, moved
  empty = providers.settings("groq", {"api_base": ""})
  assert empty["api_base"] == "https://api.groq.com/openai/v1", empty
  custom = providers.settings("custom", {"api_base": "https://custom.test"})
  assert custom == {"api_type": "openai", "api_base": "https://custom.test"}, custom

  os.environ["CLOUDFLARE_ACCOUNT_ID"] = "account"
  cloudflare = providers.settings("cloudflare", {})
  base = "https://api.cloudflare.com/client/v4/accounts/account/ai/v1"
  assert cloudflare["api_base"] == base, cloudflare
  gateway = providers.settings("cloudflare", {"api_base": "https://gateway.test/v1"})
  assert gateway["api_base"] == "https://gateway.test/v1", "the yml wins"
  assert "/accounts/account/" in cloudflare["discovery_url"], cloudflare
  assert "task=Text%20Generation" in cloudflare["discovery_url"], cloudflare

  setup = {
    "gemini": {"api_key": "k"},
    "custom": {"api_key": "k"},
    "groq": {"api_key": "k"},
  }
  provider, url, _, headers = providers.prepare("gemini/m", MESSAGE, setup)
  assert type(provider).__name__ == "GeminiProvider"
  assert (
    url == "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"
  )
  assert headers["x-goog-api-key"] == "k"
  setup["gemini"]["api_type"] = "interactions"
  try:
    providers.prepare("gemini/m", MESSAGE, setup)
  except providers.ProviderError:
    pass
  else:
    raise AssertionError("interactions is not an api_type")
  del setup["gemini"]["api_type"]
  provider, url, _, _ = providers.prepare("groq/m", MESSAGE, setup)
  assert type(provider).__name__ == "GroqProvider"
  assert url == "https://api.groq.com/openai/v1/chat/completions", url
  try:
    providers.prepare("custom/m", MESSAGE, setup)
  except providers.ProviderError:
    pass
  else:
    raise AssertionError("an unlisted provider needs api_base")

  seen = []

  def fetch(url: str, headers: dict) -> dict:
    seen.append(url)
    return {"data": [{"id": "model"}]}

  lines, skipped = discovery.build_catalog({"mistral": {"api_key": "k"}}, fetch)
  assert seen == ["https://api.mistral.ai/v1/models"], seen
  assert lines == ["mistral/model"] and skipped == [], (lines, skipped)
  print("ok: provider defaults")


if __name__ == "__main__":
  main()
