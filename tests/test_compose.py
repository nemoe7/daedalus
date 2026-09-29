"""Tests of the Docker Compose files."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_dev_copy() -> None:
  """compose.dev.yml is compose.yml with a Daedalus build in place of the image."""
  main = yaml.safe_load((ROOT / "compose.yml").read_text())
  dev = yaml.safe_load((ROOT / "compose.dev.yml").read_text())
  assert main["services"]["daedalus"].pop("image").startswith("ghcr.io/")
  built = dev["services"]["daedalus"]
  build = {"context": ".", "args": {"VERSION": "${DAEDALUS_VERSION:-dev}"}}
  assert (built.pop("image"), built.pop("build"), built.pop("pull_policy")) == (
    "daedalus:dev",
    build,
    "build",
  )
  assert dev == main
