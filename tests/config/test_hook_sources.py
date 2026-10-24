"""The hook sources: the ref, the archive, the written files and the lock record."""

import io
import json
import tarfile
from pathlib import Path

import pytest

from daedalus.config import remote
from daedalus.providers import hooks

COMMIT = "a" * 40
API = "https://api.github.com/repos/owner/name/commits/main"
ARCHIVE = f"https://codeload.github.com/owner/name/tar.gz/{COMMIT}"


def block(version: str = "1.2.0", name: str = "one") -> str:
  """A hook file with a frontmatter block."""
  return (
    "# ---\n"
    f"# name: {name}\n"
    f"# version: {version}\n"
    "# surfaces: [on-answer]\n"
    "# ---\n"
    "def on_answer(answer, model):\n"
    "  return answer\n"
  )


def tar(files: dict[str, str]) -> bytes:
  """The GitHub archive of 1 commit, with the given repo paths."""
  buffer = io.BytesIO()
  with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
    for path, text in files.items():
      body = text.encode("utf-8")
      info = tarfile.TarInfo(f"name-{COMMIT[:7]}/{path}")
      info.size = len(body)
      archive.addfile(info, io.BytesIO(body))
  return buffer.getvalue()


def source(**payload: object) -> dict[str, object]:
  """1 source entry of the settings file."""
  found: dict[str, object] = {"repo": "owner/name", "path": "hooks", "ref": "main"}
  found.update(payload)
  return found


def answers(monkeypatch: pytest.MonkeyPatch, pages: dict[str, bytes]) -> list[str]:
  """A fetch that answers from the pages, and the URLs it saw, in order."""
  calls: list[str] = []

  def get(
    url: str, timeout: float = remote.TIMEOUT, report: list[str] | None = None
  ) -> bytes | None:
    calls.append(url)
    return pages.get(url)

  monkeypatch.setattr(remote, "fetch", get)
  return calls


@pytest.mark.parametrize("existing", [False, True])
def test_write_reports_directory_failure(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
  existing: bool,
) -> None:
  """A failed parent mkdir reports failure and keeps any existing hook bytes."""
  target = tmp_path / "locked" / "one.py"
  before = b"previous hook\n"
  if existing:
    target.parent.mkdir()
    target.write_bytes(before)
  original = Path.mkdir

  def denied(path: Path, *args, **kwargs) -> None:
    if path == target.parent:
      raise PermissionError("test directory failure")
    original(path, *args, **kwargs)

  monkeypatch.setattr(Path, "mkdir", denied)
  report: list[str] = []
  with caplog.at_level("ERROR", logger=remote.logger.name):
    assert remote.write(target, b"new hook\n", report) is False
  assert len(report) == 1
  assert any(record.name == remote.logger.name for record in caplog.records)
  if existing:
    assert target.read_bytes() == before
  else:
    assert not target.exists()
  assert not target.with_name(f".{target.name}.part").exists()


