import sqlite3
from pathlib import Path

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
