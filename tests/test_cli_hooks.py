"""The `daedalus hooks` command: the update, the list and the check of each file."""

import io
import json
import tarfile
from pathlib import Path

import pytest

from daedalus import cli
from daedalus.config import remote, settings
from daedalus.providers import hooks

COMMIT = "a" * 40
API = "https://api.github.com/repos/owner/name/commits/main"
ARCHIVE = f"https://codeload.github.com/owner/name/tar.gz/{COMMIT}"
BLOCK = "# ---\n# version: 1.2.0\n# surfaces: [on-request]\n# ---\n"
BODY = (BLOCK + "def on_request(value, model, headers):\n  return value\n").encode()
PLAIN = b"def on_request(value, model, headers):\n  return value\n"
RECORD = {
  "sha256": remote.digest(BODY),
  "version": "1.2.0",
  "repo": "owner/name",
  "commit": COMMIT,
}


def tar(files: dict[str, str]) -> bytes:
  """The GitHub archive of 1 commit, with the given repo paths."""
  buffer = io.BytesIO()
  with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
    for path, text in files.items():
      found = text.encode("utf-8")
      info = tarfile.TarInfo(f"name-{COMMIT[:7]}/{path}")
      info.size = len(found)
      archive.addfile(info, io.BytesIO(found))
  return buffer.getvalue()


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  """The hook folder and the lock file of 1 test."""
  monkeypatch.setattr(hooks, "ROOT", tmp_path)
  monkeypatch.setattr(remote, "LOCK", tmp_path / "hooks.lock.json")
  found = tmp_path / "hooks"
  found.mkdir()
  return found


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
  """The load check of a row test: no process, no problem."""
  monkeypatch.setattr(cli, "import_problem", lambda path: None)


def group_of(monkeypatch: pytest.MonkeyPatch, **group: object) -> None:
  """`settings.load` of the test, with the hooks group."""
  found: dict[str, object] = {"dir": "hooks", "sources": [], "disabled": []}
  found.update(group)
  monkeypatch.setattr(settings, "load", lambda path=None: {"hooks": found})


def test_update_writes_the_source(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """`hooks update` fetches the sources and prints the record of each installed file."""
  group_of(
    monkeypatch,
    sources=[
      {"repo": "owner/name", "path": "hooks", "ref": "main", "auto_update": False}
    ],
  )
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": BLOCK + "one = 1\n"}),
  }
  monkeypatch.setattr(
    remote, "fetch", lambda url, timeout=remote.TIMEOUT, report=None: pages.get(url)
  )
  assert cli.hooks_update() == 0
  assert (folder / "owner/name/one.py").exists(), "the fetch wrote the file"
  out = capsys.readouterr().out
  assert "owner/name/one.py" in out and "1.2.0" in out
  assert "owner/name@aaaaaaaaaaaa" in out
  assert "moved 1 file" in out


def test_update_needs_a_source(
  monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A settings file with no source reports it and fails."""
  group_of(monkeypatch)
  assert cli.hooks_update() == 1
  assert "no source in hooks.sources" in capsys.readouterr().out


def test_update_reports_a_bad_settings_file(
  monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A settings file that does not parse exits 2 with its message."""

  def fail(path: Path | str | None = None) -> dict[str, dict[str, object]]:
    raise settings.SettingsError("hooks.remote is gone. Use hooks.sources")

  monkeypatch.setattr(settings, "load", fail)
  assert cli.hooks_update() == 2
  assert "hooks.remote is gone" in capsys.readouterr().out


def test_list_prints_the_block_and_the_state(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """`hooks list` prints the version, the scope, the state and the source of each file."""
  (folder / "one.py").write_bytes(BODY)
  (folder / "two.py").write_bytes(PLAIN)
  remote.write_records({"one.py": RECORD})
  group_of(monkeypatch, disabled=["two.py"])
  assert cli.hooks_list() == 0
  out = capsys.readouterr().out
  assert "one.py" in out and "1.2.0" in out and "owner/name@aaaaaaaaaaaa" in out
  assert "two.py" in out
  rows = {line.split()[0]: line for line in out.splitlines() if line.strip()}
  assert " on " in rows["one.py"] or rows["one.py"].endswith("on")
  assert "off" in rows["two.py"]


def test_verify_passes_a_recorded_file(
  folder: Path,
  monkeypatch: pytest.MonkeyPatch,
  quiet: None,
  capsys: pytest.CaptureFixture[str],
) -> None:
  """A file that matches its record reads ok, with its version and its commit."""
  (folder / "one.py").write_bytes(BODY)
  remote.write_records({"one.py": RECORD})
  group_of(monkeypatch)
  assert cli.hooks_verify() == 0
  assert (
    "ok        one.py: lock ok: 1.2.0 owner/name@aaaaaaaaaaaa"
    in capsys.readouterr().out
  )


def test_verify_fails_a_changed_file_and_a_missing_one(
  folder: Path,
  monkeypatch: pytest.MonkeyPatch,
  quiet: None,
  capsys: pytest.CaptureFixture[str],
) -> None:
  """A changed body and a record without a file both fail with exit 1."""
  (folder / "one.py").write_bytes(b"# edited\n")
  remote.write_records({"one.py": RECORD, "gone.py": {**RECORD, "sha256": "b" * 64}})
  group_of(monkeypatch)
  assert cli.hooks_verify() == 1
  out = capsys.readouterr().out
  assert "changed: the lock holds" in out
  assert "gone.py: in the lock file, not in the folder" in out


def test_verify_leaves_an_unpinned_file_alone(
  folder: Path,
  monkeypatch: pytest.MonkeyPatch,
  quiet: None,
  capsys: pytest.CaptureFixture[str],
) -> None:
  """A hand-written file with no record reports the update step and does not fail."""
  (folder / "one.py").write_bytes(PLAIN)
  group_of(monkeypatch)
  assert cli.hooks_verify() == 0
  assert (
    "unpinned  one.py: no record; run `daedalus hooks update`"
    in capsys.readouterr().out
  )


def test_verify_warns_on_a_file_that_does_not_load(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A load problem warns, and the command still passes."""
  (folder / "one.py").write_bytes(b"def broken(\n")
  monkeypatch.setattr(
    cli, "import_problem", lambda path: "SyntaxError: '(' was never closed"
  )
  group_of(monkeypatch)
  assert cli.hooks_verify() == 0
  out = capsys.readouterr().out
  assert "warn      one.py: no record; run" in out
  assert "does not load: SyntaxError" in out


def test_verify_reports_a_bad_settings_file(
  folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  """A settings file that does not parse exits 2 with its message."""

  def fail(path: Path | str | None = None) -> dict[str, dict[str, object]]:
    raise settings.SettingsError("hooks.remote is gone. Use hooks.sources")

  monkeypatch.setattr(settings, "load", fail)
  assert cli.hooks_verify() == 2
  assert "hooks.remote is gone" in capsys.readouterr().out


def test_the_load_check_reads_a_file(folder: Path) -> None:
  """The real check loads a good hook file, and names the error of a broken one."""
  good = folder / "good.py"
  good.write_bytes(BODY)
  assert cli.import_problem(good) is None
  broken = folder / "broken.py"
  broken.write_bytes(b"def broken(\n")
  assert "SyntaxError" in (cli.import_problem(broken) or "")
