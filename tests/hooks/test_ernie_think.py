"""The ernie_think hook: <ernie:think> tags move from content to reasoning_content."""

from pathlib import Path
from types import ModuleType
from typing import Any

from daedalus.providers import hooks

MODULE = hooks.load(Path(__file__).parents[2] / "hooks" / "ernie_think.py")
assert MODULE is not None
ernie_think: ModuleType = MODULE

# Each test gets its own stream id, so no state carries over.
COUNT = 0


def chunk(content: str, **extra: Any) -> dict[str, Any]:
  """One streamed chunk with a string content delta."""
  return {"choices": [{"delta": {"content": content, **extra}}]}


def deltas(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
  """The delta of each choice of each chunk after the hook runs, 1 stream id."""
  global COUNT
  COUNT += 1
  found = []
  for item in chunks:
    item = {
      **item,
      "id": f"s{COUNT}",
      "choices": [
        {
          **choice,
          "delta": {
            key: value
            for key, value in choice["delta"].items()
            if key != "finish_reason"
          },
          "finish_reason": choice["delta"].get("finish_reason"),
        }
        for choice in item["choices"]
      ],
    }
    item = ernie_think.on_chunk(item, "openrouter/baidu/ernie-5.5-flash-preview:free")
    found.extend(choice["delta"] for choice in item["choices"])
  return found


def test_tags_split_into_reasoning() -> None:
  """Text between the tags lands in reasoning_content, text outside stays content."""
  found = deltas(
    [chunk("before <ernie:think>plan</ernie:think> after", finish_reason="stop")]
  )
  assert found[0]["content"] == "before  after", found
  assert found[0]["reasoning_content"] == "plan", found


def test_opening_tag_split_across_chunks() -> None:
  """A partial opening tag at a chunk end waits, then splits on the next chunk."""
  found = deltas([chunk("a <ernie:thi"), chunk("nk>why</ernie:think>b")])
  assert found[0] == {"content": "a "}, found
  assert found[1] == {"content": "b", "reasoning_content": "why"}, found


def test_closing_tag_split_across_chunks() -> None:
  """A partial closing tag inside reasoning waits, then splits on the next chunk."""
  found = deltas([chunk("<ernie:think>why</ernie:thi"), chunk("nk>done")])
  assert found[0] == {"content": "", "reasoning_content": "why"}, found
  assert found[1] == {"content": "done"}, found


def test_unclosed_tag_remainder_is_reasoning() -> None:
  """A stream that ends inside an unclosed tag counts the remainder as reasoning."""
  found = deltas([chunk("<ernie:think>part"), chunk("more", finish_reason="stop")])
  assert found[0] == {"content": "", "reasoning_content": "part"}, found
  assert found[1] == {"content": "", "reasoning_content": "more"}, found


def test_held_partial_tag_flushes_as_content() -> None:
  """A held partial opening tag that never completes flushes as content at the end."""
  found = deltas([chunk("tail <ernie:thi", finish_reason="stop")])
  assert found[0] == {"content": "tail <ernie:thi"}, found


def test_streams_do_not_share_state() -> None:
  """Two chunk ids keep separate splitter states."""
  first = {"id": "a", "choices": [{"delta": {"content": "<ernie:think>x"}}]}
  second = {"id": "b", "choices": [{"delta": {"content": "plain"}}]}
  ernie_think.on_chunk(first, "m")
  found = ernie_think.on_chunk(second, "m")
  assert found["choices"][0]["delta"] == {"content": "plain"}, found


def test_non_string_content_passes_through() -> None:
  """A tool-call delta without string content stays untouched."""
  item = {"id": "s2", "choices": [{"delta": {"tool_calls": [{"index": 0}]}}]}
  assert ernie_think.on_chunk(item, "m") is item, item


def test_answer_message_split() -> None:
  """A full completion message splits the same way as the stream."""
  answer = {
    "choices": [
      {
        "message": {
          "role": "assistant",
          "content": "yes <ernie:think>because</ernie:think> ok",
        }
      }
    ]
  }
  found = ernie_think.on_answer(answer, "m")
  message = found["choices"][0]["message"]
  assert message["content"] == "yes  ok", message
  assert message["reasoning_content"] == "because", message


def test_answer_without_tags_stays_identical() -> None:
  """A message without tags keeps its content and gains no reasoning field."""
  answer = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
  found = ernie_think.on_answer(answer, "m")
  assert found["choices"][0]["message"] == {"role": "assistant", "content": "hi"}


def test_existing_reasoning_is_kept() -> None:
  """Reasoning the delta already carries stays, with the tag text appended."""
  found = deltas([chunk("<ernie:think>new", reasoning_content="old ")])
  assert found[0]["reasoning_content"] == "old new", found
