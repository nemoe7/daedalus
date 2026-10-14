import sqlite3
from pathlib import Path

import pytest

from daedalus.store import saved_env


def test_saved_env(tmp_path: Path) -> None:
  target = tmp_path / "models.sqlite3"
  assert saved_env.read(target) == {} and not target.exists(), "a read makes no file"
  sqlite3.connect(target).close()
  assert saved_env.read(target) == {}, "a file without the table"
  saved_env.save(target, "GROQ_API_KEY", "gsk-first")
  saved_env.save(target, "GROQ_API_KEY", "gsk-second")
  saved_env.save(target, "CLOUDFLARE_ACCOUNT_ID", "abc")
  assert saved_env.read(target) == {
    "GROQ_API_KEY": "gsk-second",
    "CLOUDFLARE_ACCOUNT_ID": "abc",
  }
  assert saved_env.clear(target, "GROQ_API_KEY") is True
  assert saved_env.clear(target, "GROQ_API_KEY") is False, "no value to clear"
  assert saved_env.read(target) == {"CLOUDFLARE_ACCOUNT_ID": "abc"}


@pytest.mark.parametrize(
  "name",
  [
    "plain.sqlite3",
    "hash#state.sqlite3",
    "query?state.sqlite3",
    "percent%20state.sqlite3",
  ],
)
@pytest.mark.parametrize("relative", [False, True])
def test_saved_uri_path(tmp_path: Path, monkeypatch, name: str, relative: bool) -> None:
  monkeypatch.chdir(tmp_path)
  path = Path(name) if relative else tmp_path / name
  assert saved_env.read(path) == {}
  assert not list(tmp_path.iterdir())
  saved_env.save(path, "EXAMPLE", "placeholder")
  files = set(tmp_path.iterdir())
  assert saved_env.read(path) == {"EXAMPLE": "placeholder"}
  assert set(tmp_path.iterdir()) == files
