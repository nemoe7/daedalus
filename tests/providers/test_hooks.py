"""Provider hooks from the `hooks` list change a copy of the request or answer, and a failure keeps the value that came in."""

import os
import time

import pytest

from daedalus.providers import hooks


def write(name: str, text: str) -> str:
  """Write a hook file in the config folder with a new file time, and give its config path."""
  path = hooks.CONFIG_DIR / "hooks" / name
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)
  stamp = time.time() + len(hooks._loaded) + 1
  os.utime(path, (stamp, stamp))
  return f"hooks/{name}"


def config(*entries: dict) -> dict:
  return {"p": {"api_key": "k", "hooks": list(entries)}}


def test_no_hooks() -> None:
  """Without a hooks list, the value stays the same object."""
  body = {"model": "m"}
  assert hooks.run("on-upstream", {"p": {}}, "p/m", body, headers={}) is body
  assert hooks.run("on-upstream", {}, "none/m", body, headers={}) is body


def test_upstream_hooks_in_order() -> None:
  """Each hook of the point runs in list order on a copy. Any file name works."""
  first = write(
    "add_user",
    "def on_upstream(body, model, headers):\n"
    "  body['messages'].append({'role': 'user', 'content': model})\n"
    "  headers['x-extra'] = '1'\n",
  )
  second = write(
    "count.txt",
    "def on_upstream(body, model, headers):\n  body['n'] = len(body['messages'])\n",
  )
  answer = write("answer.py", "def on_answer(answer, model):\n  return {'new': True}\n")
  setup = config({"on-upstream": first}, {"on-answer": answer}, {"on-upstream": second})
  body = {"messages": [{"role": "user", "content": "hi"}]}
  headers: dict[str, str] = {}
  found = hooks.run("on-upstream", setup, "p/m", body, headers=headers)
  assert found["messages"][-1] == {"role": "user", "content": "p/m"}
  assert found["n"] == 2, "the second hook sees the change of the first"
  assert len(body["messages"]) == 1, "the value that came in stays the same"
  assert headers == {"x-extra": "1"}
  assert hooks.run("on-answer", setup, "p/m", {"old": True}) == {"new": True}


def test_reload() -> None:
  """A changed file loads again."""
  path = write("r.py", "def on_answer(answer, model):\n  return {'v': 1}\n")
  assert hooks.run("on-answer", config({"on-answer": path}), "p/m", {}) == {"v": 1}
  write("r.py", "def on_answer(answer, model):\n  return {'v': 2}\n")
  assert hooks.run("on-answer", config({"on-answer": path}), "p/m", {}) == {"v": 2}


def test_failures(caplog: pytest.LogCaptureFixture) -> None:
  """A failure keeps the value, and the next hook still runs."""
  body = {"k": 1}
  boom = write(
    "boom.py", "def on_upstream(body, model, headers):\n  raise RuntimeError('boom')\n"
  )
  text = write("text.py", "def on_upstream(body, model, headers):\n  return 'text'\n")
  broken = write("broken.py", "def on_upstream(:\n")
  other = write("other.py", "def on_answer(answer, model):\n  return None\n")
  good = write("good.py", "def on_upstream(body, model, headers):\n  body['k'] = 2\n")
  (hooks.CONFIG_DIR.parent / "outside.py").write_text("raise SystemExit('ran')\n")
  setup = config(
    {"on-upstream": boom},
    {"on-upstream": text},
    {"on-upstream": broken},
    {"on-upstream": other},
    {"on-upstream": "hooks/missing.py"},
    {"on-upstream": "../outside.py"},
    {"on-upstream": 5},
    {"on-later": good},
    "not a dict",
    {"on-upstream": good},
  )
  assert hooks.run("on-upstream", setup, "p/m", body, headers={}) == {"k": 2}
  assert body == {"k": 1}
  for part in (
    "boom",
    "returned no dict",
    "did not load",
    "has no on_upstream function",
    "is not there",
    "outside the config folder",
    "is not a file path",
    "unknown point on-later",
    "each item needs a point",
  ):
    assert part in caplog.text, part
  bad = {"p": {"hooks": {"on-upstream": good}}}
  assert hooks.run("on-upstream", bad, "p/m", body, headers={}) is body
  assert "must be a list" in caplog.text
  with pytest.raises(ValueError):
    hooks.run("unknown", setup, "p/m", body)