def test_update_keeps_file_and_lock_on_directory_failure(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A parent mkdir error keeps the installed file and its lock record."""
  target = tmp_path / "owner/name/one.py"
  target.parent.mkdir(parents=True)
  before = block("1.0.0").encode()
  target.write_bytes(before)
  lock = tmp_path / "hooks.lock.json"
  records = {
    "owner/name/one.py": {
      "sha256": remote.digest(before),
      "version": "1.0.0",
      "repo": "owner/name",
      "commit": "b" * 40,
    }
  }
  remote.write_records(records, lock)
  lock_before = lock.read_bytes()
  answers(
    monkeypatch,
    {
      API: json.dumps({"sha": COMMIT}).encode(),
      ARCHIVE: tar({"hooks/one.py": block("1.3.0")}),
    },
  )
  original = Path.mkdir

  def denied(path: Path, *args, **kwargs) -> None:
    if path == target.parent:
      raise PermissionError("test directory failure")
    original(path, *args, **kwargs)

  monkeypatch.setattr(Path, "mkdir", denied)
  report: list[str] = []
  assert remote.update([source()], tmp_path, lock, report=report) == []
  assert len(report) == 1
  assert target.read_bytes() == before
  assert lock.read_bytes() == lock_before
  assert remote.read_records(lock) == records
  assert not target.with_name(f".{target.name}.part").exists()


def test_repo_name() -> None:
  """A plain owner/name and a GitHub URL both give the owner/name, and another value gives None."""
  assert remote.repo_name("owner/name") == "owner/name"
  assert remote.repo_name("https://github.com/owner/name") == "owner/name"
  assert remote.repo_name("https://github.com/owner/name.git") == "owner/name"
  assert (
    remote.repo_name("https://github.com/owner/name/tree/main/hooks") == "owner/name"
  )
  assert remote.repo_name("owner") is None
  assert remote.repo_name("https://gitlab.com/owner/name") is None
  assert remote.repo_name(None) is None
  assert remote.repo_name("../name") is None, "a part that walks out stays out"
  assert remote.repo_name("owner/..") is None
  assert remote.repo_name("./name") is None
  assert remote.repo_name("-" * 500) is None, "a long run of dashes stays out"
  assert remote.repo_name(f"owner/{'n' * 200}") is None, (
    "a name longer than GitHub takes"
  )


def test_a_source_writes_its_files(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The archive lands under owner/name, and only the `.py` files under the path."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar(
      {
        "hooks/one.py": block(),
        "hooks/two.py": block(name="two"),
        "hooks/deep/three.py": block(name="three"),
        "hooks/notes.md": "notes",
        "other/four.py": block(name="four"),
      }
    ),
  }
  answers(monkeypatch, pages)
  moved = remote.update([source()], tmp_path, tmp_path / "hooks.lock.json")
  assert moved == ["owner/name/one.py", "owner/name/two.py"]
  assert (tmp_path / "owner/name/one.py").read_text(encoding="utf-8") == block()
  assert not (tmp_path / "owner/name/three.py").exists()
  assert not (tmp_path / "owner/name/notes.md").exists()
  assert not (tmp_path / "owner/name/four.py").exists()
  assert list(tmp_path.glob("*.py")) == [], "a fetched file lands under the repo alone"
  assert list(tmp_path.glob(".*")) == [], "no half file stays"


def test_the_lock_records_the_source(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The lock keys an identity owner/repo/file.py, and holds its sha256, version, repo, and commit."""
  body = block()
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": body}),
  }
  answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  remote.update([source()], tmp_path, lock)
  assert remote.read_records(lock) == {
    "owner/name/one.py": {
      "sha256": remote.digest(body.encode("utf-8")),
      "version": "1.2.0",
      "repo": "owner/name",
      "commit": COMMIT,
    }
  }


def test_a_bad_block_stays_out(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  """Invalid hook metadata prevents installation and appears in warnings."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar(
      {
        "hooks/good.py": block(),
        "hooks/later.py": "# ---\n# surfaces: [on-later]\n# ---\n",
        "hooks/new.py": '# ---\n# requires: ">=99"\n# ---\n',
      }
    ),
  }
  answers(monkeypatch, pages)
  with caplog.at_level("WARNING"):
    moved = remote.update([source()], tmp_path, tmp_path / "hooks.lock.json")
  assert moved == ["owner/name/good.py"]
  assert not (tmp_path / "owner/name/later.py").exists()
  assert not (tmp_path / "owner/name/new.py").exists()
  assert "later.py" in caplog.text
  assert "new.py" in caplog.text


def test_no_block_still_installs(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  """A hook without frontmatter installs with an empty version and a frontmatter warning."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar(
      {"hooks/plain.py": "def on_answer(answer, model):\n  return answer\n"}
    ),
  }
  answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  with caplog.at_level("WARNING"):
    moved = remote.update([source()], tmp_path, lock)
  assert moved == ["owner/name/plain.py"]
  assert remote.read_records(lock)["owner/name/plain.py"]["version"] == ""
  assert "frontmatter" in caplog.text


