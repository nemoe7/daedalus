"""OpenAI-compatible local router for the configured providers."""

import argparse
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import catalog, keys, providers, router
from daedalus.config import get_config

HOST = "0.0.0.0"
PORT = 3357
TIMEOUT_SECONDS = 600.0
# ADR 2: the wait for one answer. A provider that sends bytes resets it, so a slow
# stream is not cut off.
WAIT_SECONDS = 60.0

logger = logging.getLogger("daedalus")
LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s %(message)s"


def setup_logging() -> None:
  """Write every log line in one format, without Uvicorn access or httpx request lines."""
  logging.addLevelName(logging.WARNING, "WARN")
  logging.basicConfig(
    level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S", force=True
  )
  logging.getLogger("httpx").setLevel(logging.WARNING)
  for handler in logging.getLogger().handlers:
    handler.addFilter(short_name)


def short_name(record: logging.LogRecord) -> bool:
  """Show Uvicorn server lines as `uvicorn`, not as `uvicorn.error`."""
  if record.name == "uvicorn.error":
    record.name = "uvicorn"
  return True


def elapsed(started: float) -> str:
  """The milliseconds since `started`, as log text."""
  return f"{(time.perf_counter() - started) * 1000:.0f}ms"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
  """Set up logging, then close the upstream client on shutdown."""
  setup_logging()
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
  header = request.headers.get("authorization", "")
  token = header[7:].strip() if header.lower().startswith("bearer ") else ""
  if keys.matches(catalog.MODELS_DB, token) is not False:
    return None
  return error_response(
    401,
    "Invalid local API key. Send 'Authorization: Bearer <key>'. Set it with daedalus -k.",
    "authentication_error",
  )


@app.middleware("http")
async def log_request(request: Request, call_next):
  started = time.perf_counter()
  response = await call_next(request)
  models = [
    f"{key}={getattr(request.state, key)}"
    for key in ("model", "via")
    if getattr(request.state, key, None)
  ]
  line = " ".join(
    [request.method, request.url.path, str(response.status_code), elapsed(started)]
  )
  logger.info(" ".join([line, *models]))
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
  names = [
    router.RESERVED_MODEL,
    *router.POOLS,
    router.PRAKTOS,
    *catalog.read_models(),
  ]
  data = [{"id": name, "object": "model", "owned_by": "daedalus"} for name in names]
  return JSONResponse({"object": "list", "data": data})


def chain(model: str, body: dict[str, Any], config: dict[str, Any]) -> list[str] | None:
  """The models to try for one request, or None for an unknown name."""
  if model == router.PRAKTOS or (model == router.RESERVED_MODEL and body.get("tools")):
    models = router.route_praktos(config, catalog.read_models(tools_only=True))
    if models:
      return models
    logger.warning("praktos has no member; using the daedalus/auto chain")
    model = router.RESERVED_MODEL
  if model in router.POOLS:
    return router.route_pool(model, config, catalog.read_models())
  if model == router.RESERVED_MODEL:
    prompt = last_user_text(body["messages"])
    return router.route(prompt, config, catalog.read_models())
  if model.partition("/")[0] in config:
    return [model]
  return None


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
  request.state.model = model
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
  models = chain(model, body, config)
  if models is None:
    return error_response(400, "Unknown provider or pool", "invalid_request_error")
  include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
  failure = error_response(502, "No model answered the request", "upstream_error")
  for index, candidate in enumerate(models):
    try:
      provider, response = await attempt(candidate, body, config)
      if not body.get("stream"):
        raw = await response.aread()
        await response.aclose()
        answer = json.loads(raw)
        if not isinstance(answer, dict) or answer.get("error"):
          raise providers.ProviderError("Invalid upstream answer")
        request.state.via = candidate
        return JSONResponse(provider.completion(answer, candidate))
      events = sse_data(provider.stream(response, candidate, include_usage))
      first = await anext(events)
    except UpstreamStatus as exc:
      failure = error_response(
        exc.status, "Upstream provider rejected the request", "upstream_error"
      )
      continue
    except ATTEMPT_ERRORS as exc:
      logger.warning("upstream %s failed: %s", candidate, type(exc).__name__)
      failure = error_response(
        502, "Upstream provider attempt failed", "upstream_error"
      )
      continue
    rest = models[index + 1 :]
    request.state.via = candidate
    return StreamingResponse(
      relay(first, events, rest, body, config, include_usage),
      media_type="text/event-stream",
    )
  return failure


class UpstreamStatus(Exception):
  def __init__(self, status: int) -> None:
    super().__init__(status)
    self.status = status


STREAM_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError)
ATTEMPT_ERRORS = (*STREAM_ERRORS, StopAsyncIteration)
STREAM_FAILED = providers.frame(
  {
    "error": {
      "message": "Upstream stream failed",
      "type": "upstream_error",
      "code": 502,
    }
  }
)


