"""The upstream HTTP client, one candidate request, and the OpenAI error shape."""

import logging
import time
from typing import Any

import httpx
from fastapi.responses import JSONResponse

from daedalus import providers
from daedalus.providers.base import error_detail, error_text
from daedalus.server.logs import elapsed

logger = logging.getLogger("daedalus")


TIMEOUT_SECONDS = 600.0


# The wait for one answer. A provider that sends bytes resets it, so a slow
# stream is not cut off.
WAIT_SECONDS = 60.0


# Characters of a provider error body that the dashboard keeps.
DETAIL_LIMIT = 4000
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


def error_response(status: int, message: str, error_type: str) -> JSONResponse:
  """Answer in the OpenAI error shape."""
  return JSONResponse(
    status_code=status,
    content={"error": {"message": message, "type": error_type, "code": status}},
  )


def failure_text(exc: Exception) -> str:
  """The error type and its message, for one log line."""
  detail = error_text(str(exc)) if str(exc) else ""
  return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class UpstreamStatus(Exception):
  def __init__(self, status: int, detail: str = "") -> None:
    super().__init__(status)
    self.status = status
    self.detail = detail


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
  return note(model, "failed", started, failure_text(exc))


async def attempt(
  candidate: str, body: dict[str, Any], config: dict[str, Any]
) -> tuple[providers.OpenAIProvider, httpx.Response]:
  """Send one candidate request, and fail on an upstream error status."""
  provider, url, payload, headers = providers.prepare(candidate, body, config)
  upstream = get_client().build_request("POST", url, json=payload, headers=headers)
  started = time.perf_counter()
  response = await get_client().send(upstream, stream=True)
  status = response.status_code
  if status < 400:
    logger.info("upstream %s %d %s", candidate, status, elapsed(started))
    return provider, response
  raw = await response.aread()
  await response.aclose()
  raise rejected(candidate, status, started, raw)


def rejected(candidate: str, status: int, started: float, raw: bytes) -> UpstreamStatus:
  """Log an upstream error status, and return the error to raise."""
  logger.warning(
    "upstream %s %d %s: %s", candidate, status, elapsed(started), error_text(raw)
  )
  return UpstreamStatus(status, error_detail(raw, DETAIL_LIMIT))


async def post(
  candidate: str, url: str, headers: dict[str, str], **content: Any
) -> Any:
  """Send one request without a stream, and return its JSON answer."""
  started = time.perf_counter()
  response = await get_client().post(url, headers=headers, **content)
  if response.status_code >= 400:
    raise rejected(candidate, response.status_code, started, response.content)
  logger.info("upstream %s %d %s", candidate, response.status_code, elapsed(started))
  return response.json()
