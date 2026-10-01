"""Tests of install.sh in a checkout, with a stub docker and no network."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
  sys.platform == "win32" or not shutil.which("bash"), reason="needs bash"
)


def checkout(tmp_path: Path, env_text: str | None) -> tuple[Path, dict[str, str]]:
  """A folder with install.sh, compose.yml, .env.example and config/, and a PATH with a docker that logs its arguments."""
  folder = tmp_path / "daedalus"
  folder.mkdir()
  for name in ("install.sh", "compose.yml", ".env.example"):
    shutil.copy(ROOT / name, folder / name)
  # The installer downloads a missing config/ dir, and a checkout always has one.
  (folder / "config").mkdir()
  if env_text is not None:
    (folder / ".env").write_text(env_text)
  stub = tmp_path / "bin"
  stub.mkdir()
  docker = stub / "docker"
  docker.write_text(f'#!/bin/sh\necho "$*" >> {tmp_path / "docker.log"}\n')
  docker.chmod(0o755)
  env = {
    **os.environ,
    "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
    "HOME": str(tmp_path),
  }
  return folder, env


def run(
  folder: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
  """Run install.sh with no terminal, so a question gets no answer."""
  return subprocess.run(
    ["bash", str(folder / "install.sh"), *args],
    env=env,
    capture_output=True,
    text=True,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
    timeout=60,
    check=False,
  )


def test_new_key(tmp_path: Path) -> None:
  """An empty master key gets a new one, and Compose starts with no question."""
  folder, env = checkout(tmp_path, "DAEDALUS_MASTER_KEY=\nTZ=\n")
  result = run(folder, env)
  assert result.returncode == 0, result.stderr
  keys = [
    line
    for line in (folder / ".env").read_text().splitlines()
    if line.startswith("DAEDALUS_MASTER_KEY=")
  ]
  key = keys[0].partition("=")[2]
  assert len(keys) == 1 and len(key) == 40 and key in result.stdout
  assert "Continue?" not in result.stdout
  assert "compose up -d" in (tmp_path / "docker.log").read_text()
  again = run(folder, env)
  assert again.returncode == 0 and key in (folder / ".env").read_text(), (
    "a second run keeps the key"
  )
  assert "master key" not in again.stdout


def test_webui_profile_does_not_require_tailscale_service_dir(tmp_path: Path) -> None:
  """The webui profile runs without the optional Open WebUI Tailscale config."""
  env_text = f"DAEDALUS_MASTER_KEY={'k' * 20}\nCOMPOSE_PROFILES=webui\n"
  folder, env = checkout(tmp_path, env_text)
  result = run(folder, env)
  assert result.returncode == 0, result.stderr + result.stdout
  assert "required service dirs" not in result.stdout
  assert "compose up -d" in (tmp_path / "docker.log").read_text()


def test_tailscale_openwebui_profile_requires_service_dir(tmp_path: Path) -> None:
  """The independent sidecar profile requests its own Tailscale config."""
  env_text = f"DAEDALUS_MASTER_KEY={'k' * 20}\nCOMPOSE_PROFILES=tailscale-openwebui\n"
  folder, env = checkout(tmp_path, env_text)
  result = run(folder, env)
  assert result.returncode == 1
  assert "required service dirs: services/tailscale-openwebui)" in result.stdout
  assert "Stopped. Nothing changed." in result.stdout
  assert not (tmp_path / "docker.log").exists()


def test_question(tmp_path: Path) -> None:
  """A missing .env asks first. With no answer, nothing changes."""
  folder, env = checkout(tmp_path, None)
  result = run(folder, env)
  assert (
    result.returncode == 1 and "This script installs or downloads:" in result.stdout
  )
  assert "Stopped. Nothing changed." in result.stdout
  assert not (folder / ".env").exists() and not (tmp_path / "docker.log").exists()


def test_bad_key(tmp_path: Path) -> None:
  """A master key that is too short stops the script before Docker."""
  folder, env = checkout(tmp_path, "DAEDALUS_MASTER_KEY=short\n")
  result = run(folder, env)
  assert result.returncode == 1 and "16 or more characters" in result.stderr
  assert not (tmp_path / "docker.log").exists()


def test_dev_needs_source(tmp_path: Path) -> None:
  """--dev stops without the source folder."""
  folder, env = checkout(tmp_path, "DAEDALUS_MASTER_KEY=" + "k" * 20 + "\n")
  result = run(folder, env, "--dev")
  assert result.returncode == 1 and "git checkout" in result.stderr
