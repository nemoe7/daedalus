"""Runnable check for the YAML config loader. Run: .venv/bin/python tests/test_config.py"""

import os
import sys
import tempfile
from pathlib import Path

from daedalus import config

SAMPLE = """\
cloudflare:
  api_key: os.environ/CLOUDFLARE_API_TOKEN
  api_base: os.environ/CLOUDFLARE_API_BASE
  discovery_url: https://api.cloudflare.com/client/v4/ai/models/search
  exclude:
    - "llama-guard*"
  tier:
    COMPLEX:
      - "*"
  models:
    "@cf/qwen/qwq-32b": { max_in_tok: 20000, max_out_tok: 4000 }

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
  assert cloudflare["tier"]["COMPLEX"] == ["*"], cloudflare["tier"]
  limits = cloudflare["models"]["@cf/qwen/qwq-32b"]
  assert limits == {"max_in_tok": 20000, "max_out_tok": 4000}, limits

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
    "zai",
  ], list(loaded)
  for name, provider in loaded.items():
    assert provider.get("discovery_url", "").startswith("https://"), name
    assert "api_key" in provider, name
  assert loaded["zai"]["exclude"] == ["*"], loaded["zai"]["exclude"]
  assert loaded["groq"]["rpm"] == 30, loaded["groq"]["rpm"]


def main() -> int:
  check_load()
  check_repo_file()
  check_missing_file()
  config.set_config(None)
  print("ok: config loader checks passed")
  return 0


if __name__ == "__main__":
  sys.exit(main())
