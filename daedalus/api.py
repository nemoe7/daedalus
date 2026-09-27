"""OpenAI-compatible local router for the configured providers."""

import argparse
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from secrets import compare_digest

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import catalog, providers, router
from daedalus.config import get_config

# When set, clients must send "Authorization: Bearer <LOCAL_API_KEY>".
LOCAL_API_KEY = ""

HOST = "0.0.0.0"
PORT = 8080
TIMEOUT_SECONDS = 600.0
# ADR 2: the wait for one answer. A provider that sends bytes resets it, so a slow
# stream is not cut off.
WAIT_SECONDS = 60.0

logger = logging.getLogger("daedalus")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
  """Set up logging, then close the upstream client on shutdown."""
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
  yield
  if _client is not None:
    await _client.aclose()
    set_client(None)


app = FastAPI(title="daedalus", version="0.1.0", lifespan=lifespan)

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


def error_response(status: int, message: str, error_type: str) -> JSONResponse:
  """Answer in the OpenAI error shape."""
  return JSONResponse(
    status_code=status,
    content={"error": {"message": message, "type": error_type, "code": status}},
  )


def check_local_key(request: Request) -> JSONResponse | None:
  """Reject the request when a local key is set and not matched."""
  if not LOCAL_API_KEY:
    return None
  header = request.headers.get("authorization", "")
  token = header[7:].strip() if header.lower().startswith("bearer ") else ""
  if compare_digest(token, LOCAL_API_KEY):
    return None
  return error_response(
    401,
    "Invalid local API key. Send 'Authorization: Bearer <LOCAL_API_KEY>'.",
    "authentication_error",
  )


@app.middleware("http")
async def log_request(request: Request, call_next):
  started = time.perf_counter()
  response = await call_next(request)
  elapsed_ms = (time.perf_counter() - started) * 1000
  logger.info(
    "%s %s %s %.1fms",
    request.method,
    request.url.path,
    response.status_code,
    elapsed_ms,
  )
  return response


def last_user_text(messages: object) -> str:
  """The text of the last user turn, whatever shape its content takes."""
  if not isinstance(messages, list):
    return ""
  for message in reversed(messages):
    if not isinstance(message, dict) or message.get("role") != "user":
      continue
    content = message.get("content")
    if isinstance(content, str):
      return content
    if isinstance(content, list):
      parts = [part.get("text", "") for part in content if isinstance(part, dict)]
      return "\n".join(part for part in parts if part)
  return ""


@app.get("/health")
async def health() -> dict[str, str]:
  return {"status": "ok"}


@app.get("/v1/models")
async def models(request: Request) -> Response:
  denied = check_local_key(request)
  if denied is not None:
    return denied
  names = [router.RESERVED_MODEL, *router.POOLS, *catalog.read_models_txt()]
  data = [{"id": name, "object": "model", "owned_by": "daedalus"} for name in names]
  return JSONResponse({"object": "list", "data": data})


@app.post("/v1/chat/completions")
async def chat(request: Request) -> Response:
  denied = check_local_key(request)
  if denied is not None:
    return denied
  try:
    body = await request.json()
  except ValueError:
    return error_response(400, "Invalid JSON", "invalid_request_error")
  if not isinstance(body, dict) or not isinstance(body.get("model"), str):
    return error_response(400, "A model is required", "invalid_request_error")
  model = body["model"]
  if not isinstance(body.get("messages"), list):
    return error_response(400, "messages must be a list", "invalid_request_error")
  if not model or any(not isinstance(message, dict) for message in body["messages"]):
    return error_response(400, "Invalid model or messages", "invalid_request_error")
  if "stream" in body and not isinstance(body["stream"], bool):
    return error_response(400, "stream must be boolean", "invalid_request_error")
  if body.get("stream_options") is not None and not isinstance(
    body["stream_options"], dict
  ):
    return error_response(
      400, "stream_options must be an object", "invalid_request_error"
    )
  config = get_config()
  if model in router.POOLS:
    models = router.route_pool(model, config, catalog.read_models_txt())
  elif model == router.RESERVED_MODEL:
    models = router.route(
      last_user_text(body["messages"]), config, catalog.read_models_txt()
    )
  elif model.partition("/")[0] in config:
    models = [model]
  else:
    return error_response(400, "Unknown provider or pool", "invalid_request_error")
  failure = error_response(502, "No model answered the request", "upstream_error")
  for candidate in models:
    response = None
    try:
      url, payload, headers, kind = providers.prepare(candidate, body, config)
      upstream = get_client().build_request("POST", url, json=payload, headers=headers)
      response = await get_client().send(upstream, stream=True)
      if response.status_code >= 400:
        await response.aread()
        failure = error_response(
          response.status_code,
          "Upstream provider rejected the request",
          "upstream_error",
        )
        await response.aclose()
        continue
      if not body.get("stream"):
        raw = await response.aread()
        await response.aclose()
        answer = json.loads(raw)
        if not isinstance(answer, dict) or answer.get("error"):
          raise providers.ProviderError("Invalid upstream answer")
        if kind != "openai":
          answer = providers.completion(answer, kind, candidate)
        return JSONResponse(answer)
      iterator = (
        stream_from(response)
        if kind == "openai"
        else providers.stream(
          response,
          kind,
          candidate,
          bool((body.get("stream_options") or {}).get("include_usage")),
        )
      )
      first = await anext(iterator)
      return StreamingResponse(
        client_stream(iterator, first), media_type="text/event-stream"
      )
    except (
      httpx.HTTPError,
      ValueError,
      KeyError,
      TypeError,
      StopAsyncIteration,
    ) as exc:
      if response is not None:
        await response.aclose()
      logger.warning(
        "provider attempt failed for %s: %s", candidate, type(exc).__name__
      )
      failure = error_response(
        502, "Upstream provider attempt failed", "upstream_error"
      )
  return failure


async def client_stream(
  iterator: AsyncIterator[bytes], first: bytes
) -> AsyncIterator[bytes]:
  try:
    yield first
    async for piece in iterator:
      yield piece
  except (httpx.HTTPError, ValueError, KeyError, TypeError):
    yield providers.frame(
      {
        "error": {
          "message": "Upstream stream failed",
          "type": "upstream_error",
          "code": 502,
        }
      }
    )
  finally:
    await iterator.aclose()


def stream_from(upstream_response: httpx.Response) -> AsyncIterator[bytes]:
  """Pass one upstream answer through, unbuffered."""

  async def stream() -> AsyncIterator[bytes]:
    try:
      async for chunk in upstream_response.aiter_bytes():
        if chunk:
          yield chunk
    finally:
      await upstream_response.aclose()

  return stream()


def run() -> None:
  """Entry point for `daedalus` and `uvicorn daedalus.api:app`."""
  argparse.ArgumentParser(
    prog="daedalus",
    description=f"Start the OpenAI-compatible router on {HOST}:{PORT}.",
  ).parse_args()
  import uvicorn

  uvicorn.run(app, host=HOST, port=PORT)
