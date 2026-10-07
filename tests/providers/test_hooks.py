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
  (hooks.CONFIG_DIR.parent / "outside.py").write_text("raise RuntimeError('ran')\n")
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
    "is not a file of the config folder",
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
  second = write(
    "req2.py",
    "def on_request(value, model, headers):\n"
    "  value['key'] = value['key'] + '!'\n"
    "  return value\n",
  )
  both = hooks.request_files({"on-request": [first, second]}, "on-request")
  assert both == [
    hooks.CONFIG_DIR / "hooks" / "req.py",
    hooks.CONFIG_DIR / "hooks" / "req2.py",
  ], "1 point takes many files"
  assert hooks.run_request(
    "on-request",
    {"on-request": [first, second]},
    "daedalus/auto",
    seen,
    headers={"x-chat": "c1"},
  ) == {"key": "c1:d1!", "digest": "d1"}, "the files run in list order"
  for entries in ({}, {"on-request": ""}, None, {"on-request": 5}, {"on-request": []}):
    assert hooks.request_files(entries, "on-request") == []
  assert hooks.run_request(
    "on-request", {"on-request": ["gone.py"]}, "daedalus/auto", {"key": None}
  ) == {"key": None}, "a file that does not load changes nothing"
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
    "on-request": [second, first],
    "on-init": third,
    "on-catalog": "",
  }
  assert hooks.init_rows(entries) == [
    ["rtN", "A repeat picked another model, N times"],
    ["x", "one"],
  ], "a row of 2 entries lands, other rows drop"
  assert hooks.init_rows({"on-request": broken}) == []
  assert hooks.init_rows({"on-answer": [second]}) == [], (
    "a file with no on_init adds no row"
  )
  assert hooks.init_rows(None) == [] and hooks.init_rows({"on-request": 5}) == []


def test_hook_files() -> None:
  """The picker lists the Python files of the hooks folder as config paths."""
  root = hooks.CONFIG_DIR / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  named = write("listed.py", "def on_answer(value, model, headers):\n  return value\n")
  listed = root / "listed.py"
  found = hooks.hook_files()
  assert "hooks/listed.py" in found, found
  assert found == sorted(found)
  assert all(name.endswith(".py") for name in found)
  (root / "notes.txt").write_text("not a hook\n", encoding="utf-8")
  assert "hooks/notes.txt" not in hooks.hook_files()
  assert named == "hooks/listed.py"
  listed.unlink()
  (root / "notes.txt").unlink()


def test_broken_hook_retry() -> None:
  """A hook file with an error does not cache None and reloads on fix."""
  target = hooks.CONFIG_DIR / "flaky.py"
  target.parent.mkdir(parents=True, exist_ok=True)
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


def test_a_run_writes_1_log_line(caplog: pytest.LogCaptureFixture) -> None:
  """Each hook that runs writes 1 line. The chunk point waits for DEBUG."""
  path = write(
    "told.py",
    "def on_answer(answer, model):\n  return {'v': 1}\n"
    "def on_chunk(chunk, model, context=None):\n  chunk['seen'] = True\n",
  )
  setup = config({"on-answer": path}, {"on-chunk": path})
  with caplog.at_level("INFO"):
    hooks.run("on-answer", setup, "p/m", {})
    hooks.run("on-chunk", setup, "p/m", {}, context={})
  assert "on-answer hook told.py for p/m: a new value" in caplog.text
  assert "on-chunk hook told.py for p/m" not in caplog.text
  with caplog.at_level("DEBUG"):
    hooks.run("on-chunk", setup, "p/m", {}, context={})
  assert "on-chunk hook told.py for p/m: edits in place" in caplog.text


def test_meta_block() -> None:
  """The frontmatter block gives the name, the version, the points, the scope and the targets."""
  path = hooks.CONFIG_DIR / "hooks" / "meta.py"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    "# ---\n"
    "# name: meta\n"
    "# version: 1.3.0\n"
    "# points: [on-chunk, on-answer]\n"
    "# scope: provider\n"
    "# targets: [openrouter]\n"
    "# ---\n"
    "def on_chunk(chunk, model, context=None):\n  chunk['seen'] = True\n",
    encoding="utf-8",
  )
  assert hooks.meta(path) == {
    "name": "meta",
    "version": "1.3.0",
    "points": ["on-chunk", "on-answer"],
    "scope": "provider",
    "targets": ["openrouter"],
  }
  assert hooks.meta_problem(path) is None


