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
from daedalus.providers import hooks
from daedalus.providers.base import error_detail, error_text
from daedalus.routing import limits, loops, router
from daedalus.server.logs import elapsed

logger = logging.getLogger("daedalus")


TIMEOUT_SECONDS = 600.0


# The wait for data from the provider. Each data event starts it again, so a slow
# stream is not cut off. Keep-alive bytes do not start it again.
WAIT_SECONDS = 60.0


# Characters of a provider error body that the dashboard keeps.
DETAIL_LIMIT = 4000

# The pool of the shared client. One chat request races at most `affinity.count + 1`
# models, and `affinity.count` stops at 10. Every connection stays warm.
MAX_CONNECTIONS = 64

# Client headers that daedalus owns: credentials, transport and proxy headers.
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
# The limits that keep the rate-limit headers of each answer. The API sets it.
LIMITS: limits.Limits | None = None
# The cooldown lane of the media attempt that runs now. Without it, the model is the lane.
LANE: ContextVar[str | None] = ContextVar("lane", default=None)


def observe(lane: str, headers: httpx.Headers, body: bytes = b"") -> None:
  """Give the limit headers and error body of one answer to the limits."""
  if LIMITS is not None:
    LIMITS.observe(lane, headers, body)


def new_client() -> httpx.AsyncClient:
  """A client with the timeouts and the pool of the router."""
  return httpx.AsyncClient(
    timeout=httpx.Timeout(TIMEOUT_SECONDS, read=WAIT_SECONDS),
    limits=httpx.Limits(
      max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS
    ),
  )


def get_client() -> httpx.AsyncClient:
  global _client
  if _client is None:
    _client = new_client()
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


def unknown_model(model: str) -> JSONResponse:
  """The 404 answer for a name that the model list does not hold, with the nearest listed id."""
  near = store.catalog_name(model)
  hint = f"The Models page lists {near}." if near else "See the Models page."
  return error_response(404, f"Unknown model {model}. {hint}", "model_not_found")


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
  model: str,
  result: str,
  started: float | None = None,
  error: str = "",
  ended: float | None = None,
) -> dict[str, Any]:
  """One attempt of a request, for the dashboard. `ended` names its end, when that is not now."""
  end = time.perf_counter() if ended is None else ended
  seconds = None if started is None else round(end - started, 3)
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


# The effort names in rank order, lowest first. A gateway maps a name it lists to itself.
def nearest_effort(asked: str, listed: list[str]) -> str | None:
  """The listed name nearest the asked effort, the lower of 2 equals, and None without a match.

  An asked name outside the effort ranks, and a list of such names alone, find no answer.
  `none` is no rung of the ladder: a real level takes a real rung, and `none` answers only a
  model that lists `none` alone.
  """
  ranks = router.EFFORT_RANKS
  if asked in listed:
    return asked
  if asked not in ranks:
    return None
  rank = ranks.index(asked)
  known = [name for name in listed if name in ranks]
  if asked != "none":
    known = [name for name in known if name != "none"] or known
  if not known:
    return None
  return min(known, key=lambda name: (abs(ranks.index(name) - rank), ranks.index(name)))


def with_defaults(candidate: str, body: dict[str, Any]) -> dict[str, Any]:
  """The body with the stored effort, the effort moved to a name the model lists, and limits cut.

  The stored effort fills the gap when the body names none, then the list of the catalog moves
  the value to the nearest name of that list, so a model never receives a name it refuses.
  """
  found = store.model_limits(candidate)
  changed = dict(body)
  if "reasoning_effort" not in body:
    effort = found.get("reasoning_effort")
    if effort is not None:
      changed["reasoning_effort"] = effort
  listed = found.get("supported_efforts")
  effort = changed.get("reasoning_effort")
  if isinstance(effort, str) and isinstance(listed, list):
    chosen = nearest_effort(effort, listed)
    if chosen is not None:
      changed["reasoning_effort"] = chosen
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

  When `sent` is a dict, it gets the reasoning effort that went upstream. `client` picks its
  provider key.
  """
  asked = "reasoning_effort" in body
  body = without_reasoning(candidate, with_defaults(candidate, body))
  provider, url, payload, headers = providers.prepare(candidate, body, config, client)
  if payload.get("stream") and provider.stream_usage:
    payload = {
      **payload,
      "stream_options": {
        **(payload.get("stream_options") or {}),
        "include_usage": True,
      },
    }
  headers = dict(headers)
  payload = hooks.run("on-upstream", config, candidate, payload, headers=headers)
  effort = provider.effort(payload)
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
  lane = router.lane(config, candidate, client)
  if status < 400:
    observe(lane, response.headers)
    logger.info("upstream %s %d %s", candidate, status, elapsed(started))
    return provider, response
  raw = await response.aread()
  await response.aclose()
  observe(lane, response.headers, raw)
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
  # The rate-limit headers and body of a 429 explain the limit, so they count before the error goes out.
  body = response.content if response.status_code >= 400 else b""
  observe(LANE.get() or candidate, response.headers, body)
  if response.status_code >= 400:
    raise rejected(candidate, response, started, response.content)
  logger.info("upstream %s %d %s", candidate, response.status_code, elapsed(started))
  return response
