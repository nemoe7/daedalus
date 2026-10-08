"""Find tool, thinking and answer loops, and keep the model that made each tool call."""

import json
import sqlite3
import time

from daedalus import store
from daedalus.providers.base import ProviderError

# The same tool call this many times since the last user message is a tool loop.
CALLS = 3
# A passage that repeats this many times in a row is a text loop.
REPEATS = 4
# The shortest and the longest passage, in characters.
SHORTEST, LONGEST = 20, 2000
# A tool call id with no use for this long expires. The session idle setting replaces it.
IDLE_SECONDS = 3600.0
TABLE = "calls"
SCHEMA = (
  f"CREATE TABLE IF NOT EXISTS {TABLE} (call TEXT PRIMARY KEY, model TEXT NOT NULL,"
  " used REAL NOT NULL)"
)


class LoopError(ProviderError):
  """A thinking or answer loop, with the answer text that the next model continues from."""

  def __init__(self, channel: str, kept: str | None = None) -> None:
    super().__init__(f"{channel} loop")
    self.channel, self.kept = channel, kept


def call_key(call: object) -> str | None:
  """The tool name and the arguments of one call, with the arguments in 1 standard form."""
  if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
    return None
  function = call["function"]
  arguments = function.get("arguments")
  if isinstance(arguments, str):
    try:
      arguments = json.loads(arguments)
    except ValueError:
      pass
  return json.dumps([function.get("name"), arguments], sort_keys=True)


def repeated_call(messages: list) -> tuple[str, int] | None:
  """The id and the count of a call in the last assistant message that repeats a loop since the last user message."""
  start = max(
    (i + 1 for i, m in enumerate(messages) if m.get("role") == "user"), default=0
  )
  answers = [m for m in messages[start:] if m.get("role") == "assistant"]
  if not answers:
    return None
  counts: dict[str, int] = {}
  for message in answers:
    for call in message.get("tool_calls") or []:
      if (key := call_key(call)) is not None:
        counts[key] = counts.get(key, 0) + 1
  for call in answers[-1].get("tool_calls") or []:
    key = call_key(call)
    if key is not None and counts[key] >= CALLS and isinstance(call.get("id"), str):
      return call["id"], counts[key]
  return None


def connect() -> sqlite3.Connection:
  return store.table_connection(SCHEMA)


def save(calls: list[str], model: str) -> None:
  """Keep the model that made each call, and remove the expired calls."""
  if not calls:
    return
  now = time.time()
  database = connect()
  with database:
    store.drop_expired(database, TABLE, IDLE_SECONDS, now)
    database.executemany(
      f"INSERT OR REPLACE INTO {TABLE} VALUES (?, ?, ?)",
      [(call, model, now) for call in calls],
    )
  database.close()


def clear() -> None:
  """Remove all kept tool calls."""
  database = connect()
  with database:
    database.execute(f"DELETE FROM {TABLE}")
  database.close()


def maker(call: str) -> str | None:
  """The model that made this call, while the call is live."""
  database = connect()
  with database:
    row = database.execute(
      f"SELECT model FROM {TABLE} WHERE call = ? AND used >= ?",
      (call, time.time() - IDLE_SECONDS),
    ).fetchone()
  database.close()
  return row[0] if row else None


def answer_calls(completion: dict) -> list[str]:
  """The tool call ids of an answer without a stream."""
  return [
    call["id"]
    for choice in completion.get("choices") or []
    for call in (choice.get("message") or {}).get("tool_calls") or []
    if isinstance(call, dict) and isinstance(call.get("id"), str)
  ]


def primitive(unit: str) -> int:
  """The length of the shortest part that repeats to make the unit."""
  return (unit + unit).find(unit, 1)


class Repeats:
  """The recent text of 1 channel, which finds a passage that repeats in a row."""

  def __init__(self) -> None:
    self.tail = ""

  def feed(self, piece: str) -> int | None:
    """Add a piece of text. Return the passage length when the text now ends with a loop."""
    if not piece:
      return None
    self.tail = (self.tail + piece)[-REPEATS * LONGEST :]
    tail, size = self.tail, len(self.tail)
    if size < REPEATS * SHORTEST:
      return None
    probe = tail[-SHORTEST:]
    end = size - SHORTEST
    while (found := tail.rfind(probe, 0, end)) >= 0:
      period = size - SHORTEST - found
      end = found + SHORTEST - 1
      if period > LONGEST or REPEATS * period > size:
        return None
      if period < SHORTEST:
        continue
      unit = tail[-period:]
      if tail[-REPEATS * period :] == unit * REPEATS:
        return period if primitive(unit) >= SHORTEST else None
    return None


def text_loop(text: str, step: int = 64) -> int | None:
  """The passage length of the first loop in a full text, read as a stream of pieces."""
  repeats = Repeats()
  for index in range(0, len(text), step):
    if (period := repeats.feed(text[index : index + step])) is not None:
      return period
  return None


def kept(text: str, period: int) -> str:
  """The text up to the end of the first copy of the passage."""
  return text[: len(text) - (REPEATS - 1) * period]


def answer_loop(completion: dict) -> str | None:
  """The channel with a loop in an answer without a stream: "thinking" or "answer"."""
  for choice in completion.get("choices") or []:
    message = choice.get("message") or {}
    thinking = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(thinking, str) and text_loop(thinking) is not None:
      return "thinking"
    content = message.get("content")
    if isinstance(content, str) and text_loop(content) is not None:
      return "answer"
  return None
