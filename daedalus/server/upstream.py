"""The upstream HTTP client, one candidate request, and the OpenAI error shape."""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Mapping
from contextvars import ContextVar
from typing import Any

import httpx
from fastapi.responses import JSONResponse

from daedalus import providers, store
from daedalus.providers.base import error_detail, error_text
from daedalus.routing import loops, router
from daedalus.server.logs import elapsed

logger = logging.getLogger("daedalus")


TIMEOUT_SECONDS = 600.0


# The wait for data from the provider. Each data event starts it again, so a slow
# stream is not cut off. Keep-alive bytes do not start it again.
WAIT_SECONDS = 60.0


# Characters of a provider error body that the dashboard keeps.
DETAIL_LIMIT = 4000

# Client headers that Daedalus owns: credentials, transport and proxy headers.
KEPT_BACK = frozenset(
  {
    "authorization",
    "cookie",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
    "host",
    "accept",
    "accept-encoding",
    "content-type",
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
    "expect",
    "forwarded",
    "via",
    "x-real-ip",
  }
)
# Open WebUI sends the name, e-mail, id and role of its user in `x-openwebui-user-*`.
KEPT_BACK_PREFIXES = ("proxy-", "x-forwarded-", "tailscale-", "x-openwebui-user-")
# The client headers of the current request that go on to each provider.
_forwarded: ContextVar[httpx.Headers] = ContextVar("forwarded")
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
  global _client
  if _client is None:
    _client = httpx.AsyncClient(
      timeout=httpx.Timeout(TIMEOUT_SECONDS, read=WAIT_SECONDS)
    )
  return _client


def set_client(client: httpx.AsyncClient | None) -> None:
  """Replace the upstream client. Test seam."""
  global _client
  _client = client


async def close() -> None:
  """Close the upstream client on shutdown."""
  if _client is not None:
    await _client.aclose()
    set_client(None)


def error_response(
  status: int, message: str, error_type: str, headers: dict[str, str] | None = None
) -> JSONResponse:
  """Answer in the OpenAI error shape."""
  return JSONResponse(
    status_code=status,
    content={"error": {"message": message, "type": error_type, "code": status}},
    headers=headers,
  )


def cooling_response(seconds: float) -> JSONResponse:
  """The 429 answer when each model of the request is in a cooldown."""
  wait = max(1, math.ceil(seconds))
  return error_response(
    429,
    f"Each model of this request is rate limited. Retry in {wait}s.",
    "rate_limit_exceeded",
    {"Retry-After": str(wait)},
  )


def failure_text(exc: Exception) -> str:
  """The error type and its message, for one log line."""
  detail = error_text(str(exc)) if str(exc) else ""
  return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class UpstreamStatus(Exception):
  """An upstream error status, with the short error text, the headers and the full body."""

  def __init__(
    self,
    status: int,
    detail: str = "",
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
  ) -> None:
    super().__init__(status)
    self.status = status
    self.detail = detail
    self.headers = httpx.Headers(headers or {})
    self.body = body


class BadRequestError(UpstreamStatus):
  """HTTP 400 or 422: the provider does not accept the request."""


class AuthenticationError(UpstreamStatus):
  """HTTP 401 or 403: the provider does not accept the key."""


class NotFoundError(UpstreamStatus):
  """HTTP 404: the provider does not know the model or the path."""


class RateLimitError(UpstreamStatus):
  """HTTP 429: the provider rate limit."""


class ServerError(UpstreamStatus):
  """HTTP 500 or higher: a provider fault."""


STATUS_ERRORS: dict[int, type[UpstreamStatus]] = {
  400: BadRequestError,
  401: AuthenticationError,
  403: AuthenticationError,
  404: NotFoundError,
  422: BadRequestError,
  429: RateLimitError,
}


def status_error(
  status: int, detail: str, headers: Mapping[str, str], body: bytes
) -> UpstreamStatus:
  """The error class of one upstream status."""
  kind = STATUS_ERRORS.get(status, ServerError if status >= 500 else UpstreamStatus)
  return kind(status, detail, headers, body)


def note(
  model: str, result: str, started: float | None = None, error: str = ""
) -> dict[str, Any]:
  """One attempt of a request, for the dashboard."""
  seconds = None if started is None else round(time.perf_counter() - started, 3)
  return {"model": model, "result": result, "seconds": seconds, "error": error}


def failure_note(model: str, started: float, exc: Exception) -> dict[str, Any]:
  """One failed attempt, with the full provider error text when there is one."""
  if isinstance(exc, UpstreamStatus):
    return note(model, f"HTTP {exc.status}", started, exc.detail)
  if isinstance(exc, loops.LoopError):
    return note(model, "loop", started, str(exc))
  return note(model, "failed", started, failure_text(exc))


# The request fields that limit the output tokens.
OUTPUT_FIELDS = ("max_tokens", "max_completion_tokens")


