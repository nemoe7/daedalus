"""Runnable check for the YAML config loader. Run: python tests/test_config.py"""

import os
import sys
import tempfile
from pathlib import Path

from daedalus import catalog, config, providers

SAMPLE = """\
cloudflare:
  api_key: os.environ/CLOUDFLARE_API_TOKEN
  api_base: os.environ/CLOUDFLARE_API_BASE
  discovery_url: https://api.cloudflare.com/client/v4/ai/models/search
  exclude:
    - "llama-guard*"
  tier:
    TIER-B:
      - "*"
  models:
    "@cf/qwen/qwq-32b": { max_input_tokens: 20000, max_output_tokens: 4000 }

gemini:
  api_key: os.environ/GEMINI_API_KEY
  discovery_url: https://generativelanguage.googleapis.com/v1beta/models
  exclude:
    - aqa
  models:
    gemini-3.5-flash: { rpm: 5, tpm: 250000, reasoning_effort: medium }
"""


def check_load() -> None:
  os.environ["CLOUDFLARE_API_TOKEN"] = "cf-token"
  os.environ.pop("CLOUDFLARE_API_BASE", None)
  with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "config.yml"
    path.write_text(SAMPLE, encoding="utf-8")
    loaded = config.load_config(path)

  assert sorted(loaded) == ["cloudflare", "gemini"], loaded
  assert list(loaded) == sorted(loaded), "top-level keys are not alphabetical"

  cloudflare = loaded["cloudflare"]
  assert cloudflare["api_key"] == "cf-token", cloudflare["api_key"]
  assert cloudflare["api_base"] == "", repr(cloudflare["api_base"])
  assert cloudflare["discovery_url"].startswith("https://"), cloudflare
  assert cloudflare["exclude"] == ["llama-guard*"], cloudflare["exclude"]
  assert cloudflare["tier"]["TIER-B"] == ["*"], cloudflare["tier"]
  limits = cloudflare["models"]["@cf/qwen/qwq-32b"]
  assert limits == {"max_input_tokens": 20000, "max_output_tokens": 4000}, limits

  gemini_model = loaded["gemini"]["models"]["gemini-3.5-flash"]
  assert gemini_model["reasoning_effort"] == "medium", gemini_model
  assert gemini_model["tpm"] == 250000, gemini_model

  assert config.get_config() is loaded, "get_config reloaded the file"


def check_missing_file() -> None:
  config.set_config(None)
  try:
    config.load_config(Path("no-such-config.yml"))
  except FileNotFoundError:
    pass
  else:
    raise AssertionError("a missing file must raise FileNotFoundError")


def check_repo_file() -> None:
  """The committed provider file parses, and its top-level keys are alphabetical."""
  loaded = config.load_config(Path("config/providers/free.yml"))
  assert sorted(loaded) == list(loaded), list(loaded)
  assert list(loaded) == [
    "cloudflare",
    "gemini",
    "groq",
    "kilo",
    "mistral",
    "openrouter",
    "z-ai",
  ], list(loaded)
  for name, provider in loaded.items():
    merged = providers.settings(name, provider)
    assert merged.get("discovery_url", "").startswith("https://"), name
    assert "api_key" in provider, name
  assert loaded["z-ai"]["exclude"] == ["*"], loaded["z-ai"]["exclude"]
  assert loaded["groq"]["rpm"] == 30, loaded["groq"]["rpm"]
  author_prefixed = [
    "@cf/meta/llama-guard-3-8b",
    "@cf/meta/llama-3.2-11b-vision-instruct",
    "@cf/zai-org/glm-5.2",
    "@cf/zai-org/glm-5.3-flash",
  ]
  # Cloudflare marks these `require_workers_paid` in its model search API.
  paid = [
    "@cf/deepseek-ai/deepseek-v4-flash-0731",
    "@cf/deepseek-ai/deepseek-v4-pro-0813",
    "@cf/moonshotai/kimi-k2.6",
    "@cf/moonshotai/kimi-k2.7-code",
    "@cf/zai-org/glm-5.2",
    "@cf/zai-org/glm-5.3",
    "@cf/zai-org/glm-5.3-flash",
  ]
  free = ["@cf/openai/gpt-oss-120b", "@cf/zai-org/glm-4.7-flash"]
  kept = catalog.select(loaded["cloudflare"], [*author_prefixed, *paid, *free])
  assert set(free) <= set(kept), kept
  assert not set(author_prefixed + paid) & set(kept), kept


def walk(node: object) -> list[str]:
  """Every string in the tree."""
  if isinstance(node, str):
    return [node]
  if isinstance(node, list):
    return [text for item in node for text in walk(item)]
  if isinstance(node, dict):
    return [text for value in node.values() for text in walk(value)]
  return []


def check_url_substitution() -> None:
  """A URL carries `os.environ/NAME` inside it, not as a whole value."""
  os.environ["CLOUDFLARE_ACCOUNT_ID"] = "acct-123"
  loaded = config.load_config(Path("config/providers/free.yml"))
  url = providers.settings("cloudflare", loaded["cloudflare"])["discovery_url"]
  assert url == (
    "https://api.cloudflare.com/client/v4/accounts/acct-123"
    "/ai/models/search?per_page=100&task=Text%20Generation"
  ), url
  left = [text for text in walk(loaded) if "os.environ/" in text]
  assert left == [], left


def main() -> int:
  check_load()
  check_url_substitution()
  check_repo_file()
  check_missing_file()
  config.set_config(None)
  print("ok: config loader checks passed")
  return 0


if __name__ == "__main__":
  sys.exit(main())
