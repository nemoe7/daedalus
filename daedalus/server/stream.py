"""Server-sent event parsing, the relay that continues a failed stream, and the stream cache."""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

import httpx

from daedalus import providers, store
from daedalus.providers import hooks
from daedalus.providers.base import error_text
from daedalus.routing import context, loops, pacing, router
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
        else:
          providers.check_wait(heard, wait)
  finally:
    await chunks.aclose()


def provider_count(usage: object) -> dict[str, Any] | None:
  """The input and output tokens of an OpenAI `usage` object, marked as a provider count."""
  if not isinstance(usage, dict) or not isinstance(usage.get("prompt_tokens"), int):
    return None
  found = {"input": usage["prompt_tokens"], "estimate": False}
  if isinstance(usage.get("completion_tokens"), int):
    found["output"] = usage["completion_tokens"]
  return found


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


def merge_delta(message: dict[str, Any], delta: dict[str, Any]) -> None:
  """Add 1 stream delta to the whole message of a buffered completion."""
  if isinstance(delta.get("role"), str):
    message["role"] = delta["role"]
  for key in ("content", "reasoning_content", "reasoning"):
    if isinstance(delta.get(key), str):
      message[key] = (message.get(key) or "") + delta[key]
  for call in delta.get("tool_calls") or []:
    if not isinstance(call, dict):
      continue
    calls = message.setdefault("tool_calls", [])
    index = call.get("index")
    if not isinstance(index, int) or not 0 <= index < len(calls):
      calls.append({})
      index = len(calls) - 1
    whole = calls[index]
    if isinstance(call.get("id"), str):
      whole["id"] = call["id"]
    if isinstance(call.get("type"), str):
      whole["type"] = call["type"]
    task = call.get("function")
    if isinstance(task, dict):
      function = whole.setdefault("function", {})
      if isinstance(task.get("name"), str):
        function["name"] = task["name"]
      if isinstance(task.get("arguments"), str):
        function["arguments"] = (function.get("arguments") or "") + task["arguments"]


async def cache(
  pending: list[str], events: AsyncIterator[str], model: str
) -> dict[str, Any]:
  """The whole answer of a stream: 1 completion for a client that did not ask for a stream."""
  found: dict[str, Any] = {}
  choices: dict[int, dict[str, Any]] = {}
  order: list[int] = []
  done = False
  try:
    while True:
      if pending:
        data = pending.pop(0)
      else:
        try:
          data = await anext(events)
        except StopAsyncIteration:
          raise providers.ProviderError(
            "Upstream stream ended without [DONE]"
          ) from None
      if data == "[DONE]":
        done = True
        break
      chunk = json.loads(data)
      if not isinstance(chunk, dict) or chunk.get("error"):
        raise providers.ProviderError(f"Upstream stream error: {error_text(chunk)}")
      for key in ("id", "created", "model", "system_fingerprint"):
        if found.get(key) is None and chunk.get(key) is not None:
          found[key] = chunk[key]
      if chunk.get("usage") is not None:
        found["usage"] = chunk["usage"]
      for choice in chunk.get("choices") or []:
        if not isinstance(choice, dict):
          continue
        index = choice.get("index")
        if not isinstance(index, int):
          index = 0
        if index not in choices:
          choices[index] = {"index": index, "message": {}, "finish_reason": None}
          order.append(index)
        whole = choices[index]
        if isinstance(choice.get("delta"), dict):
          merge_delta(whole["message"], choice["delta"])
        if choice.get("finish_reason") is not None:
          whole["finish_reason"] = choice["finish_reason"]
  finally:
    await events.aclose()
  # A stream without [DONE] stopped early, and the ladder continues with the next model.
  if not done:
    raise providers.ProviderError("Upstream stream ended without [DONE]")
  answer: dict[str, Any] = {
    "id": found.get("id") or "chatcmpl-" + uuid.uuid4().hex,
    "object": "chat.completion",
    "created": found.get("created") or int(time.time()),
    "model": found.get("model") or model,
    "choices": [choices[index] for index in order],
  }
  if found.get("usage") is not None:
    answer["usage"] = found["usage"]
  if found.get("system_fingerprint") is not None:
    answer["system_fingerprint"] = found["system_fingerprint"]
  return answer


