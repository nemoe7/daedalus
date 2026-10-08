"""Tests of install.sh in a checkout with stub Docker and curl commands."""

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
  # The dev variables show which image a start builds or pulls.
  docker.write_text(
    "#!/bin/sh\n"
    f'echo "$DAEDALUS_DEV_IMAGE|$DAEDALUS_DEV_PULL|$DAEDALUS_VERSION|$*"'
    f" >> {tmp_path / 'docker.log'}\n"
  )
  docker.chmod(0o755)
  curl = stub / "curl"
  curl.write_text(
    "#!/bin/sh\n"
    'case "$*" in\n'
    "  *api.github.com*) printf '[]' ;;\n"
    '  *archive*) tar -czf - -C "$DAEDALUS_TEST_SOURCE" '
    "--transform='s,^,daedalus/,' compose.yml compose.dev.yml .env.example config install.sh services ;;\n"
    '  *) echo "Unexpected curl request: $*" >&2; exit 1 ;;\n'
    "esac\n"
  )
  curl.chmod(0o755)
  env = {
    **os.environ,
    "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
    "HOME": str(tmp_path),
    "DAEDALUS_TEST_SOURCE": str(ROOT),
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


def run_interactive(
  folder: Path,
  env: dict[str, str],
  *answers: str,
  piped: bool = False,
  cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
  """Run install.sh on a pseudo-terminal with line answers. A piped run reads the script from a pipe."""
  import pty
  import selectors
  import signal
  import time

  script = str(folder / "install.sh")
  argv = ["bash", "-c", f'cat "{script}" | bash'] if piped else ["bash", script]
  pid, master = pty.fork()
  if pid == 0:
    if cwd is not None:
      os.chdir(cwd)
    os.execvpe("bash", argv, env)
  os.write(master, ("\n".join(answers) + "\n").encode())
  output = bytearray()
  # select() refuses a descriptor over 1023, and the whole suite keeps many open.
  watched = selectors.DefaultSelector()
  watched.register(master, selectors.EVENT_READ)
  deadline = time.monotonic() + 60
  while True:
    waited, status = os.waitpid(pid, os.WNOHANG)
    if waited == pid:
      break
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      os.kill(pid, signal.SIGKILL)
      os.waitpid(pid, 0)
      watched.close()
      os.close(master)
      detail = output.decode(errors="replace")
      raise AssertionError(f"interactive installer timed out: {detail}")
    if watched.select(min(remaining, 0.2)):
      try:
        chunk = os.read(master, 4096)
      except OSError:
        chunk = b""
      if chunk:
        output.extend(chunk)
  while watched.select(0):
    try:
      chunk = os.read(master, 4096)
    except OSError:
      break
    if not chunk:
      break
    output.extend(chunk)
  watched.close()
  os.close(master)
  return subprocess.CompletedProcess(
    argv,
    os.waitstatus_to_exitcode(status),
    output.decode(errors="replace"),
    "",
  )


def test_new_key(tmp_path: Path) -> None:
  """An empty master key gets a new one, and Compose starts with no question."""
  env_text = "DAEDALUS_MASTER_KEY=\nWEBUI_SECRET_KEY=\nTZ=\n"
  folder, env = checkout(tmp_path, env_text)
  result = run(folder, env)
  assert result.returncode == 0, result.stderr
  assert (folder / ".env.bak").read_text() == env_text
  lines = (folder / ".env").read_text().splitlines()
  keys = [line for line in lines if line.startswith("DAEDALUS_MASTER_KEY=")]
  key = keys[0].partition("=")[2]
  secret_keys = [line for line in lines if line.startswith("WEBUI_SECRET_KEY=")]
  secret_key = secret_keys[0].partition("=")[2]
  assert len(keys) == 1 and len(key) == 43 and key.startswith("sk-")
  assert key in result.stdout
  assert len(secret_keys) == 1 and len(secret_key) == 67
  assert secret_key.startswith("sk-")
  assert "Continue?" not in result.stdout
  assert "compose up -d" in (tmp_path / "docker.log").read_text()
  before_again = (folder / ".env").read_text()
  again = run(folder, env)
  assert again.returncode == 0 and key in (folder / ".env").read_text(), (
    "a second run keeps the key"
  )
  assert (folder / ".env.bak").read_text() == before_again
  assert "master key" not in again.stdout


def test_webui_profile_does_not_require_tailscale_service_dir(tmp_path: Path) -> None:
  """The webui profile runs without the optional Open WebUI Tailscale config."""
  env_text = f"DAEDALUS_MASTER_KEY={'k' * 20}\nCOMPOSE_PROFILES=webui\n"
  folder, env = checkout(tmp_path, env_text)
  result = run(folder, env)
  assert result.returncode == 0, result.stderr + result.stdout
  assert "required service dirs" not in result.stdout
  assert "Install Headroom?" not in result.stdout
  assert "Install Tika?" not in result.stdout
  assert "COMPOSE_PROFILES=webui" in (folder / ".env").read_text()
  assert "compose up -d" in (tmp_path / "docker.log").read_text()


@pytest.mark.parametrize(
  ("answers", "expected_profiles", "prompts", "skipped_prompts", "service_dirs"),
  [
    (
      ("y", "y", "n", "y"),
      "headroom,tailscale",
      (
        "Install Headroom? [y/N]",
        "Install Open WebUI? [y/N]",
        "Install Tailscale for Daedalus? [y/N]",
      ),
      (
        "Install Tika?",
        "Enable SearXNG search?",
        "Install Tailscale for Open WebUI?",
      ),
      ("services/tailscale",),
    ),
    (
      ("y", "n", "y", "n", "y", "y", "y"),
      "webui,tika,search,tailscale-openwebui",
      (
        "Install Headroom? [y/N]",
        "Install Open WebUI? [y/N]",
        "Install Tailscale for Daedalus? [y/N]",
        "Install Tika? [y/N]",
        "Enable SearXNG search? [y/N]",
        "Install Tailscale for Open WebUI? [y/N]",
      ),
      (),
      ("services/searxng", "services/tailscale-openwebui"),
    ),
  ],
)
def test_profile_prompts_are_dependency_aware(
  tmp_path: Path,
  answers: tuple[str, ...],
  expected_profiles: str,
  prompts: tuple[str, ...],
  skipped_prompts: tuple[str, ...],
  service_dirs: tuple[str, ...],
) -> None:
  """First install asks for profiles and skips WebUI options when declined."""
  folder, env = checkout(tmp_path, None)
  result = run_interactive(folder, env, *answers)
  assert result.returncode == 0, result.stdout
  for prompt in prompts:
    assert prompt in result.stdout
  for prompt in skipped_prompts:
    assert prompt not in result.stdout
  profile_lines = [
    line
    for line in (folder / ".env").read_text().splitlines()
    if line.startswith("COMPOSE_PROFILES=")
  ]
  assert profile_lines == [f"COMPOSE_PROFILES={expected_profiles}"]
  for service_dir in service_dirs:
    assert (folder / service_dir).is_dir()
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
  assert "Install Headroom?" not in result.stdout
  assert not (folder / ".env").exists() and not (tmp_path / "docker.log").exists()


def test_bad_key(tmp_path: Path) -> None:
  """A master key that is too short stops the script before Docker."""
  folder, env = checkout(tmp_path, "DAEDALUS_MASTER_KEY=short\n")
  result = run(folder, env)
  assert result.returncode == 1 and "16 or more characters" in result.stderr
  assert not (tmp_path / "docker.log").exists()


def test_dev_in_checkout_needs_the_dev_compose(tmp_path: Path) -> None:
  """--dev beside the source stops without compose.dev.yml."""
  folder, env = checkout(tmp_path, "DAEDALUS_MASTER_KEY=" + "k" * 20 + "\n")
  (folder / "daedalus").mkdir()
  result = run(folder, env, "--dev")
  assert result.returncode == 1 and "compose.dev.yml" in result.stderr
  assert not (tmp_path / "docker.log").exists()


def test_dev_with_source_builds_the_image(tmp_path: Path) -> None:
  """--dev with the source builds the image, with no dev image to pull."""
  folder, env = checkout(tmp_path, "DAEDALUS_MASTER_KEY=" + "k" * 20 + "\n")
  shutil.copy(ROOT / "compose.dev.yml", folder / "compose.dev.yml")
  (folder / "daedalus").mkdir()
  result = run(folder, env, "--dev")
  assert result.returncode == 0, result.stderr + result.stdout
  assert "|||compose -f compose.dev.yml up -d" in (tmp_path / "docker.log").read_text()


def test_dev_without_source_pulls_the_dev_image(tmp_path: Path) -> None:
  """--dev without the source pulls the dev image of main."""
  folder, env = checkout(tmp_path, "DAEDALUS_MASTER_KEY=" + "k" * 20 + "\n")
  shutil.copy(ROOT / "compose.dev.yml", folder / "compose.dev.yml")
  result = run(folder, env, "--dev")
  assert result.returncode == 0, result.stderr + result.stdout
  assert (
    "ghcr.io/nemoe7/daedalus:dev|always||compose -f compose.dev.yml up -d"
    in (tmp_path / "docker.log").read_text()
  )


def test_dev_downloads_from_main_and_pulls(tmp_path: Path) -> None:
  """The dev marker downloads the files of main, then pulls the dev image."""
  folder, env = checkout(tmp_path, "DAEDALUS_MASTER_KEY=" + "k" * 20 + "\n")
  (folder / ".daedalus-dev").write_text("")
  result = run_interactive(folder, env, "y")
  assert result.returncode == 0, result.stdout
  assert "The files of main are in" in result.stdout
  assert (folder / "compose.dev.yml").is_file()
  assert (
    "ghcr.io/nemoe7/daedalus:dev|always||compose -f compose.dev.yml up -d"
    in (tmp_path / "docker.log").read_text()
  )


def test_a_crlf_env_takes_a_valid_key(tmp_path: Path) -> None:
  """A .env with Windows line ends passes the master key check, and an empty key gets a new 1."""
  key = "sk-" + "a" * 41
  folder, env = checkout(tmp_path, f"DAEDALUS_MASTER_KEY={key}\r\n")
  done = run(folder, env)
  assert done.returncode == 0, done.stderr
  text = (folder / ".env").read_text()
  assert f"DAEDALUS_MASTER_KEY={key}\n" in text, text
  assert "\r" not in text, "the carriage return leaves the file"

  (tmp_path / "second").mkdir()
  folder, env = checkout(tmp_path / "second", "DAEDALUS_MASTER_KEY=\r\n")
  done = run(folder, env)
  assert done.returncode == 0, done.stderr
  text = (folder / ".env").read_text()
  assert "DAEDALUS_MASTER_KEY=sk-" in text, text
  assert "DAEDALUS_MASTER_KEY=\r\n" not in text, text


def test_a_piped_run_keeps_the_folder_of_another_project(tmp_path: Path) -> None:
  """The pipe leaves BASH_SOURCE empty, so a compose.yml in the current folder claims no install."""
  folder, env = checkout(tmp_path, None)
  project = tmp_path / "project"
  project.mkdir()
  shutil.copy(ROOT / "compose.yml", project / "compose.yml")
  home = tmp_path / "home"
  home.mkdir()
  done = run_interactive(
    folder, {**env, "HOME": str(home)}, "y", "n", "n", "n", piped=True, cwd=project
  )
  assert done.returncode == 0, done.stdout + done.stderr
  assert (home / "daedalus" / "install.sh").is_file(), done.stdout
  assert not (project / "install.sh").exists(), "the other project folder stays"
  assert not (project / ".env").exists(), "the other project folder stays"
