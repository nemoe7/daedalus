"""The default provider file of the repository fills only the names that the local file lacks."""

import logging
from pathlib import Path

import httpx
import pytest
import yaml

from daedalus import config
from daedalus.config import defaults


def test_fill_keeps_the_local_block() -> None:
  """A local name wins whole, and an absent name lands."""
  loaded = {"cloudflare": {"api_key": "mine", "tier": {"TIER-A": ["a"]}}}
  found = defaults.fill(
    loaded,
    {"cloudflare": {"api_key": "theirs"}, "kilo": {"api_base": "https://kilo.test"}},
  )
  assert found["cloudflare"] == {"api_key": "mine", "tier": {"TIER-A": ["a"]}}, (
    "the local name wins"
  )
  assert found["kilo"] == {"api_base": "https://kilo.test"}, "an absent name lands"
  assert found is loaded, "the fill works in place"


def test_parse_resolves_values() -> None:
  """The fetched text takes the same `env:` resolution as a local file."""
  found = defaults.parse('cloudflare:\n  api_key: "env:FAKE_API_KEY"\n  tier: {}\n')
  assert found["cloudflare"]["api_key"] != "env:FAKE_API_KEY", "the value resolves"


def test_refresh_writes_the_cache(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A good fetch lands in the cache, and the cache carries it on a bad one."""
  monkeypatch.setattr(defaults, "PATH", tmp_path / "free.defaults.yml")
  monkeypatch.setenv("CLOUD_TEST_KEY", "secret")
  monkeypatch.setattr(
    defaults,
    "fetch",
    lambda *a, **k: (
      "kilo:\n  api_key: env:CLOUD_TEST_KEY\n  api_base: https://kilo.test\n"
    ),
  )
  resolved = {"kilo": {"api_key": "secret", "api_base": "https://kilo.test"}}
  assert defaults.refresh() == resolved
  assert defaults.cached() == resolved, "the cache resolves its tokens for the runtime"
  assert defaults.cached_raw() == {
    "kilo": {"api_key": "env:CLOUD_TEST_KEY", "api_base": "https://kilo.test"}
  }, "the cache keeps editable value tokens"

  def broken(*_args, **_kwargs):
    raise httpx.ConnectError("no network")

  monkeypatch.setattr(defaults, "fetch", broken)
  assert defaults.refresh() == resolved, "the cache stands"
  assert defaults.cached() == resolved


def test_refresh_leaves_the_local_file(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The fetch never writes the local provider file of the operator."""
  local = tmp_path / "free.yml"
  text = "# mine\nkilo:\n  api_base: https://mine.test\n"
  local.write_text(text, encoding="utf-8")
  monkeypatch.setattr(defaults, "PATH", tmp_path / "free.defaults.yml")
  monkeypatch.setattr(
    defaults, "fetch", lambda *a, **k: 'kilo:\n  api_base: "danger"\n'
  )
  defaults.refresh()
  assert local.read_text(encoding="utf-8") == text, "the local file keeps its bytes"


def test_refresh_rejects_other_content(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A file that holds no provider block keeps the cache."""
  monkeypatch.setattr(defaults, "PATH", tmp_path / "free.defaults.yml")
  monkeypatch.setattr(defaults, "fetch", lambda *a, **k: "- a\n- b\n")
  assert defaults.refresh() == {}, "no block, no cache"
  assert not (tmp_path / "free.defaults.yml").exists(), "nothing writes"


def test_the_catalog_rebuild_refreshes_default_blocks(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A rebuild replaces an old cloud block and keeps a local provider block."""
  local = tmp_path / "free.yml"
  local.write_text("cloudflare:\n  api_key: mine\n", encoding="utf-8")
  monkeypatch.setattr(defaults, "PATH", tmp_path / "free.defaults.yml")
  defaults.PATH.write_text("kilo:\n  api_base: https://old.test\n", encoding="utf-8")
  monkeypatch.setattr(
    defaults, "fetch", lambda: "kilo:\n  api_base: https://new.test\n"
  )
  seen: dict = {}

  def build(config, **_kwargs):
    seen.update(config)
    return [], []

  from daedalus import config as live

  live.load_config(local)
  monkeypatch.setattr("daedalus.catalog.build_rows", build)
  monkeypatch.setattr("daedalus.catalog.enrich", lambda *a, **k: ([], []))
  monkeypatch.setattr(
    "daedalus.catalog.write_store", lambda *a, **k: Path("store.sqlite3")
  )
  from daedalus.catalog import _rebuild

  try:
    _rebuild(cached=True)
  finally:
    live.set_config(None)
  assert seen == {
    "cloudflare": {"api_key": "mine"},
    "kilo": {"api_base": "https://new.test"},
  }, seen


def test_the_cache_file_holds_yaml(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The cache is a plain YAML file, so an operator can read it."""
  monkeypatch.setattr(defaults, "PATH", tmp_path / "free.defaults.yml")
  monkeypatch.setattr(
    defaults, "fetch", lambda *a, **k: "kilo:\n  api_base: https://kilo.test\n"
  )
  defaults.refresh()
  assert yaml.safe_load(
    (tmp_path / "free.defaults.yml").read_text(encoding="utf-8")
  ) == {"kilo": {"api_base": "https://kilo.test"}}


@pytest.mark.parametrize(
  "body",
  [b"groq: [private-cache-marker", b"private-cache-marker: \xff"],
  ids=["yaml", "utf8"],
)
@pytest.mark.parametrize("reader", ["cached_raw", "cached", "refresh", "load_config"])
def test_unreadable_cache(
  tmp_path: Path, monkeypatch, caplog, body: bytes, reader: str
) -> None:
  cache = tmp_path / "cache" / "free.defaults.yml"
  cache.parent.mkdir()
  cache.write_bytes(body)
  monkeypatch.setattr(defaults, "PATH", cache)
  local = tmp_path / "providers" / "free.yml"
  local.parent.mkdir()
  local.write_text("example:\n  api_key: local-placeholder\n", encoding="utf-8")
  calls = []

  def unavailable():
    calls.append(True)
    raise httpx.ConnectError("simulated outage")

  monkeypatch.setattr(defaults, "fetch", unavailable)
  try:
    found = (
      config.load_config(local)
      if reader == "load_config"
      else getattr(defaults, reader)()
    )
    expected = (
      {"example": {"api_key": "local-placeholder"}} if reader == "load_config" else {}
    )
    assert found == expected
  finally:
    config.set_config(None)
  assert cache.read_bytes() == body
  assert local.read_text(encoding="utf-8") == "example:\n  api_key: local-placeholder\n"
  assert len(calls) == (1 if reader == "refresh" else 0)
  warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
  assert len(warnings) == (2 if reader == "refresh" else 1)
  assert "private-cache-marker" not in caplog.text


@pytest.mark.parametrize("body", [b"example: [", b"\xff"], ids=["yaml", "utf8"])
def test_unreadable_local_config_stays_invalid(tmp_path: Path, body: bytes) -> None:
  local = tmp_path / "free.yml"
  local.write_bytes(body)
  try:
    with pytest.raises((yaml.YAMLError, UnicodeError)):
      config.load_config(local)
  finally:
    config.set_config(None)
  assert local.read_bytes() == body
