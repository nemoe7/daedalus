"""Message compression through an optional Headroom sidecar, before the routing."""

import asyncio
import logging
import os
from typing import Any

import httpx

from daedalus.server import upstream

URL_ENV = "HEADROOM_URL"
MODE = "lossy_inline"
TIMEOUT_SECONDS = 5.0
HEALTH_TIMEOUT_SECONDS = 1.0

logger = logging.getLogger("daedalus")
_down = False


def base_url() -> str:
  """The Headroom address from the environment, or an empty string when it is off."""
  return os.environ.get(URL_ENV, "").strip().rstrip("/")


async def available() -> bool:
  """Whether the sidecar answers its health check as ready."""
  base = base_url()
  if not base:
    return False
  try:
    response = await asyncio.wait_for(
      upstream.get_client().get(f"{base}/health", timeout=HEALTH_TIMEOUT_SECONDS),
      HEALTH_TIMEOUT_SECONDS,
    )
    if not response.is_success:
      return False
    health = response.json()
  except (httpx.HTTPError, ValueError, TypeError, asyncio.TimeoutError):
    return False
  return (
    isinstance(health, dict)
    and health.get("status") == "healthy"
    and health.get("ready", True) is not False
  )


async def compress(
  body: dict[str, Any], model: str
) -> tuple[dict[str, Any], int | None]:
  """The body with shorter messages and the saved tokens, or the same body and None."""
  global _down
  base = base_url()
  if not base:
    return body, None
  try:
    # The httpx timeout is for each read, so a sidecar that keeps sending bytes needs the total limit.
    response = await asyncio.wait_for(
      upstream.get_client().post(
        f"{base}/v1/compress",
        json={"messages": body["messages"], "model": model, "config": {"mode": MODE}},
        timeout=TIMEOUT_SECONDS,
      ),
      TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    answer = response.json()
    messages = answer["messages"]
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
      raise ValueError("messages must be a list of objects")
    saved = int(answer.get("tokens_saved") or 0)
  except (
    httpx.HTTPError,
    ValueError,
    KeyError,
    TypeError,
    asyncio.TimeoutError,
  ) as exc:
    if not _down:
      if isinstance(exc, asyncio.TimeoutError):
        detail = f"no answer in {TIMEOUT_SECONDS:g}s"
      elif isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP {exc.response.status_code}"
      else:
        detail = upstream.failure_text(exc)
      logger.warning("headroom failed, sending the original messages: %s", detail)
    _down = True
    return body, None
  if _down:
    logger.info("headroom answers again")
  _down = False
  return {**body, "messages": messages}, saved