def test_a_failed_ref_keeps_the_files(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A ref that does not answer writes nothing, and the file and the record stay."""
  kept = {
    "sha256": "b" * 64,
    "version": "1.0.0",
    "repo": "owner/name",
    "commit": "c" * 40,
  }
  lock = tmp_path / "hooks.lock.json"
  (tmp_path / "owner/name/one.py").parent.mkdir(parents=True)
  (tmp_path / "owner/name/one.py").write_text(block(), encoding="utf-8")
  remote.write_records({"owner/name/one.py": kept}, lock)
  before = lock.read_text(encoding="utf-8")
  answers(monkeypatch, {})
  assert remote.update([source()], tmp_path, lock) == []
  assert (tmp_path / "owner/name/one.py").read_text(encoding="utf-8") == block()
  assert lock.read_text(encoding="utf-8") == before
  assert remote.read_records(lock)["owner/name/one.py"] == kept


def test_a_bad_archive_keeps_the_files(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """An archive that does not read writes nothing, and the file and the record stay."""
  lock = tmp_path / "hooks.lock.json"
  (tmp_path / "owner/name/one.py").parent.mkdir(parents=True)
  (tmp_path / "owner/name/one.py").write_text(block(), encoding="utf-8")
  remote.write_records(
    {
      "owner/name/one.py": {
        "sha256": "b" * 64,
        "version": "1.0.0",
        "repo": "owner/name",
        "commit": "c" * 40,
      }
    },
    lock,
  )
  before = lock.read_text(encoding="utf-8")
  pages = {API: json.dumps({"sha": COMMIT}).encode(), ARCHIVE: b"not a tar"}
  answers(monkeypatch, pages)
  assert remote.update([source()], tmp_path, lock) == []
  assert (tmp_path / "owner/name/one.py").read_text(encoding="utf-8") == block()
  assert lock.read_text(encoding="utf-8") == before


def test_a_moved_install_leaves_no_flat_copy(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A legacy flat install moves under owner/name when the source writes again."""
  legacy = {
    "sha256": "b" * 64,
    "version": "1.0.0",
    "repo": "owner/name",
    "commit": "c" * 40,
  }
  lock = tmp_path / "hooks.lock.json"
  (tmp_path / "one.py").write_text(block(), encoding="utf-8")
  remote.write_records({"one.py": legacy}, lock)
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block()}),
  }
  answers(monkeypatch, pages)
  moved = remote.update([source()], tmp_path, lock)
  assert moved == ["owner/name/one.py"]
  assert (tmp_path / "owner/name/one.py").is_file()
  assert not (tmp_path / "one.py").exists(), "the flat copy moved, it did not fork"
  found = remote.read_records(lock)
  assert list(found) == ["owner/name/one.py"]


def test_only_missing_skips_a_complete_source(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A start fetches a source only when 1 of its recorded files is missing from the folder."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block()}),
  }
  calls = answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  remote.update([source()], tmp_path, lock)
  calls.clear()
  assert remote.update([source()], tmp_path, lock, only_missing=True) == []
  assert calls == []


def test_only_missing_fetches_a_missing_file(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A start fetches when a recorded file is gone from the folder."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block()}),
  }
  calls = answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  remote.update([source()], tmp_path, lock)
  (tmp_path / "owner/name/one.py").unlink()
  calls.clear()
  assert remote.update([source()], tmp_path, lock, only_missing=True) == [
    "owner/name/one.py"
  ]
  assert calls == [API, ARCHIVE]


def test_auto_update_follows_the_ref(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A source with auto_update reads the ref at a start, even when every file is in place."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block()}),
  }
  calls = answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  remote.update([source()], tmp_path, lock)
  calls.clear()
  assert (
    remote.update([source(auto_update=True)], tmp_path, lock, only_missing=True) == []
  )
  assert calls == [API, ARCHIVE]


