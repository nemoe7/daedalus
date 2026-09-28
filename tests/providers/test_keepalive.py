"""Check that the Gemini and Mistral stream readers stop after the wait with only keep-alive lines."""

import asyncio
import json
import time
from collections.abc import AsyncIterator

import httpx

from daedalus import providers

DATA = {"choices": [{"index": 0, "delta": {"content": "hi"}}]}
GEMINI_DATA = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}


class KeepAlive(httpx.AsyncByteStream):
  """A body with 1 data line, then only keep-alive lines."""

  def __init__(self, data: dict) -> None:
    self.data = data

  async def __aiter__(self) -> AsyncIterator[bytes]:
    yield f"data: {json.dumps(self.data)}\n\n".encode()
    while True:
      yield b": keep-alive\n\n"
      await asyncio.sleep(0.02)


async def read(name: str, data: dict) -> tuple[int, float, str]:
  """The chunk count, the seconds and the error of 1 stream with a 0.2 s wait."""
  config = {"api_base": f"https://{name}.test", "api_key": "k"}
  provider = providers.PROVIDERS[name](name, config)
  response = httpx.Response(200, stream=KeepAlive(data))
  started, count = time.perf_counter(), 0
  try:
    async for _ in provider.stream(response, f"{name}/m", False, 0.2):
      count += 1
  except httpx.ReadTimeout as exc:
    return count, time.perf_counter() - started, str(exc)
  return count, time.perf_counter() - started, ""


def test_readers_stop_after_the_wait() -> None:
  for name, data in (("mistral", DATA), ("gemini", GEMINI_DATA)):
    count, seconds, error = asyncio.run(asyncio.wait_for(read(name, data), 5))
    assert error == "Only keep-alive bytes for 0.2s", (name, error)
    assert count >= 1 and seconds < 2, (name, count, seconds)