def test_levels() -> None:
  """Like `order`: a models entry wins, then the block that owns the model. An empty list turns hooks off."""
  mark = write(
    "mark.py",
    "def on_answer(answer, model):\n  answer.setdefault('by', []).append('main')\n",
  )
  own = write(
    "own.py",
    "def on_answer(answer, model):\n  answer.setdefault('by', []).append('file')\n",
  )
  entry = write(
    "entry.py",
    "def on_answer(answer, model):\n  answer.setdefault('by', []).append('model')\n",
  )
  setup = {
    "p": {
      "api_key": "k",
      "hooks": [{"on-answer": mark}],
      "models": {
        "plain": {},
        "off": {"hooks": []},
        "one*": {"hooks": [{"on-answer": entry}]},
      },
      "_file": {
        "hooks": [{"on-answer": own}],
        "models": {"filed": {}, "filed-model": {"hooks": [{"on-answer": entry}]}},
      },
    }
  }
  found = {
    slug: hooks.run("on-answer", setup, f"p/{slug}", {}).get("by")
    for slug in ("plain", "off", "one-a", "filed", "filed-model")
  }
  assert found == {
    "plain": ["main"],
    "off": None,
    "one-a": ["model"],
    "filed": ["file"],
    "filed-model": ["model"],
  }


def test_request_hooks() -> None:
  """A request hook comes from the settings group, runs on a copy, and an empty value turns it off."""
  first = write(
    "req.py",
    "def on_request(value, model, headers):\n"
    "  value['key'] = headers['x-chat'] + ':' + value['digest']\n"
    "  return value\n",
  )
  seen: dict = {"key": None, "digest": "d1"}
  found = hooks.run_request(
    "on-request", {"on-request": first}, "daedalus/auto", seen, headers={"x-chat": "c1"}
  )
  assert found == {"key": "c1:d1", "digest": "d1"}
  assert seen == {"key": None, "digest": "d1"}, "the hook changes a copy"
  assert hooks.request_files({"on-request": first}, "on-request") == [
    hooks.CONFIG_DIR / "hooks" / "req.py"
  ]
  for entries in ({}, {"on-request": ""}, None, {"on-request": 5}):
    assert hooks.request_files(entries, "on-request") == []
  plain = {"key": None}
  assert hooks.run_request("on-request", {}, "daedalus/auto", plain) is plain


def test_init_rows() -> None:
  """The legend rows come from each enabled file that defines on_init, and bad rows drop."""
  first = write(
    "legend-one.py",
    "def on_init():\n  return [['rtN', 'A repeat picked another model, N times']]\n",
  )
  second = write(
    "legend-two.py", "def on_request(value, model, headers):\n  return value\n"
  )
  third = write(
    "legend-three.py",
    "def on_init():\n"
    "  return [['x', 'one'], 'junk', ['y', 'two', 'extra'], ['z'], 7]\n",
  )
  broken = write("legend-broken.py", "def on_init():\n  raise ValueError('no')\n")
  entries = {
    "on-request": first,
    "on-init": third,
    "on-answer": second,
    "on-catalog": "",
  }
  assert hooks.init_rows(entries) == [
    ["rtN", "A repeat picked another model, N times"],
    ["x", "one"],
  ], "a row of 2 entries lands, other rows drop"
  assert hooks.init_rows({"on-request": broken}) == []
  assert hooks.init_rows(None) == [] and hooks.init_rows({"on-request": 5}) == []


def test_broken_hook_retry() -> None:
  """A hook file with an error does not cache None and reloads on fix."""
  target = hooks.CONFIG_DIR / "flaky.py"
  target.write_text("def on_answer(:\n")
  assert hooks.load(target) is None
  target.write_text("def on_answer(a, m):\n  return {'ok': True}\n")
  loaded = hooks.load(target)
  assert loaded is not None
  assert loaded.on_answer({}, "m") == {"ok": True}


def test_chunk_hook_changes_the_copy() -> None:
  """The on-chunk point runs with its context on each stream chunk, and the value that came in stays."""
  path = write(
    "mark.py",
    "def on_chunk(chunk, model, context=None):\n"
    "  chunk['seen'] = [model, context['attempts']]\n",
  )
  setup = config({"on-chunk": path})
  chunk = {"choices": []}
  found = hooks.run("on-chunk", setup, "p/m", chunk, context={"attempts": 2})
  assert found == {"choices": [], "seen": ["p/m", 2]}
  assert chunk == {"choices": []}, "the value that came in stays the same"
