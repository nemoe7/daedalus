"""The hook sources: the ref, the archive, the written files and the lock record."""

import io
import json
import tarfile
from pathlib import Path

import pytest

from daedalus.config import remote

COMMIT = "a" * 40
API = "https://api.github.com/repos/owner/name/commits/main"
ARCHIVE = f"https://codeload.github.com/owner/name/tar.gz/{COMMIT}"


def block(version: str = "1.2.0", name: str = "one") -> str:
  """A hook file with a frontmatter block."""
  return (
    "# ---\n"
    f"# name: {name}\n"
    f"# version: {version}\n"
    "# points: [on-answer]\n"
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

  def get(url: str, timeout: float = remote.TIMEOUT) -> bytes | None:
    calls.append(url)
    return pages.get(url)

  monkeypatch.setattr(remote, "fetch", get)
  return calls


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


def test_a_source_writes_its_files(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The archive of the ref lands in the folder, and only the `.py` files under the path."""
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
  assert moved == ["one.py", "two.py"]
  assert (tmp_path / "one.py").read_text(encoding="utf-8") == block()
  assert not (tmp_path / "three.py").exists()
  assert not (tmp_path / "notes.md").exists()
  assert not (tmp_path / "four.py").exists()
  assert list(tmp_path.glob(".*")) == [], "no half file stays"


def test_the_lock_records_the_source(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The lock holds the sha256, the version, the repo and the commit of each file."""
  body = block()
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": body}),
  }
  answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  remote.update([source()], tmp_path, lock)
  assert remote.read_records(lock) == {
    "one.py": {
      "sha256": remote.digest(body.encode("utf-8")),
      "version": "1.2.0",
      "repo": "owner/name",
      "commit": COMMIT,
    }
  }


def test_a_bad_block_stays_out(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  """A block with an unknown point or an unmet requires never lands, with 1 warning a file."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar(
      {
        "hooks/good.py": block(),
        "hooks/later.py": "# ---\n# points: [on-later]\n# ---\n",
        "hooks/new.py": '# ---\n# requires: ">=99"\n# ---\n',
      }
    ),
  }
  answers(monkeypatch, pages)
  with caplog.at_level("WARNING"):
    moved = remote.update([source()], tmp_path, tmp_path / "hooks.lock.json")
  assert moved == ["good.py"]
  assert not (tmp_path / "later.py").exists()
  assert not (tmp_path / "new.py").exists()
  assert "later.py" in caplog.text
  assert "new.py" in caplog.text


def test_no_block_still_installs(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  """A fetched file with no block lands, with an empty version and 1 line that asks for a block."""
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
  assert moved == ["plain.py"]
  assert remote.read_records(lock)["plain.py"]["version"] == ""
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
  (tmp_path / "one.py").write_text(block(), encoding="utf-8")
  remote.write_records({"one.py": kept}, lock)
  before = lock.read_text(encoding="utf-8")
  answers(monkeypatch, {})
  assert remote.update([source()], tmp_path, lock) == []
  assert (tmp_path / "one.py").read_text(encoding="utf-8") == block()
  assert lock.read_text(encoding="utf-8") == before
  assert remote.read_records(lock)["one.py"] == kept


def test_a_bad_archive_keeps_the_files(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """An archive that does not read writes nothing, and the file and the record stay."""
  lock = tmp_path / "hooks.lock.json"
  (tmp_path / "one.py").write_text(block(), encoding="utf-8")
  remote.write_records(
    {
      "one.py": {
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
  assert (tmp_path / "one.py").read_text(encoding="utf-8") == block()
  assert lock.read_text(encoding="utf-8") == before


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
  (tmp_path / "one.py").unlink()
  calls.clear()
  assert remote.update([source()], tmp_path, lock, only_missing=True) == ["one.py"]
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
  assert (tmp_path / "two.py").exists(), "the file stays on disk"
  found = remote.read_records(lock)
  assert found["two.py"]["repo"] == "owner/name"
  assert found["two.py"]["commit"] == COMMIT, "the record keeps its own commit"
  assert found["one.py"]["commit"] == second


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
  remote.write_records({"mine.py": mine}, lock)
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block()}),
  }
  answers(monkeypatch, pages)
  remote.update([source()], tmp_path, lock)
  found = remote.read_records(lock)
  assert found["mine.py"] == mine
  assert found["one.py"]["commit"] == COMMIT


def test_records_round_trip(tmp_path: Path) -> None:
  """The lock writes in name order, reads back its records, and keeps 1 bad record out."""
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
  """`on_disk` holds the sha256 of each file, and a name that starts with a dot stays out."""
  (tmp_path / "one.py").write_bytes(b"body\n")
  (tmp_path / ".one.py.part").write_bytes(b"half")
  assert remote.on_disk(tmp_path) == {"one.py": remote.digest(b"body\n")}
  assert remote.on_disk(tmp_path / "gone") == {}


def test_update_takes_the_named_files(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A take list writes those names alone, and the other files of the archive stay out."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar({"hooks/one.py": block(), "hooks/two.py": block(name="two")}),
  }
  answers(monkeypatch, pages)
  lock = tmp_path / "hooks.lock.json"
  moved = remote.update([source()], tmp_path, lock, take=["one.py"])
  assert moved == ["one.py"]
  assert (tmp_path / "one.py").is_file() and not (tmp_path / "two.py").exists()
  assert list(remote.read_records(lock)) == ["one.py"]


def test_scan_names_the_files_of_a_source(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The scan answers with the frontmatter of each file and writes nothing."""
  pages = {
    API: json.dumps({"sha": COMMIT}).encode(),
    ARCHIVE: tar(
      {
        "hooks/one.py": block(),
        "hooks/two.py": block(name="two"),
        "hooks/bad.py": "# ---\n# points: [on-later]\n# ---\n",
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
  assert sorted(rows) == ["bad.py", "one.py", "two.py"]
  assert rows["one.py"] == {
    "name": "one.py",
    "version": "1.2.0",
    "scope": "global",
    "targets": [],
    "points": ["on-answer"],
    "problem": "",
    "sha256": remote.digest(block().encode()),
  }
  assert "on-later" in rows["bad.py"]["problem"]
  assert calls == [API, ARCHIVE]
  assert not list(tmp_path.iterdir()), "a scan writes nothing"


def test_scan_of_a_bad_source_gives_none(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A source that GitHub does not answer gives None."""
  answers(monkeypatch, {})
  assert remote.scan(source(repo="owner/none")) is None
  assert remote.scan({"repo": "nope"}) is None
