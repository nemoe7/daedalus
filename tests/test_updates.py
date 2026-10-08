"""The update check: the channel of the version, the GitHub reads, the stored row."""

import httpx

from daedalus import store, updates

SHA = "a" * 40
HEAD = "b" * 40


def test_channel() -> None:
  assert updates.channel("v1.2.3") == "release"
  assert updates.channel("dev-abc1234") == "dev"
  assert updates.channel("dev") == "dev"
  assert updates.channel("demo.17") == "dev"


def test_check_release_newer() -> None:
  calls = []

  def fetch(url: str) -> dict:
    calls.append(url)
    return {
      "tag_name": "v1.3.0",
      "html_url": "https://github.com/o/r/releases/tag/v1.3.0",
    }

  found = updates.check("o/r", "v1.2.1", fetch=fetch)
  assert found["channel"] == "release"
  assert found["latest"] == "v1.3.0"
  assert found["url"] == "https://github.com/o/r/releases/tag/v1.3.0"
  assert found["update"] is True
  assert calls == ["https://api.github.com/repos/o/r/releases/latest"]


def test_check_release_same() -> None:
  found = updates.check("o/r", "v1.2.0", fetch=lambda url: {"tag_name": "v1.2.0"})
  assert found["latest"] == "v1.2.0" and found["update"] is False


def test_check_dev_behind() -> None:
  def fetch(url: str) -> dict:
    if url.endswith("/commits/main"):
      return {"sha": HEAD}
    assert url == f"https://api.github.com/repos/o/r/compare/{SHA}...{HEAD}"
    return {"behind_by": 4}

  found = updates.check("o/r", f"dev-{SHA}", fetch=fetch)
  assert found["channel"] == "dev"
  assert found["behind"] == 4 and found["update"] is True


def test_check_dev_current() -> None:
  found = updates.check("o/r", f"dev-{SHA}", fetch=lambda url: {"sha": SHA})
  assert found["behind"] is None and found["update"] is False


def test_check_dev_without_sha() -> None:
  found = updates.check("o/r", "demo.17", fetch=lambda url: None)
  assert found["error"] and found["update"] is False and found["behind"] is None


def test_check_failure() -> None:
  def fetch(url: str) -> dict:
    raise httpx.ConnectError("down")

  found = updates.check("o/r", "v1.0.0", fetch=fetch)
  assert found["error"] and found["latest"] is None and found["update"] is False


def test_save_and_read() -> None:
  store.migrate()
  assert updates.read() is None
  row = {
    "repo": "o/r",
    "current": "v1.0.0",
    "channel": "release",
    "latest": "v1.1.0",
    "url": None,
    "behind": None,
    "update": True,
    "error": None,
  }
  updates.save(row)
  got = updates.read()
  assert got["repo"] == "o/r" and got["latest"] == "v1.1.0" and got["at"] > 0


def test_due() -> None:
  assert updates.due(1000.0, None), "without a check"
  assert not updates.due(1000.0, 1000.0), "a fresh check"
  assert not updates.due(1000.0 + 3600, 1000.0), "within the period"
  assert updates.due(1000.0 + 6 * 3600, 1000.0), "after the period"
