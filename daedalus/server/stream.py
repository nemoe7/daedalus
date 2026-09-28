"""Server-sent event parsing, and the relay that continues a failed stream."""

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from daedalus import providers, store
from daedalus.providers.base import error_text
from daedalus.routing import context, pacing, router
from daedalus.server import upstream

if TYPE_CHECKING:
  from daedalus.server.api import Tracker

logger = logging.getLogger("daedalus")

STREAM_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError)
ATTEMPT_ERRORS = (*STREAM_ERRORS, StopAsyncIteration)
PACING: pacing.Pacing | None = None
STREAM_FAILED = providers.frame(
  {
    "error": {
      "message": "Upstream stream failed",
      "type": "upstream_error",
      "code": 502,
    }
  }
)


async def sse_data(
  chunks: AsyncIterator[bytes], wait: float | None = None
) -> AsyncIterator[str]:
  """Split an SSE byte stream into the payloads of its `data:` lines, and stop after `wait` seconds of only comments."""
  buffer, heard = b"", time.perf_counter()
  try:
    async for piece in chunks:
      buffer += piece.replace(b"\r\n", b"\n")
      while b"\n\n" in buffer:
        block, buffer = buffer.split(b"\n\n", 1)
        lines = [
          line[5:].strip() for line in block.split(b"\n") if line.startswith(b"data:")
        ]
        if lines:
          heard = time.perf_counter()
          yield b"\n".join(lines).decode()
        elif wait is not None and time.perf_counter() - heard > wait:
          raise httpx.ReadTimeout(f"Only keep-alive bytes for {wait:g}s")
  finally:
    await chunks.aclose()


def provider_count(usage: object) -> dict[str, Any] | None:
  """The input tokens of an OpenAI `usage` object, marked as a provider count."""
  if isinstance(usage, dict) and isinstance(usage.get("prompt_tokens"), int):
    return {"input": usage["prompt_tokens"], "estimate": False}
  return None


def has_content(data: str) -> bool:
  """True for `[DONE]`, and for a chunk with text, a tool call, or a finish reason."""
  if data == "[DONE]":
    return True
  chunk = json.loads(data)
  if not isinstance(chunk, dict) or chunk.get("error"):
    raise providers.ProviderError(f"Upstream stream error: {error_text(chunk)}")
  for choice in chunk.get("choices") or []:
    delta = choice.get("delta") or {}
    text = [delta.get(key) for key in ("content", "reasoning_content", "reasoning")]
    if any(text) or delta.get("tool_calls") or choice.get("finish_reason"):
      return True
  return False


async def first_content(events: AsyncIterator[str]) -> list[str]:
  """The events up to the first one with content."""
  pending = [await anext(events)]
  while not has_content(pending[-1]):
    pending.append(await anext(events))
  return pending


async def relay(
  pending: list[str],
  events: AsyncIterator[str],
  rest: list[str],
  body: dict[str, Any],
  config: dict[str, Any],
  include_usage: bool,
  model: str,
  pin: "Tracker",
  attempts: list[dict[str, Any]] | None = None,
  counts: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
  """Stream one answer, continue from the sent text on a failure, and keep the provider input count in `counts`."""
  identifier, sent, tool = None, [], False
  attempts = [] if attempts is None else attempts
  while True:
    try:
      while True:
        data = pending.pop(0) if pending else await anext(events)
        if data == "[DONE]":
          yield b"data: [DONE]\n\n"
          return
        chunk = json.loads(data)
        if not isinstance(chunk, dict) or chunk.get("error"):
          raise providers.ProviderError(f"Upstream stream error: {error_text(chunk)}")
        if counts is not None:
          counts.update(provider_count(chunk.get("usage")) or {})
        if not include_usage and "usage" in chunk:
          # The client did not ask for the count, so a chunk with only the count stays out.
          if not chunk.get("choices"):
            continue
          if chunk["usage"] is None:
            del chunk["usage"]
        identifier = identifier or chunk.get("id")
        if identifier:
          chunk["id"] = identifier
        for choice in chunk.get("choices") or []:
          delta = choice.get("delta") or {}
          tool = tool or bool(delta.get("tool_calls"))
          if isinstance(delta.get("content"), str):
            sent.append(delta["content"])
        yield providers.frame(chunk)
    except StopAsyncIteration:
      logger.warning("upstream stream ended without [DONE]")
      attempts.append(
        upstream.note(model, "stream failed", None, "ended without [DONE]")
      )
    except STREAM_ERRORS as exc:
      logger.warning("upstream stream failed: %s", upstream.failure_text(exc))
      attempts.append(
        upstream.note(model, "stream failed", None, upstream.failure_text(exc))
      )
    await events.aclose()
    pin.failed(model)
    if tool:
      yield STREAM_FAILED
      return
    prefix = {"role": "assistant", "content": "".join(sent)}
    continued = {**body, "messages": [*body["messages"], prefix]} if sent else body
    tokens, limits = context.input_tokens(continued), store.input_limits()
    paces = store.pace_limits()
    while rest:
      candidate = rest.pop(0)
      if context.too_large(candidate, tokens, limits):
        continue
      if PACING:
        if PACING.full(candidate, paces):
          continue
        PACING.record(candidate, tokens)
      started, effort = time.perf_counter(), {}
      try:
        provider, response = await upstream.attempt(
          candidate, continued, config, effort
        )
        wait = router.model_wait(config, candidate, upstream.WAIT_SECONDS)
        events = sse_data(provider.stream(response, candidate, True), wait)
        pending = await first_content(events)
        model = candidate
        pin.answered(model, time.perf_counter() - started)
        attempts.append(upstream.note(candidate, "answered", started) | effort)
        break
      except (upstream.UpstreamStatus, *ATTEMPT_ERRORS) as exc:
        attempts.append(upstream.failure_note(candidate, started, exc) | effort)
        if isinstance(exc, upstream.RateLimitError):
          attempts[-1]["cooldown"] = pin.failed(candidate, exc)
        logger.warning(
          "upstream %s continuation failed: %s", candidate, upstream.failure_text(exc)
        )
    else:
      yield STREAM_FAILED
      return