def test_a_file_that_leaves_the_repo_stays_pinned(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A file that the archive no longer holds stays on disk, with its record."""
  lock = tmp_path / "hooks.lock.json"
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block(), "hooks/two.py": block(name="two")}),
  }
  answers(monkeypatch, pages)
  remote.update([source()], tmp_path, lock)
  second = "d" * 40
  pages["https://api.github.com/repos/owner/name/commits/main"] = json.dumps(
    {"sha": second}
  ).encode()
  pages[f"https://codeload.github.com/owner/name/tar.gz/{second}"] = tar(
    {"hooks/one.py": block()}
  )
  assert remote.update([source()], tmp_path, lock) == []
  assert (tmp_path / "owner/name/two.py").exists(), "the file stays on disk"
  found = remote.read_records(lock)
  assert found["owner/name/two.py"]["repo"] == "owner/name"
  assert found["owner/name/two.py"]["commit"] == COMMIT, (
    "the record keeps its own commit"
  )
  assert found["owner/name/one.py"]["commit"] == second


def test_other_sources_stay_in_the_lock(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The records of another repo stay after 1 source updates."""
  lock = tmp_path / "hooks.lock.json"
  mine = {
    "sha256": "b" * 64,
    "version": "0.1",
    "repo": "other/repo",
    "commit": "c" * 40,
  }
  remote.write_records({"other/repo/mine.py": mine}, lock)
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block()}),
  }
  answers(monkeypatch, pages)
  remote.update([source()], tmp_path, lock)
  found = remote.read_records(lock)
  assert found["other/repo/mine.py"] == mine
  assert found["owner/name/one.py"]["commit"] == COMMIT


