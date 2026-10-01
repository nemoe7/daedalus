"""The SSE parsers of `stream.py`: a split stream, a cut stream, no usage, and an error chunk."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from daedalus.providers.base import ProviderError
from daedalus.server import stream

MASTER = "test-master-key-0001"


async def pieces(*parts: bytes) -> AsyncIterator[bytes]:
  for part in parts:
    yield part


async def events(*items: str) -> AsyncIterator[str]:
  for item in items:
    yield item


def chunk(**values: object) -> str:
  return json.dumps({"id": "x", "choices": [{"delta": values}]})


async def sse(*parts: bytes, wait: float | None = None) -> list[str]:
  return [data async for data in stream.sse_data(pieces(*parts), wait)]


async def test_sse_data_splits_blocks() -> None:
  """A blank line ends one event, and each `data:` payload comes out alone."""
  body = b'data: {"a":1}\n\ndata: {"b":2}\n\n'
  assert await sse(body) == ['{"a":1}', '{"b":2}']


async def test_sse_data_joins_the_data_lines_of_one_block() -> None:
  """Several `data:` lines in one block come out as one payload."""
  assert await sse(b'data: {"a":\ndata: 1}\n\n') == ['{"a":\n1}']


async def test_sse_data_takes_crlf_and_newlines() -> None:
  """A CRLF stream gives the same payloads as an LF stream."""
  assert await sse(b'data: {"a":1}\r\n\r\n') == ['{"a":1}']


async def test_sse_data_skips_a_keep_alive_comment() -> None:
  """A block with only a comment yields nothing."""
  assert await sse(b': ping\n\ndata: {"a":1}\n\n') == ['{"a":1}']


async def test_sse_data_stops_on_a_block_cut_in_two() -> None:
  """A stream cut inside an event yields the events it got, not the broken one."""
  assert await sse(b'data: {"a":1}\n\ndata: {"b"') == ['{"a":1}']


async def test_sse_data_times_out_on_keep_alive_only() -> None:
  """With a wait of 0, a comment block raises instead of waiting for data."""
  with pytest.raises(httpx.ReadTimeout):
    await sse(b": ping\n\n", wait=0.0)


def test_provider_count_needs_a_prompt_count() -> None:
  """No usage, or a usage without `prompt_tokens`, gives no count."""
  assert stream.provider_count(None) is None
  assert stream.provider_count({"completion_tokens": 7}) is None
  assert stream.provider_count({"prompt_tokens": "5"}) is None


def test_provider_count_keeps_the_output_count() -> None:
  """A usage with both counts gives the input, the output and the provider mark."""
  assert stream.provider_count({"prompt_tokens": 5}) == {"input": 5, "estimate": False}
  assert stream.provider_count({"prompt_tokens": 5, "completion_tokens": 7}) == {
    "input": 5,
    "estimate": False,
    "output": 7,
  }


def test_has_content_accepts_done_and_a_finish_reason() -> None:
  """`[DONE]` and a finish reason count as content."""
  assert stream.has_content("[DONE]") is True
  assert stream.has_content('{"choices":[{"finish_reason":"stop"}]}') is True


def test_has_content_needs_text_a_tool_call_or_a_finish_reason() -> None:
  """An empty delta is not content."""
  assert stream.has_content('{"choices":[{"delta":{}}]}') is False
  assert stream.has_content(chunk(content="hi")) is True
  assert stream.has_content(chunk(tool_calls=[{"id": "c1"}])) is True


def test_has_content_raises_on_an_error_chunk() -> None:
  """An error object in the stream stops the relay."""
  with pytest.raises(ProviderError, match="Upstream stream error"):
    stream.has_content('{"error":{"message":"boom"}}')


async def test_first_content_waits_for_content() -> None:
  """The events up to the first one with content stay, in order."""
  kept = await stream.first_content(
    events('{"choices":[{"delta":{}}]}', chunk(content="hi"), chunk(content="there"))
  )
  assert kept == ['{"choices":[{"delta":{}}]}', chunk(content="hi")]
