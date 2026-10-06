import json
import os

from daedalus import config, providers
from daedalus.catalog import discovery
from daedalus.providers import base

FIELDS = ("api_base", "api_type", "discovery_url")
MESSAGE = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def test_provider_defaults() -> None:
  os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
  os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-account"
  loaded = config.load_config()
  assert set(providers.PROVIDERS) == set(loaded), loaded.keys()
  name = "openrouter"
  main = loaded[name]
  file = main[config.FILE_KEY]
  assert main["api_key"] == file["api_key"], "the same env token in both files"
  main["api_base"] = "https://main.test/v1"
  file["api_base"] = "https://file.test/v1"
  provider, slug = providers.provider_for("openrouter/z-ai/glm-5.3-flash", loaded)
  assert provider.base == "https://file.test/v1", "the file owns it"
  assert slug == "z-ai/glm-5.3-flash"
  provider, _ = providers.provider_for("openrouter/other/model:free", loaded)
  assert provider.base == "https://main.test/v1", "the main file owns it"
  del main["api_base"], file["api_base"]
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
  try:
    cloudflare = providers.settings("cloudflare", {})
    base = "https://api.cloudflare.com/client/v4/accounts/account/ai/v1"
    assert cloudflare["api_base"] == base, cloudflare
    gateway = providers.settings("cloudflare", {"api_base": "https://gateway.test/v1"})
    assert gateway["api_base"] == "https://gateway.test/v1", "the yml wins"
    assert "/accounts/account/" in cloudflare["discovery_url"], cloudflare
  finally:
    os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-account"
  assert "task=" not in cloudflare["discovery_url"], "the provider class filters tasks"

  saved = config.SAVED.pop("CLOUDFLARE_ACCOUNT_ID", None)
  os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
  try:
    plain = providers.settings("cloudflare", {"api_key": "k"})
    assert "api_base" not in plain and "discovery_url" not in plain, plain
    assert providers.CloudflareProvider.defaults["api_base"].startswith(
      "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
    ), "the card placeholder holds the template"
  finally:
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-account"
    if saved is not None:
      config.SAVED["CLOUDFLARE_ACCOUNT_ID"] = saved

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

  os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)

  seen = []

  def fetch(url: str, headers: dict) -> dict:
    seen.append(url)
    return {"data": [{"id": "model"}]}

  lines, skipped = discovery.build_rows({"mistral": {"api_key": "k"}}, fetch)
  assert seen == ["https://api.mistral.ai/v1/models"], seen
  assert list(lines) == ["mistral/model"] and skipped == [], (lines, skipped)


# Mistral gets the reasoning_content of an old assistant message back as a thinking chunk.
THOUGHT = {
  "type": "thinking",
  "thinking": [{"type": "text", "text": "secret thoughts"}],
}


def test_mistral_fields() -> None:
  call = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
  history = [
    {"role": "system", "content": "s", "name": "x"},
    {"role": "user", "content": "hi"},
    {
      "role": "assistant",
      "content": "",
      "reasoning_content": "secret thoughts",
      "reasoning": "more",
      "tool_calls": [call],
    },
    {"role": "tool", "content": "42", "tool_call_id": "c1", "name": "f", "extra": 1},
  ]
  body = {"model": "m", "messages": history}
  _, _, sent, _ = providers.prepare("mistral/m", body, {"mistral": {"api_key": "k"}})
  assert sent["messages"] == [
    {"role": "system", "content": "s"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": [THOUGHT], "tool_calls": [call]},
    {"role": "tool", "content": "42", "tool_call_id": "c1", "name": "f"},
  ], sent["messages"]
  assert history[2]["reasoning_content"] == "secret thoughts", "the client body stays"
  _, _, sent, _ = providers.prepare("groq/m", body, {"groq": {"api_key": "k"}})
  assert sent["messages"][2] == {
    "role": "assistant",
    "content": "",
    "tool_calls": [call],
  }
  assert sent["messages"][0] == {"role": "system", "content": "s", "name": "x"}, "name"
  keys = {"openrouter": {"api_key": "k"}}
  _, _, sent, _ = providers.prepare("openrouter/m", body, keys)
  assert sent["messages"] == history, "OpenRouter keeps all fields"


def test_hidden_inputs() -> None:
  detail = {
    "type": "extra_forbidden",
    "loc": ["body"],
    "msg": "Extra",
    "input": "PROMPT",
  }
  raw = json.dumps({"detail": [detail, {**detail, "input": {"deep": "PROMPT"}}]})
  text = base.error_text(raw.encode())
  assert "PROMPT" not in text and "extra_forbidden" in text, text
  shown = base.error_detail(raw.encode(), 4000)
  assert "PROMPT" not in shown and '"msg": "Extra"' in shown, shown
  assert base.error_detail(b"plain text", 5) == "plain", "not JSON: cut only"
