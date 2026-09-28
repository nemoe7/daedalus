import json

from daedalus.routing import loops

LINE = "Let me check the file again to be sure. "


def call(identifier: str, name: str = "run", arguments: object = None) -> dict:
  text = json.dumps(arguments if arguments is not None else {"code": "print(1)"})
  return {
    "id": identifier,
    "type": "function",
    "function": {"name": name, "arguments": text},
  }


def rounds(*calls: dict) -> list[dict]:
  messages: list[dict] = [{"role": "user", "content": "go"}]
  for item in calls:
    messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
    messages.append({"role": "tool", "tool_call_id": item["id"], "content": "ok"})
  return messages


def test_repeated_call() -> None:
  assert loops.repeated_call(rounds(call("c1"), call("c2"))) is None
  assert loops.repeated_call(rounds(call("c1"), call("c2"), call("c3"))) == ("c3", 3)
  assert loops.repeated_call(
    rounds(call("c1"), call("c2"), call("c3"), call("c4"))
  ) == (
    "c4",
    4,
  )
  changed = [call("c1"), call("c2"), call("c3", arguments={"code": "print(2)"})]
  assert loops.repeated_call(rounds(*changed)) is None, "new arguments are no loop"
  other = [call("c1"), call("c2"), call("c3", name="search")]
  assert loops.repeated_call(rounds(*other)) is None, "another tool is no loop"
  last = [call("c1"), call("c2"), call("c3"), call("c4", name="search")]
  assert loops.repeated_call(rounds(*last)) is None, "the last call must repeat"


def test_repeated_call_since_user() -> None:
  messages = rounds(call("c1"), call("c2"))
  messages.append({"role": "user", "content": "again"})
  messages += rounds(call("c3"))[1:]
  assert loops.repeated_call(messages) is None, "a user message starts a new count"
  assert loops.repeated_call([{"role": "user", "content": "hi"}]) is None


def test_repeated_call_argument_order() -> None:
  first = call("c1", arguments={"a": 1, "b": 2})
  second = call("c2", arguments={"b": 2, "a": 1})
  third = call("c3", arguments={"a": 1, "b": 2})
  assert loops.repeated_call(rounds(first, second, third)) == ("c3", 3)


def test_repeats_finds_a_loop() -> None:
  repeats = loops.Repeats()
  found = [repeats.feed(piece) for piece in ["Intro. ", LINE, LINE, LINE]]
  assert found == [None, None, None, None]
  assert repeats.feed(LINE) == len(LINE)


def test_repeats_small_pieces() -> None:
  repeats, text = loops.Repeats(), "Start. " + LINE * 4
  found = [repeats.feed(text[i : i + 3]) for i in range(0, len(text), 3)]
  first = next(i for i, value in enumerate(found) if value is not None)
  # The text before the loop ends like the passage, so the loop can show 1 piece early.
  assert first >= len(found) - 2 and found[first] == len(LINE), found


def test_repeats_ignores_short_runs() -> None:
  for text in ("=" * 400, "0, " * 200, "| - | - |\n" * 40, "ab" * 300):
    assert loops.text_loop(text) is None, text[:20]
  assert loops.text_loop("Some text. " + LINE * 3) is None
  assert loops.text_loop("Some text. " + LINE * 4) == len(LINE)


def test_repeats_long_passage() -> None:
  passage = " ".join(f"word{i}" for i in range(150)) + ". "
  assert 1000 < len(passage) <= loops.LONGEST
  assert loops.text_loop(passage * 4) == len(passage)
  assert loops.text_loop("x" + "y" * 3000 + "z") is None


def test_kept() -> None:
  text = "Start. " + LINE * 4
  assert loops.kept(text, len(LINE)) == "Start. " + LINE


def test_answer_loop() -> None:
  def answer(**message: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", **message}}]}

  assert loops.answer_loop(answer(content="Fine. " + LINE)) is None
  assert loops.answer_loop(answer(content=LINE * 5)) == "answer"
  assert loops.answer_loop(answer(content="ok", reasoning_content=LINE * 4)) == (
    "thinking"
  )
  assert loops.answer_loop({"choices": []}) is None


def test_calls_store() -> None:
  loops.save(["c1", "c2"], "a/x")
  loops.save([], "b/x")
  assert (loops.maker("c1"), loops.maker("c2"), loops.maker("c9")) == (
    "a/x",
    "a/x",
    None,
  )
  loops.save(["c1"], "b/x")
  assert loops.maker("c1") == "b/x"
  completion = {"choices": [{"message": {"tool_calls": [call("c5"), {"id": 3}]}}]}
  assert loops.answer_calls(completion) == ["c5"]


def test_calls_expire(monkeypatch) -> None:
  loops.save(["old"], "a/x")
  monkeypatch.setattr(loops, "IDLE_SECONDS", -1.0)
  assert loops.maker("old") is None
