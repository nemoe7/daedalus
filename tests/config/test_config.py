"""Tests for the YAML config loader."""

import logging
import os
import tempfile
from pathlib import Path

import pytest

from daedalus import config, providers
from daedalus.config import defaults
from daedalus.routing import router

SAMPLE = """\
cloudflare:
  api_key: env:CLOUDFLARE_API_KEY
  api_base: env:CLOUDFLARE_API_BASE
  discovery_url: https://api.cloudflare.com/client/v4/ai/models/search
  exclude:
    - "llama-guard*"
  tier:
    TIER-B:
      - "*"
  models:
    "@cf/qwen/qwq-32b": { max_input_tokens: 20000, max_output_tokens: 4000 }

gemini:
  api_key: env:GEMINI_API_KEY
  discovery_url: https://generativelanguage.googleapis.com/v1beta/models
  exclude:
    - aqa
  models:
    gemini-3.5-flash: { rpm: 5, tpm: 250000, reasoning_effort: medium }
"""


def test_load() -> None:
  os.environ["CLOUDFLARE_API_KEY"] = "cf-token"
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


def test_legacy_token_is_not_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("LEGACY_API_KEY", "environment")
  monkeypatch.setitem(config.SAVED, "LEGACY_API_KEY", "saved")
  token = "os.environ/LEGACY_API_KEY"
  assert config.expand(token) == token


def test_missing_file() -> None:
  config.set_config(None)
  try:
    config.load_config(Path("no-such-config.yml"))
  except FileNotFoundError:
    pass
  else:
    raise AssertionError("a missing file must raise FileNotFoundError")


