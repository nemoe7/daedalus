"""The API requests in flight, and the event stream that sends their changes to the dashboard."""

import asyncio
import itertools
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

# A comment line on an idle stream keeps proxies from closing it.
KEEPALIVE_SECONDS = 15.0
# A stream that falls this many events behind stops, and the page opens a new one.
QUEUE_LIMIT = 1000


def event(kind: str, data: object) -> str:
  return f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"


class Live:
  """The requests in flight, and a queue for each open event stream."""

  def __init__(self) -> None:
    self.rows: dict[int, dict[str, Any]] = {}
    self.queues: set[asyncio.Queue[str | None]] = set()
    self.ids = itertools.count(1)

  def start(self, path: str) -> int:
    """Add a request that has just arrived, and return its id."""
    key = next(self.ids)
    now = time.time()
    self.rows[key] = {
      "id": key,
      "path": path,
      "started": now,
      "attempt_started": now,
      "first": None,
    }
    self.send("start", self.view(key))
    return key

  def update(self, key: int, **fields: Any) -> None:
    """Add request fields, such as the model, to a request in flight."""
    if key in self.rows:
      row = self.rows[key]
      if "trying" in fields and fields["trying"] != row.get("trying"):
        row["attempt_started"] = time.time()
        row["first"] = None
      row.update(fields)
      self.send("update", self.view(key))

  def first(self, key: int, **fields: Any) -> None:
    """Keep the time of the first token, or of the full answer without a stream."""
    if key in self.rows:
      self.rows[key].update(fields, first=time.time())
      self.send("first", self.view(key))

  def end(self, key: int, row: dict[str, Any] | None) -> None:
    """Remove a finished request, and send the row that the history keeps."""
    if self.rows.pop(key, None) is not None:
      self.send("end", {"id": key, "row": row})

  def view(self, key: int) -> dict[str, Any]:
    """A request as the page gets it, with ages in seconds instead of clock times."""
    row, now = self.rows[key], time.time()
    shown = {
      name: value
      for name, value in row.items()
      if name not in {"started", "attempt_started", "first"}
    }
    shown["age"] = round(now - row["started"], 3)
    shown["attempt_age"] = round(now - row["attempt_started"], 3)
    shown["ttft"] = (
      None if row["first"] is None else round(row["first"] - row["attempt_started"], 3)
    )
    return shown

  def send(self, kind: str, data: object) -> None:
    text = event(kind, data)
    for queue in list(self.queues):
      if queue.qsize() < QUEUE_LIMIT:
        queue.put_nowait(text)
      else:
        self.queues.discard(queue)
        queue.put_nowait(None)

  async def events(
    self,
    gone: Callable[[], Awaitable[bool]],
    keepalive: float = KEEPALIVE_SECONDS,
  ) -> AsyncIterator[str]:
    """All requests in flight, then each change, until the page closes the stream."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    self.queues.add(queue)
    try:
      yield event("live", [self.view(key) for key in self.rows])
      while not await gone():
        try:
          text = await asyncio.wait_for(queue.get(), keepalive)
        except TimeoutError:
          text = ": keepalive\n\n"
        if text is None:
          return
        yield text
    finally:
      self.queues.discard(queue)
