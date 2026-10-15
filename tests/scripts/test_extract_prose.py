"""Check the shared prose inventory with synthetic source files."""

import json
from pathlib import Path


def test_collector_keeps_prose_anchors_and_manual_review_gaps(
  tmp_path, monkeypatch, capsys
):
  import extract_prose

  source = tmp_path / "sample.py"
  source.write_text(
    '# A comment.\n"""A module description."""\nmessage = f"Hello {name}."\n',
    encoding="utf-8",
  )
  guide = tmp_path / "guide.md"
  guide.write_text("# A guide\n\nSave the file.\n", encoding="utf-8")
  unknown = tmp_path / "setup.yml"
  unknown.write_text("name: A sample\n", encoding="utf-8")
  broken = tmp_path / "broken.py"
  broken.write_text("def broken(\n", encoding="utf-8")
  paths = [Path(path.name) for path in (source, guide, unknown, broken)]
  monkeypatch.setattr(extract_prose, "ROOT", tmp_path)
  monkeypatch.setattr(extract_prose, "tracked_files", lambda excludes: paths)
  monkeypatch.setattr(extract_prose.sys, "argv", ["extract_prose.py"])
  assert extract_prose.main() == 2
  output = json.loads(capsys.readouterr().out)
  assert output["manual_files"] == 3
  records = output["files"]
  assert {record["path"] for record in records} == {
    str(tmp_path / path) for path in paths
  }
  assert {(part["line"], part["kind"]) for part in records[0]["parts"]} == {
    (1, "comment"),
    (2, "docstring"),
    (3, "candidate"),
  }
  assert records[0]["parts"][2]["text"] == "Hello {value}."
  assert records[1]["parts"] == [
    {"line": 1, "kind": "markdown", "text": guide.read_text()}
  ]
  assert records[1]["manual"] == []
  assert records[2]["parts"] == records[3]["parts"] == []
  assert records[2]["manual"] and records[3]["manual"]
  monkeypatch.setattr(
    extract_prose, "tracked_files", lambda excludes: [Path(guide.name)]
  )
  assert extract_prose.main() == 0
  assert json.loads(capsys.readouterr().out)["manual_files"] == 0


def test_scanner_consumes_the_shared_inventory(tmp_path, monkeypatch):
  import lint_prose

  source = tmp_path / "synthetic.py"
  calls = []
  manual = ["Synthetic review boundary."]

  def collect(paths):
    calls.append(paths)
    return [
      {
        "path": str(source),
        "parts": [{"line": 17, "kind": "candidate", "text": "The file is saved."}],
        "manual": manual,
      }
    ]

  monkeypatch.setattr(lint_prose.extract_prose, "collect", collect)
  result = lint_prose.scan_file(source)
  assert calls == [[source]]
  assert result["manual"] == manual
  assert result["findings"]
  assert all(
    finding["line"] == 17 and finding["kind"] == "candidate"
    for finding in result["findings"]
  )
