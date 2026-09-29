"""Provider hook files change a copy of the request or answer, and a failure keeps the value that came in."""

import os
import time

import pytest

from daedalus.providers import hooks


def write(name: str, text: str) -> None:
  """Write a hook file with a new file time, so that it loads again."""
  hooks.HOOKS_DIR.mkdir(parents=True, exist_ok=True)
  path = hooks.HOOKS_DIR / f"{name}.py"
  path.write_text(text)
  stamp = time.time() + len(hooks._loaded)
  os.utime(path, (stamp, stamp))


def test_no_file() -> None:
  """Without a hook file, the value stays the same object."""
  body = {"model": "m"}
  assert hooks.run("request", "none/m", body, headers={}) is body


def test_request_hook() -> None:
  """The hook changes a copy in place, and the headers dict too."""
  write(
    "p",
    "def request(body, model, headers):\n"
    "  body['messages'].append({'role': 'user', 'content': model})\n"
    "  headers['x-extra'] = '1'\n",
  )
  body = {"messages": [{"role": "user", "content": "hi"}]}
  headers: dict[str, str] = {}
  found = hooks.run("request", "p/m", body, headers=headers)
  assert found["messages"][-1] == {"role": "user", "content": "p/m"}
  assert len(body["messages"]) == 1, "the value that came in stays the same"
  assert headers == {"x-extra": "1"}
  assert hooks.run("answer", "p/m", {"a": 1}) == {"a": 1}, "no answer hook"


def test_new_value_and_reload() -> None:
  """A returned dict replaces the value, and a changed file loads again."""
  write("q", "def answer(answer, model):\n  return {'new': True}\n")
  assert hooks.run("answer", "q/m", {"old": True}) == {"new": True}
  write("q", "def answer(answer, model):\n  return {'newer': True}\n")
  assert hooks.run("answer", "q/m", {"old": True}) == {"newer": True}


def test_failures(caplog: pytest.LogCaptureFixture) -> None:
  """An error, a value that is not a dict or a file that does not load keeps the value."""
  body = {"k": 1}
  write("r", "def request(body, model, headers):\n  raise RuntimeError('boom')\n")
  assert hooks.run("request", "r/m", body, headers={}) is body
  write("r", "def request(body, model, headers):\n  return 'text'\n")
  assert hooks.run("request", "r/m", body, headers={}) is body
  write("r", "def request(:\n")
  assert hooks.run("request", "r/m", body, headers={}) is body
  assert "boom" in caplog.text and "did not load" in caplog.text
  with pytest.raises(ValueError):
    hooks.run("unknown", "r/m", body)