@pytest.mark.parametrize("stage", ["write", "replace"])
def test_failed_lock_write_keeps_previous_records(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
  """A failed lock write or replacement keeps the previous bytes and raises."""
  lock = tmp_path / "hooks.lock.json"
  records = {
    "one.py": {
      "sha256": "a" * 64,
      "version": "1.0",
      "repo": "owner/name",
      "commit": COMMIT,
    }
  }
  remote.write_records(records, lock)
  before = lock.read_bytes()
  write_text = Path.write_text

  def interrupted(path: Path, text: str, *args, **kwargs) -> int:
    write_text(path, text[:12], *args, **kwargs)
    raise OSError("interrupted lock write")

  def refused(path: Path, target: Path) -> Path:
    raise OSError("refused lock replacement")

  if stage == "write":
    monkeypatch.setattr(Path, "write_text", interrupted)
  else:
    monkeypatch.setattr(Path, "replace", refused)
  with pytest.raises(OSError):
    remote.write_records({}, lock)
  assert lock.read_bytes() == before
  assert remote.read_records(lock) == records
  assert list(tmp_path.iterdir()) == [lock]


def test_lock_replaces_a_complete_sibling(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The live lock keeps its bytes until a complete sibling replaces it."""
  lock = tmp_path / "hooks.lock.json"
  lock.write_text('{"old": "bytes"}')
  before = lock.read_bytes()
  replace = Path.replace
  calls = []

  def complete(path: Path, target: Path) -> Path:
    assert path != lock and path.parent == lock.parent
    assert target == lock and lock.read_bytes() == before
    assert json.loads(path.read_text()) == {}
    calls.append(path)
    return replace(path, target)

  monkeypatch.setattr(Path, "replace", complete)
  remote.write_records({}, lock)
  assert len(calls) == 1
  assert json.loads(lock.read_text()) == {}
  assert list(tmp_path.iterdir()) == [lock]


def test_records_round_trip(tmp_path: Path) -> None:
  """Lock records round-trip in name order, while malformed records and missing files return empty maps."""
  lock = tmp_path / "hooks.lock.json"
  one = {"sha256": "a" * 64, "version": "2.0", "repo": "owner/name", "commit": COMMIT}
  two = {"sha256": "b" * 64, "version": "", "repo": "owner/name", "commit": COMMIT}
  remote.write_records({"two.py": two, "one.py": one}, lock)
  assert list(json.loads(lock.read_text(encoding="utf-8"))) == ["one.py", "two.py"]
  assert remote.read_records(lock) == {"one.py": one, "two.py": two}
  lock.write_text(
    json.dumps({"one.py": {"sha256": "nope"}, "two.py": 7}), encoding="utf-8"
  )
  assert remote.read_records(lock) == {}
  assert remote.read_records(tmp_path / "gone.json") == {}


def test_on_disk_skips_a_temporary_name(tmp_path: Path) -> None:
  """`on_disk` keys a flat file by name and a nested one by identity, and skips dot names."""
  (tmp_path / "one.py").write_bytes(b"body\n")
  (tmp_path / ".one.py.part").write_bytes(b"half")
  (tmp_path / "owner/name").mkdir(parents=True)
  (tmp_path / "owner/name/two.py").write_bytes(b"more\n")
  (tmp_path / "owner/name/.two.py.part").write_bytes(b"half")
  (tmp_path / "hooks.lock.json").write_text("{}")
  (tmp_path / "notes.md").write_text("hook notes")
  (tmp_path / "owner/name/__pycache__").mkdir()
  (tmp_path / "owner/name/__pycache__/two.cpython-311.pyc").write_bytes(b"cache")
  (tmp_path / ".hidden").mkdir()
  (tmp_path / ".hidden/secret.py").write_bytes(b"hidden")
  assert remote.on_disk(tmp_path) == {
    "one.py": remote.digest(b"body\n"),
    "owner/name/two.py": remote.digest(b"more\n"),
  }
  assert remote.on_disk(tmp_path / "gone") == {}


def test_update_takes_the_named_files(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A take list writes those identities alone, and the other files of the archive stay out."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block(), "hooks/two.py": block(name="two")}),
  }
  answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  moved = remote.update([source()], tmp_path, lock, take=["owner/name/one.py"])
  assert moved == ["owner/name/one.py"]
  assert (tmp_path / "owner/name/one.py").is_file()
  assert not (tmp_path / "owner/name/two.py").exists()
  assert list(remote.read_records(lock)) == ["owner/name/one.py"]


def test_scan_names_the_files_of_a_source(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The scan returns frontmatter by identity and leaves the configured hook root empty."""
  monkeypatch.setattr(hooks, "DIR", str(tmp_path))
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar(
      {
        "hooks/one.py": block(),
        "hooks/two.py": block(name="two"),
        "hooks/bad.py": "# ---\n# surfaces: [on-later]\n# ---\n",
      }
    ),
  }
  calls = answers(monkeypatch, pages)
  found = remote.scan(source())
  assert found is not None
  assert found["repo"] == "owner/name"
  assert found["path"] == "hooks" and found["ref"] == "main"
  assert found["commit"] == COMMIT
  rows = {row["name"]: row for row in found["files"]}
  assert sorted(rows) == [
    "owner/name/bad.py",
    "owner/name/one.py",
    "owner/name/two.py",
  ]
  assert rows["owner/name/one.py"] == {
    "name": "owner/name/one.py",
    "version": "1.2.0",
    "scope": "global",
    "targets": [],
    "surfaces": ["on-answer"],
    "problem": "",
    "sha256": remote.digest(block().encode()),
  }
  assert "on-later" in rows["owner/name/bad.py"]["problem"]
  assert calls == [API, ARCHIVE]
  assert not list(tmp_path.iterdir()), "a scan writes nothing"


def test_scan_of_a_bad_source_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
  """A source that GitHub does not answer gives None."""
  answers(monkeypatch, {})
  assert remote.scan(source(repo="owner/none")) is None
  assert remote.scan({"repo": "nope"}) is None


def test_a_report_names_a_source_that_did_not_read(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The update names, in a report it fills, the source that read nothing."""
  answers(monkeypatch, {})
  report: list[str] = []
  assert (
    remote.update([source()], tmp_path, tmp_path / "hooks.lock.json", report=report)
    == []
  )
  assert any(line.startswith("source owner/name at main") for line in report)


def test_a_report_names_an_archive_without_the_folder(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """An archive with no .py file under the path names the folder in the report."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"other/four.py": block(name="four")}),
  }
  answers(monkeypatch, pages)
  report: list[str] = []
  assert (
    remote.update([source()], tmp_path, tmp_path / "hooks.lock.json", report=report)
    == []
  )
  assert any("held no .py file under 'hooks'" in line for line in report)


def test_a_report_names_a_refused_file(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A file whose block the reader refuses names its identity and its problem in the report."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": "# ---\n# bogus: 1\n# ---\n"}),
  }
  answers(monkeypatch, pages)
  report: list[str] = []
  assert (
    remote.update([source()], tmp_path, tmp_path / "hooks.lock.json", report=report)
    == []
  )
  assert any(
    "owner/name/one.py" in line and "unknown frontmatter key" in line for line in report
  )


