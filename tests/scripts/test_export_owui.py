"""Check source-only Open WebUI imports and their generated files."""

import ast
import json
from pathlib import Path

import pytest


def test_committed_imports_match_sources() -> None:
  import export_owui

  assert export_owui.main(["--check"]) == 0
  plugins = export_owui.ROOT / "integrations/openwebui"
  sources = [*plugins.glob("tools/*.py"), *plugins.glob("functions/*.py")]
  assert {path.stem for path in sources} == {
    "github",
    "google",
    "owui_manager",
    "chat_metadata",
    "served_model",
    "auto_reasoning_effort",
  }
  expected = set()
  for source in sources:
    target = plugins / "imports" / source.parent.name / source.with_suffix(".json").name
    expected.add(target)
    rows = json.loads(target.read_text(encoding="utf-8"))
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"id", "name", "content", "meta"}
    assert row["id"] == source.stem
    assert row["content"] == source.read_text(encoding="utf-8")
    assert isinstance(row["name"], str) and row["name"]
    assert set(row["meta"]) == {"description"}
    assert isinstance(row["meta"]["description"], str) and row["meta"]["description"]
    ast.parse(row["content"])
  assert set((plugins / "imports").rglob("*.json")) == expected


def test_generation_and_read_only_freshness_check(tmp_path: Path, monkeypatch) -> None:
  import export_owui

  monkeypatch.setattr(export_owui, "ROOT", tmp_path)
  source = tmp_path / "integrations/openwebui/tools/synthetic.py"
  source.parent.mkdir(parents=True)
  source.write_text(
    '"""title: Synthetic\ndescription: Unicode: café\nversion: 1.0.0"""\n'
    'raise RuntimeError("generation must not execute plugin code")\n',
    encoding="utf-8",
  )
  target = tmp_path / "integrations/openwebui/imports/tools/synthetic.json"
  assert export_owui.main(["--check"]) == 1
  assert not target.exists()
  assert export_owui.main([]) == 0
  assert export_owui.main(["--check"]) == 0
  first = target.read_bytes()
  assert b"\r\n" not in first
  row = json.loads(first)[0]
  assert row["id"] == "synthetic" and row["name"] == "Synthetic"
  assert row["meta"]["description"] == "Unicode: café"
  assert row["content"] == source.read_text(encoding="utf-8")
  assert export_owui.main([]) == 0
  assert target.read_bytes() == first
  source.write_text(
    source.read_text(encoding="utf-8") + "VALUE = 1\n", encoding="utf-8"
  )
  assert export_owui.main(["--check"]) == 1
  assert target.read_bytes() == first
  assert export_owui.main([]) == 0
  assert export_owui.main(["--check"]) == 0
  source.unlink()
  assert export_owui.main(["--check"]) == 1
  assert target.exists()


def test_import_requires_source_metadata(tmp_path: Path) -> None:
  import export_owui

  source = tmp_path / "missing.py"
  source.write_text('"""title: Missing description"""\n', encoding="utf-8")
  with pytest.raises(ValueError):
    export_owui.payload(source)