def test_missing_main_uses_cached_defaults(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(defaults, "PATH", tmp_path / "free.defaults.yml")
  defaults.PATH.write_text("p:\n  exclude: [gpt3]\n", encoding="utf-8")

  loaded = config.load_config()

  assert loaded == {"p": {"exclude": ["gpt3"]}}
  assert config.get_config() is loaded


def test_missing_main_keeps_defaults_beside_a_provider_file(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A provider file stays separate from cached and refreshed main defaults."""
  monkeypatch.chdir(tmp_path)
  folder = tmp_path / "config" / "providers"
  folder.mkdir(parents=True)
  (folder / "p.yml").write_text(
    "api_key: file\nmodels:\n  special: {pool: false}\n", encoding="utf-8"
  )
  monkeypatch.setattr(defaults, "PATH", tmp_path / "free.defaults.yml")
  defaults.PATH.write_text("p:\n  exclude: [gpt3]\n", encoding="utf-8")

  loaded = config.load_config()

  assert config.main_block(loaded["p"]) == {"exclude": ["gpt3"]}
  assert config.file_block(loaded["p"]) == {
    "api_key": "file",
    "models": {"special": {"pool": False}},
  }
  refreshed = config.sync_defaults({"p": {"exclude": ["gpt4"]}})
  assert config.main_block(refreshed["p"]) == {"exclude": ["gpt4"]}
  assert config.file_block(refreshed["p"]) == config.file_block(loaded["p"])


def test_repo_file() -> None:
  """The shipped provider files load without a parse error."""
  config.load_config(Path("config/providers/free.yml"))


def walk(node: object) -> list[str]:
  """Every string in the tree."""
  if isinstance(node, str):
    return [node]
  if isinstance(node, list):
    return [text for item in node for text in walk(item)]
  if isinstance(node, dict):
    return [text for value in node.values() for text in walk(value)]
  return []


def test_url_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  """A URL resolves an environment token inside its path."""
  monkeypatch.setenv("TEST_ACCOUNT_ID", "acct-123")
  path = tmp_path / "config.yml"
  path.write_text(
    "p:\n  api_base: https://provider.test/accounts/env:TEST_ACCOUNT_ID/v1\n"
  )
  loaded = config.load_config(path)
  assert providers.settings("p", loaded["p"])["api_base"] == (
    "https://provider.test/accounts/acct-123/v1"
  )
  assert not [text for text in walk(loaded) if "env:" in text or "db:" in text]


def test_provider_file() -> None:
  """A {provider}.yml file stays separate from its provider block in the main file."""
  with tempfile.TemporaryDirectory() as folder:
    main = Path(folder) / "free.yml"
    main.write_text(
      "p:\n  api_key: main\n  tier:\n    TIER-B: ['*']\n  models:\n    '*': {rpm: 1}\n",
      encoding="utf-8",
    )
    separate = Path(folder) / "p.yml"
    separate.write_text(
      "api_key: file\ntier:\n  TIER-A: ['special']\nmodels:\n  special: {pool: false}\n",
      encoding="utf-8",
    )
    (Path(folder) / "daedalus.yml").write_text("weights: {enabled: true}\n")
    loaded = config.load_config(main)
    assert config.provider_files(main) == [separate], config.provider_files(main)
    assert loaded["p"]["api_key"] == "main"
    assert loaded["p"][config.FILE_KEY]["api_key"] == "file"
    assert config.block_for(loaded, "p", "special")["api_key"] == "file"
    assert config.block_for(loaded, "p", "regular")["api_key"] == "main"
    assert router.candidates(loaded, "TIER-A", ["p/special"]) == [], "pool false"
    assert router.candidates(loaded, "TIER-B", ["p/special", "p/regular"]) == [
      "p/regular"
    ], "the main tier does not apply to the file model"
    assert config.file_shadows(main) == {"p.yml": "free.yml"}, (
      "the file that no main block shadows stays out"
    )
    separate.unlink()
    assert config.get_config()["p"][config.FILE_KEY]["api_key"] == "file"
    assert config.load_config(main)["p"].get(config.FILE_KEY) is None, "reload drops it"
    assert config.file_shadows(main) == {}


def test_file_shadows_names_only_a_shared_block() -> None:
  """A provider file with a block of its own is not shadowed, and the load logs each shadowed file."""
  with tempfile.TemporaryDirectory() as folder:
    main = Path(folder) / "free.yml"
    main.write_text("p:\n  api_key: main\n", encoding="utf-8")
    (Path(folder) / "p.yml").write_text(
      "api_key: file\nmodels:\n  m: {}\n", encoding="utf-8"
    )
    (Path(folder) / "q.yml").write_text("api_key: q\n", encoding="utf-8")
    assert config.file_shadows(main) == {"p.yml": "free.yml"}
    records: list[logging.LogRecord] = []

    class Keep(logging.Handler):
      def emit(self, record: logging.LogRecord) -> None:
        records.append(record)

    logger = logging.getLogger("daedalus.config")
    handler = Keep()
    logger.addHandler(handler)
    try:
      config.load_config(main)
    finally:
      logger.removeHandler(handler)
    messages = [record.getMessage() for record in records]
    assert any("p.yml" in text and "free.yml" in text for text in messages), messages
    assert not any("q.yml" in text for text in messages), messages


def _log_capture():
  """A logger handler that keeps the records of the daedalus.config logger."""
  records: list[logging.LogRecord] = []

  class Keep(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
      records.append(record)

  handler = Keep()
  logging.getLogger("daedalus.config").addHandler(handler)
  return records, handler


def test_a_wrong_shape_in_the_main_file_drops_out() -> None:
  """The loader omits invalid provider values, and the router uses the remaining block."""
  with tempfile.TemporaryDirectory() as folder:
    main = Path(folder) / "free.yml"
    main.write_text(
      "p:\n  api_key: k\n  tier: TIER-B\n  models: nope\n  hooks: 7\n"
      "  discovery_match: nope\n",
      encoding="utf-8",
    )
    records, handler = _log_capture()
    try:
      loaded = config.load_config(main)
    finally:
      logging.getLogger("daedalus.config").removeHandler(handler)
  messages = [record.getMessage() for record in records]
  assert loaded["p"] == {"api_key": "k"}, loaded["p"]
  assert any("free.yml" in text and "tier" in text for text in messages), messages
  assert any("models" in text for text in messages), messages
  assert any("hooks" in text for text in messages), messages
  assert router.pooled(loaded, "p/a"), "the router reads the rest of the block"


def test_a_wrong_shape_in_a_tier_drops_out() -> None:
  """A tier value that is not a list leaves the tier map, and the other tiers stay."""
  with tempfile.TemporaryDirectory() as folder:
    main = Path(folder) / "free.yml"
    main.write_text(
      "p:\n  api_key: k\n  tier:\n    TIER-A: TIER-B\n    TIER-B: [a]\n",
      encoding="utf-8",
    )
    records, handler = _log_capture()
    try:
      loaded = config.load_config(main)
    finally:
      logging.getLogger("daedalus.config").removeHandler(handler)
  assert loaded["p"]["tier"] == {"TIER-B": ["a"]}, loaded["p"]["tier"]
  assert any("TIER-A" in record.getMessage() for record in records), records


def test_a_wrong_shape_in_a_provider_file_drops_out() -> None:
  """The file block of a `{provider}.yml` file keeps its good keys."""
  with tempfile.TemporaryDirectory() as folder:
    main = Path(folder) / "free.yml"
    main.write_text("p:\n  api_key: main\n", encoding="utf-8")
    (Path(folder) / "p.yml").write_text(
      "api_key: file\nmodels: nope\n", encoding="utf-8"
    )
    records, handler = _log_capture()
    try:
      loaded = config.load_config(main)
    finally:
      logging.getLogger("daedalus.config").removeHandler(handler)
  assert loaded["p"]["_file"] == {"api_key": "file"}, loaded["p"]["_file"]
  assert any("p.yml" in record.getMessage() for record in records), records