def test_meta_absent_and_defaults() -> None:
  """A file with no block has no problem, and the loader keeps its file name."""
  path = hooks.CONFIG_DIR / "hooks" / "plain.py"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    "def on_answer(answer, model):\n  return {'v': 1}\n", encoding="utf-8"
  )
  assert hooks.meta(path) is None
  assert hooks.meta_problem(path) is None


def test_meta_problems() -> None:
  """A bad point, a bad scope, a target-less scope and a new version stop the file."""
  cases = (
    ("# ---\n# points: [on-later]\n# ---\n", "on-later"),
    ("# ---\n# scope: pool\n# ---\n", "pool"),
    ("# ---\n# scope: model\n# ---\n", "targets"),
    ('# ---\n# requires: ">=99.0"\n# ---\n', "99.0"),
    ("# ---\n# version: [1]\n# ---\n", "version"),
  )
  for text, part in cases:
    path = hooks.CONFIG_DIR / "hooks" / "bad.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "def on_answer(a, m):\n  return {}\n", encoding="utf-8")
    problem = hooks.meta_problem(path)
    assert problem and part in problem, (part, problem)
    assert hooks.meta(path) is None


def test_meta_runs_no_code() -> None:
  """The reader takes the block from the text, so the body runs at no read."""
  path = hooks.CONFIG_DIR / "hooks" / "danger.py"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    "# ---\n# name: danger\n# version: 1\n# ---\nraise RuntimeError('ran')\n",
    encoding="utf-8",
  )
  assert hooks.meta(path) == {"name": "danger", "version": "1"}
  assert hooks.meta_problem(path) is None


def test_requires_compare() -> None:
  """A requirement takes a comma list of comparisons against the running version."""
  assert hooks.requires_ok(">=0.1", "0.1.0") is True
  assert hooks.requires_ok(">=0.2", "0.1.0") is False
  assert hooks.requires_ok(">=0.1,<1.0", "0.1.0") is True
  assert hooks.requires_ok("==0.1.0", "0.1.0") is True
  assert hooks.requires_ok(">=x", "0.1.0") is False


def test_hook_folder() -> None:
  """The settings name the folder of the hook files, and the picker follows it."""
  try:
    hooks.set_installed("shared", [])
    path = hooks.CONFIG_DIR / "shared" / "one.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
      "def on_answer(answer, model):\n  return {'v': 1}\n", encoding="utf-8"
    )
    assert hooks.resolve("shared/one.py") == path
    assert hooks.hook_files() == ["shared/one.py"]
  finally:
    hooks.set_installed()


def scoped(name: str, body: str, block: str) -> None:
  """Write 1 hook file with a frontmatter block in the hook folder."""
  path = hooks.CONFIG_DIR / hooks.DIR / name
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(f"{block}{body}", encoding="utf-8")


ANSWER = "def on_answer(answer, model):\n  answer.setdefault('by', []).append('%s')\n"


def test_scoped_hooks() -> None:
  """A file with a block runs for the models of its scope, and a disabled name stays out."""
  try:
    hooks.set_installed("scoped", [])
    scoped("all.py", ANSWER % "all", "# ---\n# points: [on-answer]\n# ---\n")
    scoped(
      "or.py",
      ANSWER % "or",
      "# ---\n# scope: provider\n# targets: [openrouter]\n# points: [on-answer]\n# ---\n",
    )
    scoped(
      "mine.py",
      ANSWER % "mine",
      "# ---\n# scope: model\n# targets: [p/m]\n# points: [on-answer]\n# ---\n",
    )
    scoped("plain.py", ANSWER % "plain", "# ---\n# points: [on-answer]\n# ---\n")
    assert hooks.run("on-answer", {}, "openrouter/x", {}).get("by") == [
      "all",
      "or",
      "plain",
    ]
    assert hooks.run("on-answer", {}, "p/m", {}).get("by") == ["all", "mine", "plain"]
    assert hooks.run("on-answer", {}, "other/m", {}).get("by") == ["all", "plain"]
    hooks.set_installed("scoped", ["all.py", "plain"])
    assert hooks.run("on-answer", {}, "openrouter/x", {}).get("by") == ["or"]
  finally:
    hooks.set_installed()