def test_a_report_names_a_source_that_is_not_a_map(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A source entry that is not a map names itself in the report."""
  answers(monkeypatch, {})
  report: list[str] = []
  assert (
    remote.update(["owner/name"], tmp_path, tmp_path / "hooks.lock.json", report=report)
    == []
  )
  assert report == ["source 'owner/name' is not an owner/name or a GitHub URL"]


def test_the_base_example_never_installs(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """example.py of nemoe7/daedalus stays out, and the same name of another repo lands."""
  base_api = "https://api.github.com/repos/nemoe7/daedalus/commits/main"
  base_archive = f"https://codeload.github.com/nemoe7/daedalus/tar.gz/{COMMIT}"
  pages = {
    base_api: json.dumps({"sha": COMMIT}).encode(),
    base_archive: tar(
      {"hooks/example.py": block(name="example"), "hooks/one.py": block()}
    ),
  }
  answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  moved = remote.update([source(repo="nemoe7/daedalus")], tmp_path, lock)
  assert moved == ["nemoe7/daedalus/one.py"]
  assert not (tmp_path / "nemoe7/daedalus/example.py").exists()
  assert list(remote.read_records(lock)) == ["nemoe7/daedalus/one.py"]
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/example.py": block(name="example")}),
  }
  answers(monkeypatch, pages)
  assert remote.update([source()], tmp_path, lock) == ["owner/name/example.py"]


def test_the_lock_lives_in_the_hooks_folder(
  monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
  """The default lock path is the hooks folder, beside installed hook files."""
  (tmp_path / "hooks").mkdir()
  monkeypatch.setattr(hooks, "ROOT", Path("."))
  monkeypatch.chdir(tmp_path)
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block()}),
  }
  answers(monkeypatch, pages)
  remote.update([source()])
  assert (tmp_path / "hooks" / "hooks.lock.json").exists(), (
    "the lock rides in the hooks folder"
  )
  assert not (tmp_path / "hooks.lock.json").exists(), "no lock litters the start folder"
  assert (tmp_path / "hooks" / "owner" / "name" / "one.py").is_file()
  assert remote.read_records()["owner/name/one.py"]["repo"] == "owner/name"


def test_the_report_never_carries_exception_text(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The report names the step that failed, never the text of the exception."""

  def boom(url: str, **kwargs: object) -> None:
    raise remote.httpx.ConnectError("SECRET-LOAD")

  monkeypatch.setattr(remote.httpx, "get", boom)

  def refuse(self: Path, data: bytes) -> None:
    raise OSError("SECRET-WRITE")

  monkeypatch.setattr(Path, "write_bytes", refuse)
  report: list[str] = []
  remote.fetch(API, report=report)
  assert remote.write(tmp_path / "x.py", b"data", report) is False
  assert remote.archive_files(b"not-a-tar", "hooks", report) is None
  assert report == [
    f"{API} did not load",
    "x.py did not write",
    "the archive did not read",
  ]
  joined = "\n".join(report)
  assert "SECRET-LOAD" not in joined and "SECRET-WRITE" not in joined
