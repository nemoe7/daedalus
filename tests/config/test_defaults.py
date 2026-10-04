"""The default provider file of the repository fills only the names that the local file lacks."""

from pathlib import Path

import httpx
import pytest
import yaml

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
  monkeypatch.setattr(
    defaults, "fetch", lambda *a, **k: "kilo:\n  api_base: https://kilo.test\n"
  )
  assert defaults.refresh() == {"kilo": {"api_base": "https://kilo.test"}}
  assert defaults.cached() == {"kilo": {"api_base": "https://kilo.test"}}, (
    "the cache holds it"
  )

  def broken(*_args, **_kwargs):
    raise httpx.ConnectError("no network")

  monkeypatch.setattr(defaults, "fetch", broken)
  assert defaults.refresh() == {"kilo": {"api_base": "https://kilo.test"}}, (
    "the cache stands"
  )
  assert defaults.cached() == {"kilo": {"api_base": "https://kilo.test"}}


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


def test_the_catalog_rebuild_fills_the_live_config(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """A rebuild fetches the defaults and adds the absent provider names to the live config."""
  seen: dict = {}
  monkeypatch.setattr(
    defaults,
    "refresh",
    lambda: {"kilo": {"api_base": "https://kilo.test"}},
  )

  def build(config, **_kwargs):
    seen.update(config)
    return [], []

  from daedalus import config as live

  monkeypatch.setattr(live, "get_config", lambda: {"cloudflare": {"api_key": "mine"}})
  monkeypatch.setattr("daedalus.catalog.get_config", live.get_config)
  monkeypatch.setattr("daedalus.catalog.build_rows", build)
  monkeypatch.setattr("daedalus.catalog.enrich", lambda *a, **k: ([], []))
  monkeypatch.setattr(
    "daedalus.catalog.write_store", lambda *a, **k: Path("store.sqlite3")
  )
  from daedalus.catalog import _rebuild

  _rebuild(cached=True)
  assert "kilo" in seen and seen["cloudflare"] == {"api_key": "mine"}, seen


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