def routed_effort(attempts: list[dict[str, Any]], model: str) -> str:
  """The reasoning effort of the attempt that answered, and empty when it carries none."""
  for note in reversed(attempts):
    if note.get("result") == "answered" and note.get("model") == model:
      return str(note.get("effort") or "")
  return ""


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
  on_transition: Callable[[str, str, str], None] | None = None,
  requested: str = "",
  stream_context: dict[str, Any] | None = None,
  hook_files: list[Any] | None = None,
) -> AsyncIterator[bytes]:
  """Stream one answer, continue from the sent text on a failure, and keep the provider input count in `counts`."""
  identifier, sent, tool = None, [], False
  attempts = [] if attempts is None else attempts
  chunk_hooks = hook_files or []
  state = stream_context or {}
  while True:
    # Each model gets new loop checks. The answer text so far stays in `sent`.
    thinking, answer = loops.Repeats(), loops.Repeats()
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
          loops.save(
            [
              call["id"]
              for call in delta.get("tool_calls") or []
              if isinstance(call, dict) and isinstance(call.get("id"), str)
            ],
            model,
          )
          reasoning = delta.get("reasoning_content") or delta.get("reasoning")
          if isinstance(reasoning, str) and thinking.feed(reasoning) is not None:
            raise loops.LoopError("thinking")
          if isinstance(delta.get("content"), str):
            if (period := answer.feed(delta["content"])) is not None:
              text = "".join(sent) + delta["content"]
              raise loops.LoopError("answer", loops.kept(text, period))
            sent.append(delta["content"])
        if chunk_hooks:
          failures = sum(1 for note in attempts if note.get("result") != "answered")
          chunk = hooks.run_files(
            "on-chunk",
            chunk_hooks,
            chunk,
            model=requested,
            context={
              **state,
              "attempts": failures,
              "served": model,
              "effort": routed_effort(attempts, model),
            },
          )
        yield providers.frame(chunk)
    except loops.LoopError as exc:
      logger.warning("upstream %s stopped: %s", model, exc)
      attempts.append(upstream.note(model, "loop", None, str(exc)))
      if exc.kept is not None:
        sent = [exc.kept]
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
    pin.failed(model, attempt=attempts[-1])
    if tool:
      yield STREAM_FAILED
      return
    prefix = {"role": "assistant", "content": "".join(sent)}
    continued = {**body, "messages": [*body["messages"], prefix]} if sent else body
    tokens, limits = context.input_tokens(continued), store.input_limits()
    paces = store.pace_limits()
    transition_from, transition_reason = model, "err"
    while rest:
      candidate = rest.pop(0)
      if context.too_large(candidate, tokens, limits):
        transition_reason = "ctx"
        continue
      if PACING:
        if PACING.full(pin.lane(candidate), paces):
          transition_reason = "lmt"
          continue
        PACING.record(pin.lane(candidate), tokens)
      if on_transition is not None:
        on_transition(transition_from, candidate, transition_reason)
      started, effort = time.perf_counter(), {}
      try:
        provider, response = await upstream.attempt(
          candidate, continued, config, effort, pin.client
        )
        wait = router.model_wait(config, candidate, upstream.WAIT_SECONDS)
        events = sse_data(provider.stream(response, candidate, True, wait), wait)
        pending = await first_content(events)
        model = candidate
        attempts.append(upstream.note(candidate, "answered", started) | effort)
        pin.answered(model, time.perf_counter() - started, attempts[-1])
        break
      except (upstream.UpstreamStatus, *ATTEMPT_ERRORS) as exc:
        attempts.append(upstream.failure_note(candidate, started, exc) | effort)
        if isinstance(exc, upstream.RateLimitError):
          attempts[-1]["cooldown"] = pin.failed(candidate, exc, attempts[-1])
          transition_reason = "lmt"
        else:
          transition_reason = "err"
        transition_from = candidate
        logger.warning(
          "upstream %s continuation failed: %s", candidate, upstream.failure_text(exc)
        )
    else:
      yield STREAM_FAILED
      return
