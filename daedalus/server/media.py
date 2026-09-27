"""The OpenAI endpoints for models that do not chat, each for one model with no fallback."""

import base64
import struct
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from daedalus import providers
from daedalus.config import get_config
from daedalus.routing import router
from daedalus.server import access, stream, upstream

routes = APIRouter()


def invalid(message: str) -> JSONResponse:
  return upstream.error_response(400, message, "invalid_request_error")


async def json_body(request: Request) -> dict | Response:
  """The JSON body with its model, or the error answer."""
  try:
    body = await request.json()
  except ValueError:
    return invalid("Invalid JSON")
  if not isinstance(body, dict) or not isinstance(body.get("model"), str):
    return invalid("A model is required")
  request.state.model = body["model"]
  return body


def provider_for(model: str) -> tuple[providers.OpenAIProvider, str] | Response:
  """The provider and the slug of a `provider/slug` model, or the error answer."""
  if model == router.RESERVED_MODEL or model in router.POOLS:
    return invalid("This endpoint needs a provider/slug model")
  try:
    return providers.provider_for(model, get_config())
  except providers.ProviderError as exc:
    return invalid(str(exc))


async def attempt(
  request: Request, model: str, call: Callable[[], Awaitable[Any]]
) -> Any:
  """Run the one upstream call, and record it for the dashboard."""
  attempts: list[dict[str, Any]] = []
  request.state.attempts, request.state.fallbacks = attempts, "0"
  started = time.perf_counter()
  try:
    answer = await call()
  except upstream.UpstreamStatus as exc:
    attempts.append(upstream.failure_note(model, started, exc))
    return upstream.error_response(
      exc.status, "Upstream provider rejected the request", "upstream_error"
    )
  except stream.ATTEMPT_ERRORS as exc:
    attempts.append(upstream.failure_note(model, started, exc))
    return upstream.error_response(
      502, "Upstream provider attempt failed", "upstream_error"
    )
  request.state.via = model
  attempts.append(upstream.note(model, "answered", started))
  return answer


def packed(vector: list[float]) -> str:
  """The base64 text of a vector as little-endian float32 values."""
  return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()


def text_input(value: Any) -> bool:
  """Accept a string, a list of strings, a token list, or a list of token lists."""
  if isinstance(value, str):
    return bool(value)
  if not isinstance(value, list) or not value:
    return False
  if all(isinstance(item, int) for item in value):
    return True
  return all(
    isinstance(item, str)
    or (isinstance(item, list) and all(isinstance(t, int) for t in item))
    for item in value
  )


@routes.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
  denied = access.check_api_key(request)
  if denied is not None:
    return denied
  body = await json_body(request)
  if isinstance(body, Response):
    return body
  if not text_input(body.get("input")):
    return invalid("input must be a string or a list of strings or tokens")
  encoding = body.get("encoding_format", "float")
  if encoding not in ("float", "base64"):
    return invalid("encoding_format must be float or base64")
  model = body["model"]
  found = provider_for(model)
  if isinstance(found, Response):
    return found
  provider, slug = found
  try:
    url, payload, headers = provider.embed_request(slug, body)
  except providers.ProviderError as exc:
    return invalid(str(exc))

  async def call() -> dict:
    answer = await upstream.post(model, url, headers, json=payload)
    return provider.embeddings(answer, model)

  result = await attempt(request, model, call)
  if isinstance(result, Response):
    return result
  if encoding == "base64":
    for item in result["data"]:
      item["embedding"] = packed(item["embedding"])
  return JSONResponse(result)
