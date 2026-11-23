"""Config layer of the `_default` global tier block."""

import logging
from pathlib import Path

from daedalus import config
from daedalus.catalog.discovery import read_providers
from daedalus.config import defaults

MAIN = """\
_default:
  tier:
    TIER-D:
      - "zz*"

p:
  api_key: k
  tier:
    TIER-B:
      - "*"
"""


def _load(tmp_path: Path, text: str) -> dict:
  main = tmp_path / "free.yml"
  main.write_text(text, encoding="utf-8")
  try:
    return config.load_config(main)
  finally:
    config.set_config(None)


def test_default_block_loads(tmp_path: Path) -> None:
  """A top-level `_default` block loads like any provider block."""
  loaded = _load(tmp_path, MAIN)
  assert loaded["_default"]["tier"] == {"TIER-D": ["zz*"]}, loaded["_default"]
  assert config.FILE_KEY not in loaded["_default"], "no file block"


def test_default_wrong_shape_drops_out(tmp_path: Path) -> None:
  """A `_default` tier value of wrong shape drops out with a log line."""
  records = []
  handler = logging.Handler()
  handler.emit = records.append  # type: ignore[method-assign]
  logger = logging.getLogger("daedalus.config")
  logger.addHandler(handler)
  try:
    loaded = _load(tmp_path, "_default:\n  tier: TIER-D\n")
  finally:
    logger.removeHandler(handler)
  assert "tier" not in loaded["_default"], loaded["_default"]
  assert any(
    "_default" in record.getMessage() and "tier" in record.getMessage()
    for record in records
  ), records


def test_fill_lands_default_block() -> None:
  """Shipped defaults land a `_default` block that local file lacks."""
  loaded = {"p": {"api_key": "local"}}
  found = defaults.fill(loaded, {"_default": {"tier": {"TIER-D": ["zz*"]}}})
  assert found["_default"] == {"tier": {"TIER-D": ["zz*"]}}, found


def test_fill_keeps_local_default_block() -> None:
  """Local `_default` wins whole over shipped defaults."""
  loaded = {"_default": {"tier": {"TIER-C": ["cc*"]}}}
  found = defaults.fill(loaded, {"_default": {"tier": {"TIER-D": ["zz*"]}}})
  assert found["_default"] == {"tier": {"TIER-C": ["cc*"]}}, found


def test_default_block_skips_discovery() -> None:
  """Discovery skips the `_default` block, which holds no provider key."""
  skipped = read_providers({"_default": {"tier": {"TIER-D": ["zz*"]}}})[1]
  assert any("_default" in line for line in skipped), skipped
