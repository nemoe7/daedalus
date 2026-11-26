# ---
# name: ernie_think
# version: 1.0.0
# surfaces: [on-chunk, on-answer]
# author: nemoe7
# title: ERNIE think tags
# description: Moves ERNIE <ernie:think> text from content into reasoning_content.
# license: nemoe7 Noncommercial and Public-Interest License 1.2.0
# ---

import logging
import time
from typing import Any

logger = logging.getLogger("daedalus.hooks")

OPEN = "<ernie:think>"
CLOSE = "</ernie:think>"
# Splitter state per stream id, dropped after this many seconds without a chunk.
STALE_SECONDS = 3600
_states: dict[str, tuple[float, dict[str, Any]]] = {}


def state_for(key: str) -> dict[str, Any]:
  """The splitter state of 1 stream, with stale states dropped."""
  now = time.monotonic()
  for name, (stamp, _state) in list(_states.items()):
    if now - stamp > STALE_SECONDS:
      del _states[name]
  found = _states.get(key)
  if found is None:
    found = (now, {"inside": False, "held": "", "done": False})
    _states[key] = found
  _states[key] = (now, found[1])
  return found[1]


def partial(text: str, tag: str) -> int:
  """The length of the suffix of `text` that starts `tag`, 0 when none does."""
  for size in range(min(len(text), len(tag) - 1), 0, -1):
    if tag.startswith(text[-size:]):
      return size
  return 0


def split(text: str, state: dict[str, Any]) -> tuple[str, str]:
  """The content and reasoning parts of `text`, holding a partial tag back."""
  if state["done"]:
    return (text, "") if not state["inside"] else ("", text)
  held, state["held"] = state["held"], ""
  text = held + text
  content, thinking = [], []
  while text:
    tag = CLOSE if state["inside"] else OPEN
    at = text.find(tag)
    if at < 0:
      keep = partial(text, tag)
      if keep:
        state["held"] = text[-keep:]
        text = text[:-keep]
      (thinking if state["inside"] else content).append(text)
      break
    (thinking if state["inside"] else content).append(text[:at])
    state["inside"] = not state["inside"]
    text = text[at + len(tag) :]
  return ("".join(content), "".join(thinking))


def finish(state: dict[str, Any]) -> None:
  """Flush a held partial tag: reasoning while inside, content outside."""
  if state["done"]:
    return
  state["done"] = True
  held, state["held"] = state["held"], ""
  if held:
    state["tail"] = held


def on_chunk(
  chunk: dict[str, Any], model: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
  """Move ERNIE think tag text of each delta into reasoning_content.

  :param chunk: one OpenAI chunk on its way to the client
  :param model: the requested model
  :param context: unused, the stream id keys the state
  """
  choices = chunk.get("choices")
  if not isinstance(choices, list):
    return chunk
  state = state_for(str(chunk.get("id") or ""))
  for choice in choices:
    if not isinstance(choice, dict):
      continue
    delta = choice.get("delta")
    if not isinstance(delta, dict) or not isinstance(delta.get("content"), str):
      continue
    content, thinking = split(delta["content"], state)
    if choice.get("finish_reason"):
      finish(state)
      tail = state.pop("tail", "")
      if state["inside"]:
        thinking += tail
      else:
        content += tail
    found = {**delta, "content": content}
    if thinking or delta.get("reasoning_content"):
      found["reasoning_content"] = (delta.get("reasoning_content") or "") + thinking
    choice["delta"] = found
  return chunk


def on_answer(answer: dict[str, Any], model: str) -> dict[str, Any]:
  """Move ERNIE think tag text of each message into reasoning_content.

  :param answer: the full OpenAI completion
  :param model: the served model
  """
  choices = answer.get("choices")
  if not isinstance(choices, list):
    return answer
  for choice in choices:
    if not isinstance(choice, dict):
      continue
    message = choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
      continue
    content, thinking = split(
      message["content"], {"inside": False, "held": "", "done": False}
    )
    found = {**message, "content": content}
    if thinking or message.get("reasoning_content"):
      found["reasoning_content"] = (message.get("reasoning_content") or "") + thinking
    choice["message"] = found
  return answer
