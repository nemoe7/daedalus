"""The mermaid blocks of the docs: the default look, and no theme of our own."""

import re
from pathlib import Path

# A theme line, a node class, or a per-node style: each one overrides the default look.
CUSTOM = re.compile(
  r"^\s*(%%\{init:|classDef\b|class\s+[\w,\s-]+\s+\w+\s*$|style\s+\S+\s+fill:)",
  re.MULTILINE,
)
# The chains run LR, so a page fills the width of a desktop. A fan-out or a loop keeps TD.
DIRECTION = re.compile(r"flowchart (LR|TD)")
BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)


def pages() -> list[Path]:
  """Every markdown page under `docs`, with the README."""
  return [*sorted(Path("docs").rglob("*.md")), Path("README.md")]


def test_docs_carry_no_custom_mermaid_theme() -> None:
  """Every mermaid diagram uses the default theme of the renderer."""
  found: list[str] = []
  for page in pages():
    for block in BLOCK.findall(page.read_text(encoding="utf-8")):
      found += [f"{page}: {line.strip()}" for line in CUSTOM.findall(block)]
  assert found == [], "custom mermaid themes: " + "; ".join(found)


def test_the_check_finds_a_theme() -> None:
  """A theme line, a class and a node style each count, so the check holds."""
  sample = '%%{init: {"theme": "base"}}%%\nclassDef bad fill:#f00\nclass A bad\nstyle B fill:#0f0\n'
  assert len(CUSTOM.findall(sample)) == 4


def test_mermaid_blocks_stay_in_the_docs() -> None:
  """The pages keep their diagrams, so a removal never empties a page."""
  total = sum(len(BLOCK.findall(page.read_text(encoding="utf-8"))) for page in pages())
  assert total == 25, total


def test_every_flow_chart_names_a_direction() -> None:
  """A chart with no direction falls back to TD, the tall shape a desktop reader scrolls."""
  found = []
  for page in pages():
    for block in BLOCK.findall(page.read_text(encoding="utf-8")):
      head = block.strip().splitlines()[0].strip()
      if head.startswith("flowchart") and DIRECTION.fullmatch(head) is None:
        found.append(f"{page}: {head}")
  assert found == [], "a flow chart with no direction: " + "; ".join(found)
