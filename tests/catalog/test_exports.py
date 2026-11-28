"""Exports return files and report failed providers instead of refusing them."""

import logging
import time

from daedalus import catalog
from daedalus.catalog import exports


def test_collect_returns_files_and_warns_on_failed_providers(
  tmp_path, monkeypatch, caplog
):
  """Failed providers warn but still return every other fresh file."""
  caplog.set_level(logging.WARNING, logger="daedalus")
  dump_dir = tmp_path / "dump"
  dump_dir.mkdir()
  (dump_dir / "p.json").write_text("{}", encoding="utf-8")
  (dump_dir / "models.json").write_text("{}", encoding="utf-8")

  monkeypatch.setattr(catalog, "LAST_FAILED", ["requesty"])
  monkeypatch.setattr(exports.discovery, "dump", lambda **_: [dump_dir / "p.json"])
  monkeypatch.setattr(exports.enrichment, "dump", lambda **_: [])
  monkeypatch.setattr(exports, "dump_models", lambda *_: dump_dir / "models.json")

  files = exports.collect("all", "json", time.time())

  assert set(files) == {"p.json", "models.json"}
  messages = [record.getMessage() for record in caplog.records]
  assert "export includes stale rows from failed providers: requesty" in messages


def test_collect_warns_only_when_providers_failed(tmp_path, monkeypatch, caplog):
  """No failed providers means no stale-rows warning."""
  caplog.set_level(logging.WARNING, logger="daedalus")
  dump_dir = tmp_path / "dump"
  dump_dir.mkdir()
  (dump_dir / "models.json").write_text("{}", encoding="utf-8")

  monkeypatch.setattr(catalog, "LAST_FAILED", [])
  monkeypatch.setattr(exports.discovery, "dump", lambda **_: [])
  monkeypatch.setattr(exports.enrichment, "dump", lambda **_: [])
  monkeypatch.setattr(exports, "dump_models", lambda *_: dump_dir / "models.json")

  files = exports.collect("models", "json", time.time())

  assert set(files) == {"models.json"}
  assert "failed providers" not in caplog.text