def test_scoped_request_hook() -> None:
  """A request-level file with a provider scope runs for that provider only."""
  try:
    hooks.set_installed("reqscope", [])
    scoped(
      "req.py",
      "def on_request(value, model, headers):\n  value['key'] = 'set'\n",
      "# ---\n# scope: provider\n# targets: [openrouter]\n# points: [on-request]\n# ---\n",
    )
    assert hooks.run_request(
      "on-request", {}, "openrouter/x", {"key": None}, headers={}
    ) == {"key": "set"}
    assert hooks.run_request("on-request", {}, "p/m", {"key": None}, headers={}) == {
      "key": None
    }
  finally:
    hooks.set_installed()


def test_explicit_before_installed() -> None:
  """The named files run first, and the installed files follow in name order."""
  try:
    hooks.set_installed("order", [])
    named = hooks.CONFIG_DIR / "order" / "named.py"
    named.parent.mkdir(parents=True, exist_ok=True)
    named.write_text(ANSWER % "named", encoding="utf-8")
    scoped("b.py", ANSWER % "b", "# ---\n# points: [on-answer]\n# ---\n")
    scoped("a.py", ANSWER % "a", "# ---\n# points: [on-answer]\n# ---\n")
    setup = {"p": {"api_key": "k", "hooks": [{"on-answer": "order/named.py"}]}}
    found = hooks.run("on-answer", setup, "p/m", {})
    assert found["by"] == ["named", "a", "b"]
  finally:
    hooks.set_installed()


def test_bad_block_stays_out() -> None:
  """A block with an unknown point keeps the file out of the installed list."""
  try:
    hooks.set_installed("badblock", [])
    scoped(
      "bad.py",
      "def on_answer(answer, model):\n  answer['bad'] = True\n",
      "# ---\n# points: [on-later]\n# ---\n",
    )
    assert hooks.run("on-answer", {}, "p/m", {}) == {}
  finally:
    hooks.set_installed()


def test_rows_add_the_request_points_that_name_a_file() -> None:
  """The settings name a file at a request point: the row shows that point once, in point order."""
  try:
    hooks.set_installed("rows2")
    scoped("both.py", ANSWER % "both", "# ---\n# points: [on-chunk]\n# ---\n")
    scoped("named.py", ANSWER % "named", "")
    rows = {
      row["name"]: row
      for row in hooks.rows(
        {
          "on-prompt": ["rows2/both.py", "rows2/named.py"],
          "on-chunk": ["rows2/both.py"],
        }
      )
    }
    assert rows["both.py"]["runs"] == ["on-chunk"], "the block admits on-chunk alone"
    assert rows["both.py"]["points"] == ["on-chunk"], "the block is the declaration"
    assert rows["named.py"]["runs"] == ["on-prompt"], rows["named.py"]
    assert "runs" not in hooks.rows()[0], "no settings gives no named point"
  finally:
    hooks.set_installed()


def test_rows_report_the_folder() -> None:
  """Each row names the file, its frontmatter, its state and its problem."""
  try:
    hooks.set_installed("rows", ["off.py"])
    scoped(
      "on.py",
      ANSWER % "on",
      "# ---\n# version: 2.0\n# scope: provider\n# targets: [p]\n"
      "# points: [on-answer]\n# ---\n",
    )
    scoped("off.py", ANSWER % "off", "# ---\n# points: [on-answer]\n# ---\n")
    scoped("plain.py", ANSWER % "plain", "")
    scoped("bad.py", ANSWER % "bad", "# ---\n# points: [on-later]\n# ---\n")
    found = {row["name"]: row for row in hooks.rows()}
    assert found["on.py"] == {
      "name": "on.py",
      "path": "rows/on.py",
      "version": "2.0",
      "scope": "provider",
      "targets": ["p"],
      "points": ["on-answer"],
      "enabled": True,
      "problem": "",
    }
    assert found["off.py"]["enabled"] is False
    assert found["plain.py"]["version"] == "" and found["plain.py"]["enabled"] is True
    assert found["bad.py"]["enabled"] is False
    assert "on-later" in found["bad.py"]["problem"]
  finally:
    hooks.set_installed()
