"""Tests for the lobe-icons coverage report of a dump."""

from __future__ import annotations

import json
from pathlib import Path

import check_icons


def svg(folder: Path, name: str) -> None:
  """Write one empty SVG file into a folder."""
  folder.mkdir(parents=True, exist_ok=True)
  (folder / f"{name}.svg").write_text("<svg/>", encoding="utf-8")


def test_icon_names_splits_provider_and_dev():
  """A three-part id yields provider and developer slugs."""
  row = {"id": "openrouter/mistralai/mistral-large"}
  assert check_icons.icon_names(row) == ["openrouter", "mistralai"]


def test_icon_names_drops_dev_equal_to_provider():
  """A developer equal to the provider yields the provider slug only."""
  row = {"id": "groq/groq/llama-3"}
  assert check_icons.icon_names(row) == ["groq"]


def test_icon_names_keeps_scope_prefix():
  """A scoped developer slug stays as the last middle segment."""
  row = {"id": "cloudflare/@cf/baai/bge-base"}
  assert check_icons.icon_names(row) == ["cloudflare", "baai"]


def test_icon_names_two_part_id_has_no_dev():
  """A two-part id yields the provider slug only."""
  assert check_icons.icon_names({"id": "openai/gpt-4"}) == ["openai"]


def test_icon_names_single_part_id_is_empty():
  """A one-part id yields no icon names."""
  assert check_icons.icon_names({"id": "gpt-4"}) == []


def test_icon_names_novita_ling_uses_inclusionai():
  """A novita ling row adds the inclusionai developer icon."""
  assert check_icons.icon_names({"id": "novita/ling-3.1-flash"}) == [
    "novita",
    "inclusionai",
  ]
  assert check_icons.icon_names({"id": "novita/other-model"}) == ["novita"]


def test_covered_keeps_names_with_file(tmp_path):
  """A name with an SVG file counts as covered."""
  svg(tmp_path, "openai")
  assert check_icons.covered({"openai", "acme"}, tmp_path) == {"openai"}


def test_origin_of_direct_match():
  """A name present in the lobe set resolves to itself."""
  assert check_icons.origin_of("anthropic", {"anthropic", "mistral"}) == "anthropic"


def test_origin_of_through_rename_map():
  """A renamed target resolves to its lobe origin."""
  assert check_icons.origin_of("mistralai", {"mistral"}) == "mistral"


def test_origin_of_without_match_is_none():
  """A name absent from the lobe set resolves to None."""
  assert check_icons.origin_of("acme", {"mistral"}) is None


def test_report_lists_uncovered_with_lobe_origin(tmp_path):
  """Rows without a file report count and lobe origin, sorted by name."""
  icons = tmp_path / "icons"
  svg(icons, "openai")
  lobe = tmp_path / "lobe"
  svg(lobe, "mistral")
  rows = [
    {"id": "openai/gpt-4"},
    {"id": "openrouter/mistralai/mistral-large"},
    {"id": "openrouter/mistralai/mistral-small"},
    {"id": "acme/secret/model"},
  ]
  found = check_icons.report(rows, icons, lobe)
  assert found == [
    ("acme", 1, None),
    ("mistralai", 2, "mistral"),
    ("openrouter", 2, None),
    ("secret", 1, None),
  ]


def test_main_json_output(tmp_path, capsys):
  """The json format prints one object per uncovered name."""
  icons = tmp_path / "icons"
  icons.mkdir()
  lobe = tmp_path / "lobe"
  svg(lobe, "mistral")
  dump = tmp_path / "models.json"
  dump.write_text(
    json.dumps({"data": [{"id": "openrouter/mistralai/mistral-large"}]}),
    encoding="utf-8",
  )
  code = check_icons.main(
    [str(dump), "--icons", str(icons), "--lobe", str(lobe), "--format", "json"]
  )
  assert code == 0
  printed = json.loads(capsys.readouterr().out)
  assert printed == [
    {"name": "mistralai", "rows": 1, "lobe": "mistral"},
    {"name": "openrouter", "rows": 1, "lobe": None},
  ]
