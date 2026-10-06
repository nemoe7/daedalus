"""The remote hook files: the fetch, the pin, and the last good copy."""

import json
from pathlib import Path

import httpx
import pytest

from daedalus.config import remote, settings

PIN = "a" * 64
BODY = b'"""A remote hook."""\n'
URL = "https://example.com/my_hook.py"


def entry(**payload: str) -> dict[str, str]:
  found = {"url": URL, "sha256": PIN}
  found.update(payload)
  return found


def matched(body: bytes = BODY, **payload: str) -> dict[str, str]:
  return entry(sha256=remote.digest(body), **payload)


def answer(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
  """A fetch that answers with the body."""
  monkeypatch.setattr(remote, "fetch", lambda url, timeout=remote.TIMEOUT: body)


def test_the_pin_names_the_bytes() -> None:
  """The digest is the sha256 of the body, in lowercase."""
  assert (
    remote.digest(b"")
    == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  )


def test_a_good_fetch_writes_the_file(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A body that matches the pin lands in the folder under the URL file name."""
  answer(monkeypatch, BODY)
  moved = remote.sync([matched()], tmp_path)
  assert moved == ["my_hook.py"]
  assert (tmp_path / "my_hook.py").read_bytes() == BODY
  assert not (tmp_path / ".my_hook.py.part").exists(), "no half file stays"


def test_a_name_of_the_entry_wins(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A `name` names the file, and a name with a separator never leaves the folder."""
  answer(monkeypatch, BODY)
  assert remote.sync([matched(name="mine.py")], tmp_path) == ["mine.py"]
  bad = matched(name="../escape.py")
  assert remote.sync([bad], tmp_path) == []
  assert not (tmp_path.parent / "escape.py").exists()


def test_a_mismatch_keeps_the_last_copy(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A body that misses the pin writes nothing, and the old file stays."""
  old = tmp_path / "my_hook.py"
  old.write_bytes(b"old body\n")
  answer(monkeypatch, BODY)
  assert remote.sync([entry()], tmp_path) == []
  assert old.read_bytes() == b"old body\n", "the last good file stays"


def test_a_failed_fetch_keeps_the_last_copy(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A network error keeps the old file and starts the process anyway."""
  old = tmp_path / "my_hook.py"
  old.write_bytes(b"old body\n")

  def fail(*args: object, **kwargs: object) -> bytes:
    raise httpx.ConnectError("no route")

  monkeypatch.setattr(remote.httpx, "get", fail)
  assert remote.sync([entry()], tmp_path) == []
  assert old.read_bytes() == b"old body\n"


def test_a_current_copy_skips_the_fetch(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A file that already matches the pin never goes to the network."""
  calls: list[str] = []

  def count(url: str, timeout: float = remote.TIMEOUT) -> bytes:
    calls.append(url)
    return BODY

  monkeypatch.setattr(remote, "fetch", count)
  (tmp_path / "my_hook.py").write_bytes(BODY)
  assert remote.sync([matched()], tmp_path) == [], "nothing moved"
  assert calls == [], "no fetch"


def test_sync_needs_a_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  """A list that holds junk skips it, so the boot survives a bad edit."""
  answer(monkeypatch, BODY)
  assert remote.sync(["nonsense", 7, None], tmp_path) == []


def test_the_group_takes_a_list_of_entries() -> None:
  """The settings group checks the URL, the pin and the name."""
  text = f"remote_hooks:\n  - url: {URL}\n    sha256: {'A' * 64}\n"
  found = settings.parse(text)
  assert found["remote_hooks"] == [{"url": URL, "sha256": PIN}]
  assert settings.parse("")["remote_hooks"] == []
  doubled = (
    "remote_hooks:\n"
    f"  - url: {URL}\n    sha256: {PIN}\n    name: same.py\n"
    f"  - url: {URL}\n    sha256: {PIN}\n    name: same.py\n"
  )
  for text, message in (
    ("remote_hooks: nope\n", "must be a list"),
    ("remote_hooks:\n  - url: nope\n    sha256: " + PIN + "\n", "http:// or https://"),
    (f"remote_hooks:\n  - url: {URL}\n    sha256: abc\n", "64 hex"),
    (
      f"remote_hooks:\n  - url: {URL}\n    sha256: {PIN}\n    extra: 1\n",
      "unknown key",
    ),
    (doubled, "own name"),
  ):
    with pytest.raises(settings.SettingsError, match=message):
      settings.parse(text)


def test_the_allowlist_gates_the_host() -> None:
  """Only a listed host passes, a wildcard covers the subdomains, and an empty list passes all."""
  assert remote.allowed("https://example.com/a.py", [])
  assert remote.allowed("https://example.com/a.py", ["example.com"])
  assert remote.allowed("https://EXAMPLE.com:8443/a.py", ["example.com."])
  assert remote.allowed("https://raw.example.com/a.py", ["*.example.com"])
  assert remote.allowed("https://example.com/a.py", ["*.example.com"])
  assert not remote.allowed("https://evil.test/a.py", ["example.com"])
  assert not remote.allowed("https://example.com.evil.test/a.py", ["*.example.com"])


def test_a_refused_host_never_goes_to_the_network(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """An entry outside the allowlist keeps the last copy and fetches nothing."""
  calls: list[str] = []

  def count(url: str, timeout: float = remote.TIMEOUT) -> bytes:
    calls.append(url)
    return BODY

  monkeypatch.setattr(remote, "fetch", count)
  old = tmp_path / "my_hook.py"
  old.write_bytes(b"old body\n")
  assert remote.sync([matched()], tmp_path, hosts=["other.test"]) == []
  assert calls == [], "no fetch"
  assert old.read_bytes() == b"old body\n"


def test_the_shape_check_warns_and_keeps_the_file(tmp_path: Path) -> None:
  """A file that does not compile is named, and it stays on disk."""
  (tmp_path / "broken.py").write_text("def broken(\n", encoding="utf-8")
  (tmp_path / "good.py").write_text(
    "def on_request(value):\n  return value\n", encoding="utf-8"
  )
  (tmp_path / "binary.py").write_bytes(b"\xff\xfe\x00")
  bad = remote.compile_check(
    [
      {"url": URL, "sha256": PIN, "name": name}
      for name in ("broken.py", "good.py", "binary.py", "gone.py")
    ],
    tmp_path,
  )
  assert bad == ["broken.py", "binary.py", "gone.py"]
  assert (tmp_path / "broken.py").exists(), "the file stays"


def test_the_lock_holds_the_digests(tmp_path: Path) -> None:
  """`on_disk` skips a temporary name, and the lock file reads back what it wrote."""
  (tmp_path / "one.py").write_bytes(BODY)
  (tmp_path / ".one.py.part").write_bytes(b"half")
  lock = tmp_path / "hooks.lock.json"
  assert remote.on_disk(tmp_path) == {"one.py": remote.digest(BODY)}
  remote.write_lock({"b.py": PIN, "a.py": "b" * 64}, lock)
  assert list(json.loads(lock.read_text(encoding="utf-8"))) == ["a.py", "b.py"]
  assert remote.read_lock(lock) == {"a.py": "b" * 64, "b.py": PIN}
  lock.write_text("nonsense", encoding="utf-8")
  assert remote.read_lock(lock) == {}
  assert remote.read_lock(tmp_path / "not-there.json") == {}


def test_the_group_takes_the_hosts() -> None:
  """The allowlist checks each host, keeps the wildcard, and refuses anything else."""
  text = "remote_hook_hosts:\n  - example.com.\n  - '*.GitHub.com'\n"
  assert settings.parse(text)["remote_hook_hosts"] == ["example.com", "*.github.com"]
  assert settings.parse("")["remote_hook_hosts"] == []
  for text, message in (
    ("remote_hook_hosts: nope\n", "must be a list"),
    ("remote_hook_hosts:\n  - https://example.com\n", "is not a host"),
    ("remote_hook_hosts:\n  - example.com/path\n", "is not a host"),
    ("remote_hook_hosts:\n  - ''\n", "must be a list"),
  ):
    with pytest.raises(settings.SettingsError, match=message):
      settings.parse(text)