async def attempt(
  candidate: str, body: dict[str, Any], config: dict[str, Any]
) -> tuple[providers.OpenAIProvider, httpx.Response]:
  """Send one candidate request, and fail on an upstream error status."""
  provider, url, payload, headers = providers.prepare(candidate, body, config)
  upstream = get_client().build_request("POST", url, json=payload, headers=headers)
  started = time.perf_counter()
  response = await get_client().send(upstream, stream=True)
  level = logging.WARNING if response.status_code >= 400 else logging.INFO
  logger.log(
    level, "upstream %s %d %s", candidate, response.status_code, elapsed(started)
  )
  if response.status_code >= 400:
    await response.aread()
    await response.aclose()
    raise UpstreamStatus(response.status_code)
  return provider, response


async def sse_data(chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
  """Split an SSE byte stream into the payloads of its `data:` lines."""
  buffer = b""
  try:
    async for piece in chunks:
      buffer += piece.replace(b"\r\n", b"\n")
      while b"\n\n" in buffer:
        block, buffer = buffer.split(b"\n\n", 1)
        lines = [
          line[5:].strip() for line in block.split(b"\n") if line.startswith(b"data:")
        ]
        if lines:
          yield b"\n".join(lines).decode()
  finally:
    await chunks.aclose()


async def relay(
  first: str,
  events: AsyncIterator[str],
  rest: list[str],
  body: dict[str, Any],
  config: dict[str, Any],
  include_usage: bool,
) -> AsyncIterator[bytes]:
  """Stream one answer, and continue from the sent text on a failure (ADR 2)."""
  identifier, sent, tool = None, [], False
  pending: list[str] = [first]
  while True:
    try:
      while True:
        data = pending.pop(0) if pending else await anext(events)
        if data == "[DONE]":
          yield b"data: [DONE]\n\n"
          return
        chunk = json.loads(data)
        if not isinstance(chunk, dict) or chunk.get("error"):
          raise providers.ProviderError("Upstream stream error")
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
    except STREAM_ERRORS as exc:
      logger.warning("upstream stream failed: %s", type(exc).__name__)
    await events.aclose()
    if tool:
      yield STREAM_FAILED
      return
    prefix = {"role": "assistant", "content": "".join(sent)}
    continued = {**body, "messages": [*body["messages"], prefix]} if sent else body
    while rest:
      candidate = rest.pop(0)
      try:
        provider, response = await attempt(candidate, continued, config)
        events = sse_data(provider.stream(response, candidate, include_usage))
        pending = [await anext(events)]
        break
      except (UpstreamStatus, *ATTEMPT_ERRORS) as exc:
        logger.warning(
          "upstream %s continuation failed: %s", candidate, type(exc).__name__
        )
    else:
      yield STREAM_FAILED
      return


KEY_OFF = "off"


def set_key(value: str) -> None:
  """Store a new, custom, or no local API key. Show a new key once."""
  if value == KEY_OFF:
    keys.save_hash(catalog.MODELS_DB, None)
    logger.info("removed the local API key; the router accepts all requests")
    return
  key = value or keys.generate()
  keys.save_hash(catalog.MODELS_DB, keys.digest(key))
  if not value:
    print(key)
  logger.info("stored the local API key hash in %s", catalog.MODELS_DB)


def run(argv: list[str] | None = None) -> None:
  """Entry point for `daedalus`."""
  parser = argparse.ArgumentParser(
    prog="daedalus", description="OpenAI-compatible router for the providers."
  )
  parser.add_argument(
    "-i",
    "--init",
    nargs="?",
    const=PORT,
    type=int,
    metavar="PORT",
    help=f"start the router on {HOST}:PORT (default {PORT}); build a missing model store first",
  )
  parser.add_argument(
    "-c",
    "--catalog",
    action="store_true",
    help="discover provider models and rebuild the model store",
  )
  parser.add_argument(
    "-d",
    "--dump",
    action="store_true",
    help="write the raw model list of each provider to .daedalus-state/dump",
  )
  parser.add_argument(
    "-k",
    "--key",
    nargs="?",
    const="",
    metavar="KEY",
    help=f"set the local API key: a new key without KEY, your KEY, or '{KEY_OFF}' to remove it",
  )
  args = parser.parse_args(argv)
  serve = args.init is not None
  if args.key not in (None, "", KEY_OFF) and not keys.valid(args.key):
    parser.error(
      f"a custom key needs {keys.MIN_LENGTH} or more characters and no spaces"
    )
  if not (serve or args.catalog or args.dump or args.key is not None):
    parser.print_help()
    return
  setup_logging()
  if args.key is not None:
    set_key(args.key)
  if args.dump:
    catalog.dump()
  if args.catalog or (serve and not catalog.has_store()):
    catalog.refresh()
  if not serve:
    return
  import uvicorn

  uvicorn.run(app, host=HOST, port=args.init, log_config=None, access_log=False)