def with_defaults(candidate: str, body: dict[str, Any]) -> dict[str, Any]:
  """The body with the stored effort when it has none, and output limits cut to the model."""
  found = store.model_limits(candidate)
  changed = dict(body)
  if "reasoning_effort" in found and "reasoning_effort" not in body:
    changed["reasoning_effort"] = found["reasoning_effort"]
  limit = found.get("max_output_tokens")
  for field in OUTPUT_FIELDS:
    asked = body.get(field)
    if limit is None or isinstance(asked, bool) or not isinstance(asked, int):
      continue
    changed[field] = min(asked, limit)
  return changed


def without_reasoning(candidate: str, body: dict[str, Any]) -> dict[str, Any]:
  """The body without `reasoning_effort` for a stored model that does not reason."""
  if "reasoning_effort" not in body or store.reasoning_flags().get(candidate, True):
    return body
  return {key: value for key, value in body.items() if key != "reasoning_effort"}


def forward(headers: Mapping[str, str]) -> None:
  """Keep the client headers of the current request that go on to each provider."""
  _forwarded.set(
    httpx.Headers(
      {
        name: value
        for name, value in headers.items()
        if name.lower() not in KEPT_BACK
        and not name.lower().startswith(KEPT_BACK_PREFIXES)
      }
    )
  )


def with_client(headers: Mapping[str, str]) -> httpx.Headers:
  """The client headers of the current request, with the provider headers on top."""
  merged = httpx.Headers(_forwarded.get(httpx.Headers()))
  merged.update(headers)
  return merged


async def attempt(
  candidate: str,
  body: dict[str, Any],
  config: dict[str, Any],
  sent: dict[str, Any] | None = None,
  client: str | None = None,
) -> tuple[providers.OpenAIProvider, httpx.Response]:
  """Send one candidate request, and fail on an upstream error status.

  When `sent` is a dict, it gets the reasoning effort that went upstream. `client` picks its provider key.
  """
  asked = "reasoning_effort" in body
  body = without_reasoning(candidate, with_defaults(candidate, body))
  provider, url, payload, headers = providers.prepare(candidate, body, config, client)
  effort = provider.effort(payload)
  if payload.get("stream") and provider.stream_usage:
    payload = {
      **payload,
      "stream_options": {
        **(payload.get("stream_options") or {}),
        "include_usage": True,
      },
    }
  if sent is not None and (asked or effort is not None):
    sent["effort"] = effort
  wait = router.model_wait(config, candidate, WAIT_SECONDS)
  timeout = httpx.Timeout(TIMEOUT_SECONDS, read=wait)
  upstream = get_client().build_request(
    "POST", url, json=payload, headers=with_client(headers), timeout=timeout
  )
  started = time.perf_counter()
  response = await get_client().send(upstream, stream=True)
  status = response.status_code
  if status < 400:
    logger.info("upstream %s %d %s", candidate, status, elapsed(started))
    return provider, response
  raw = await response.aread()
  await response.aclose()
  raise rejected(candidate, response, started, raw)


async def in_time(work: Awaitable[Any], deadline: float) -> Any:
  """The result of `work`, or TimeoutError at the deadline."""
  return await asyncio.wait_for(work, max(0.0, deadline - time.perf_counter()))


def late_note(model: str, started: float) -> dict[str, Any]:
  """The attempt that reached the request limit, logged as a failure."""
  exc = TimeoutError(f"No answer in {TIMEOUT_SECONDS:g}s")
  logger.warning("upstream %s failed: %s", model, failure_text(exc))
  return failure_note(model, started, exc)


async def read_body(response: httpx.Response, wait: float) -> bytes:
  """The whole body, or a ReadTimeout after `wait` seconds of only blank bytes."""
  parts, heard = [], time.perf_counter()
  try:
    async for chunk in response.aiter_bytes():
      parts.append(chunk)
      if chunk.strip():
        heard = time.perf_counter()
      else:
        providers.check_wait(heard, wait)
  finally:
    await response.aclose()
  return b"".join(parts)


def rejected(
  candidate: str, response: httpx.Response, started: float, raw: bytes
) -> UpstreamStatus:
  """Log an upstream error status, and return the error to raise."""
  status = response.status_code
  logger.warning(
    "upstream %s %d %s: %s", candidate, status, elapsed(started), error_text(raw)
  )
  return status_error(status, error_detail(raw, DETAIL_LIMIT), response.headers, raw)


async def post(
  candidate: str, url: str, headers: dict[str, str], **content: Any
) -> httpx.Response:
  """Send one request without a stream, and fail on an upstream error status."""
  started = time.perf_counter()
  # A whole answer can take longer than the wait for one stream chunk.
  timeout = httpx.Timeout(TIMEOUT_SECONDS)
  response = await get_client().post(
    url, headers=with_client(headers), timeout=timeout, **content
  )
  if response.status_code >= 400:
    raise rejected(candidate, response, started, response.content)
  logger.info("upstream %s %d %s", candidate, response.status_code, elapsed(started))
  return response
