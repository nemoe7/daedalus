"""The `daedalus hooks` command: the pin, the lock and the check of each file."""

from pathlib import Path

import pytest

from daedalus import cli
from daedalus.config import remote, settings

BODY = b"def on_request(value, model, headers):\n  return value\n"
SECOND = b"# two\n"
PIN = remote.digest(BODY)
SECOND_PIN = remote.digest(SECOND)


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  """A hooks folder and a lock file of the test, in place of the config folder."""
  hooks = tmp_path / "hooks"
  hooks.mkdir()
  monkeypatch.setattr(remote, "FOLDER", hooks)
  monkeypatch.setattr(remote, "LOCK", tmp_path / "hooks.lock.json")
  return hooks


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
  """The load check of a row test: no process, no problem."""
  monkeypatch.setattr(cli, "import_problem", lambda path: None)


def no_remote(
  monkeypatch: pytest.MonkeyPatch, entries: list[dict[str, str]] | None = None
) -> None:
  """`settings.load` without the file, with the given remote entries."""
  monkeypatch.setattr(
    settings, "load", lambda path=None: {"remote_hooks": entries or []}
  )


def test_pin_prints_every_file_and_writes_the_lock(
  folder: Path, quiet: None, capsys: pytest.CaptureFixture[str]
) -> None:
  """A bare `pin` covers the folder, prints `sha256  name`, and records the digests."""
  (folder / "one.py").write_bytes(BODY)
  (folder / "two.py").write_bytes(SECOND)
  assert cli.hooks_pin([], []) == 0
  out = capsys.readouterr().out
  assert f"{PIN}  one.py" in out
  assert f"{SECOND_PIN}  two.py" in out
  assert remote.read_lock() == {"one.py": PIN, "two.py": SECOND_PIN}


def test_pin_takes_a_config_path_and_keeps_the_old_lock(
  folder: Path, quiet: None, capsys: pytest.CaptureFixture[str]
) -> None:
  """A `hooks/...` path names the file, and the lock keeps the entries of other files."""
  remote.write_lock({"old.py": "a" * 64})
  (folder / "one.py").write_bytes(BODY)
  assert cli.hooks_pin(["hooks/one.py"], []) == 0
  assert f"{PIN}  one.py" in capsys.readouterr().out
  assert remote.read_lock() == {"old.py": "a" * 64, "one.py": PIN}


def test_pin_reports_a_missing_file_and_a_refused_name(
  folder: Path, quiet: None, capsys: pytest.CaptureFixture[str]
) -> None:
  """A name that is not there, or that holds a separator, fails the command."""
  (folder / "one.py").write_bytes(BODY)
  assert cli.hooks_pin(["gone.py", "../escape.py", "one.py"], []) == 1
  out = capsys.readouterr().out
  assert "missing   gone.py" in out
  assert "refused   ../escape.py" in out
  assert f"{PIN}  one.py" in out


def test_pin_reads_a_url(
  folder: Path,
  quiet: None,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  """`--url` prints the digest of the bytes at the URL, for a remote_hooks entry."""
  monkeypatch.setattr(remote, "fetch", lambda url, timeout=remote.TIMEOUT: BODY)
  assert cli.hooks_pin([], ["https://example.com/my_hook.py"]) == 0
  assert f"{PIN}  https://example.com/my_hook.py" in capsys.readouterr().out


def test_verify_passes_a_pinned_file(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A file that matches its lock entry and its remote pin reads ok."""
  (folder / "one.py").write_bytes(BODY)
  remote.write_lock({"one.py": PIN})
  no_remote(
    monkeypatch,
    [{"url": "https://example.com/one.py", "sha256": PIN, "name": "one.py"}],
  )
  assert cli.hooks_verify() == 0
  assert "ok        one.py: pin ok; settings pin ok" in capsys.readouterr().out


def test_verify_fails_a_changed_file_and_a_missing_one(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A changed body and a lock entry without a file both fail with exit 1."""
  (folder / "one.py").write_bytes(b"# edited\n")
  remote.write_lock({"one.py": PIN, "gone.py": "b" * 64})
  no_remote(monkeypatch)
  assert cli.hooks_verify() == 1
  out = capsys.readouterr().out
  assert "changed: the lock holds" in out
  assert "gone.py: in the lock file, not in the folder" in out


def test_verify_fails_a_drift_off_the_settings_pin(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A file whose bytes differ from the settings pin fails, and a missing download does not."""
  (folder / "one.py").write_bytes(BODY)
  no_remote(
    monkeypatch,
    [{"url": "https://example.com/one.py", "sha256": "c" * 64, "name": "one.py"}],
  )
  assert cli.hooks_verify() == 1
  assert "drift: the bytes differ from the settings pin" in capsys.readouterr().out
  (folder / "one.py").unlink()
  no_remote(
    monkeypatch,
    [{"url": "https://example.com/one.py", "sha256": PIN, "name": "one.py"}],
  )
  assert cli.hooks_verify() == 0
  assert "downloads at the next start" in capsys.readouterr().out


def test_verify_leaves_an_unpinned_file_alone(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A hand-written file with no pin reports the pin step and does not fail."""
  (folder / "one.py").write_bytes(BODY)
  no_remote(monkeypatch)
  assert cli.hooks_verify() == 0
  out = capsys.readouterr().out
  assert "unpinned  one.py: no pin recorded" in out


def test_verify_warns_on_a_file_that_does_not_load(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A load problem warns, and the command still passes."""
  (folder / "one.py").write_bytes(b"def broken(\n")
  monkeypatch.setattr(
    cli, "import_problem", lambda path: "SyntaxError: '(' was never closed"
  )
  no_remote(monkeypatch)
  assert cli.hooks_verify() == 0
  out = capsys.readouterr().out
  assert "warn      one.py: no pin recorded; run" in out
  assert "does not load: SyntaxError" in out


def test_the_load_check_reads_a_file(folder: Path) -> None:
  """The real check loads a good hook file, and names the error of a broken one."""
  good = folder / "good.py"
  good.write_bytes(BODY)
  assert cli.import_problem(good) is None
  broken = folder / "broken.py"
  broken.write_bytes(b"def broken(\n")
  assert "SyntaxError" in (cli.import_problem(broken) or "")


def test_verify_reports_a_broken_settings_file(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A settings file that does not parse exits 2 with its message."""

  def fail(path: Path | str = settings.DEFAULT_PATH) -> dict[str, dict[str, object]]:
    raise settings.SettingsError("unknown key remote_hook_host in config/daedalus.yml")

  monkeypatch.setattr(settings, "load", fail)
  assert cli.hooks_verify() == 2
  assert "unknown key remote_hook_host" in capsys.readouterr().out
