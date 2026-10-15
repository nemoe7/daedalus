"""JSON dump metadata keeps build time separate from export time."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from daedalus import cli, store
from daedalus.catalog import discovery


@pytest.mark.parametrize("present", [False, True])
def test_model_dump_records_export_and_build_times(tmp_path, monkeypatch, present):
  """JSON records the saved build time or null without claiming row fetch times."""
  monkeypatch.setattr(store, "MODELS_DB", tmp_path / "models.sqlite3")
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path / "dump")
  if present:
    store.write_store([{"id": "p/one"}])
  built = store.built()
  before = datetime.now(UTC)
  cli.dump_models("json")
  envelope = json.loads((tmp_path / "dump" / "models.json").read_text())
  expected = (
    datetime.fromtimestamp(built, UTC).isoformat().replace("+00:00", "Z")
    if built is not None
    else None
  )
  assert envelope["built_at"] == expected
  assert envelope["exported_at"].endswith("Z")
  assert before <= datetime.fromisoformat(envelope["exported_at"]) <= datetime.now(UTC)
  assert [row["id"] for row in envelope["data"]] == (["p/one"] if present else [])
  assert "fetched_at" not in envelope


def test_model_dump_reads_rows_and_build_time_from_one_snapshot(tmp_path, monkeypatch):
  """A concurrent rebuild cannot attach its new time to previously read model rows."""
  monkeypatch.setattr(store, "MODELS_DB", tmp_path / "models.sqlite3")
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path / "dump")
  store.write_store([{"id": "p/old"}])
  built = store.built()
  read_built = store.built
  with sqlite3.connect(store.MODELS_DB) as database:
    database.execute("PRAGMA journal_mode=WAL")

  def rebuild_during_read(database=None):
    store.write_store([{"id": "p/new"}])
    return read_built(database) if database is not None else read_built()

  monkeypatch.setattr(store, "built", rebuild_during_read)
  cli.dump_models("json")
  envelope = json.loads((tmp_path / "dump" / "models.json").read_text())
  assert [row["id"] for row in envelope["data"]] == ["p/old"]
  assert envelope["built_at"] == datetime.fromtimestamp(built, UTC).isoformat().replace(
    "+00:00", "Z"
  )
  assert [row["id"] for row in store.stored_rows()] == ["p/new"]
