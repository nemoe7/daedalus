"""OpenAI-compatible local router for the configured providers."""

import argparse
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import catalog, keys, penalties, providers, router, settings
from daedalus.config import get_config
from daedalus.providers.base import error_text

HOST = os.environ.get("DAEDALUS_HOST") or "0.0.0.0"
PORT = int(os.environ.get("DAEDALUS_PORT") or 3357)
TIMEOUT_SECONDS = 600.0
# The wait for one answer. A provider that sends bytes resets it, so a slow
# stream is not cut off.
WAIT_SECONDS = 60.0
SLOW_SECONDS = WAIT_SECONDS / 2
AFFINITY = True

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


def seconds_text(seconds: float) -> str:
  """A duration as log text, in seconds with 3 decimals."""
  return f"{seconds:.3f}s"


def elapsed(started: float) -> str:
  """The time since `started`, as log text."""
  return seconds_text(time.perf_counter() - started)


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


def bearer(request: Request) -> str:
  header = request.headers.get("authorization", "")
  return header[7:].strip() if header.lower().startswith("bearer ") else ""


def check_local_key(request: Request) -> JSONResponse | None:
  """Reject the request when a local key is set and not matched."""
  if keys.matches(catalog.MODELS_DB, bearer(request)) is not False:
    return None
  return error_response(
    401,
    "Invalid local API key. Send 'Authorization: Bearer <key>'. Set it with daedalus key.",
    "authentication_error",
  )


@app.middleware("http")
async def log_request(request: Request, call_next):
  started = time.perf_counter()
  response = await call_next(request)
  models = [
    f"{key}={getattr(request.state, key)}"
    for key in ("model", "pool", "via", "pin", "ttft", "fallbacks")
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


def chain(
  model: str, body: dict[str, Any], config: dict[str, Any]
) -> tuple[list[list[str]], str | None] | None:
  """The tier groups to try and the pin slot for one request, or None for an unknown name."""
  if model == router.PRAKTOS or (model == router.RESERVED_MODEL and body.get("tools")):
    lines = catalog.read_models(tools_only=True)
    groups = router.chain_groups(config, lines, router.PRAKTOS_TIERS)
    if any(groups):
      return groups, router.PRAKTOS
    logger.warning("praktos has no member; using the daedalus/auto chain")
    model = router.RESERVED_MODEL
  if model in router.POOLS:
    order = router.fallback_order(router.POOLS[model])
    return router.chain_groups(config, catalog.read_models(), order), model
  if model == router.RESERVED_MODEL:
    tier = router.required_tier(last_user_text(body["messages"]))
    order = router.fallback_order(tier)
    groups = router.chain_groups(config, catalog.read_models(), order)
    return groups, f"{model}:{router.TIER_NAMES[tier]}"
  if model.partition("/")[0] in config:
    return [[model]], None
  return None


def routed_pool(slot: str) -> str:
  """The short name of the pool that serves a `daedalus/auto` slot."""
  if slot == router.PRAKTOS:
    return slot.rpartition("/")[2]
  names = {router.TIER_NAMES[tier]: name for name, tier in router.POOLS.items()}
  return names[slot.rpartition(":")[2]].rpartition("/")[2]


PENALTIES = penalties.Penalties(lambda: catalog.MODELS_DB)


class Tracker:
  """Weights and the pin of one request, keyed by the hash of its API key."""

  def __init__(self, token: str, slot: str | None) -> None:
    self.key = keys.digest(token) if token else ""
    self.slot = slot if AFFINITY else None
    self.dropped = False

  def order(self, groups: list[list[str]]) -> list[str]:
    return PENALTIES.order(groups, self.key, self.slot)

  def answered(self, model: str, ttft: float) -> str | None:
    """Update the weight and pin the model. A slow success removes the pin instead."""
    slow = ttft >= SLOW_SECONDS
    PENALTIES.record(model, PENALTIES.slow if slow else PENALTIES.success)
    if not self.slot:
      return None
    if slow:
      PENALTIES.unpin(self.key, self.slot, model)
      return "slow"
    state = PENALTIES.pin(self.key, self.slot, model)
    return "moved" if self.dropped else state

  def failed(self, model: str) -> None:
    """Lower the weight, and remove the pin when it names this model."""
    PENALTIES.record(model, PENALTIES.fault)
    if self.slot and PENALTIES.unpin(self.key, self.slot, model):
      self.dropped = True


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
  found = chain(model, body, config)
  if found is None:
    return error_response(400, "Unknown provider or pool", "invalid_request_error")
  if model == router.RESERVED_MODEL and found[1]:
    request.state.pool = routed_pool(found[1])
  pin = Tracker(bearer(request), found[1])
  models = pin.order(found[0])
  include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
  failure = error_response(502, "No model answered the request", "upstream_error")
  for index, candidate in enumerate(models):
    request.state.fallbacks = str(index)
    started = time.perf_counter()
    try:
      provider, response = await attempt(candidate, body, config)
      if not body.get("stream"):
        raw = await response.aread()
        await response.aclose()
        answer = json.loads(raw)
        if not isinstance(answer, dict) or answer.get("error"):
          raise providers.ProviderError(f"Invalid upstream answer: {error_text(raw)}")
        completion = provider.completion(answer, candidate)
        ttft = time.perf_counter() - started
        request.state.via, request.state.pin = candidate, pin.answered(candidate, ttft)
        request.state.ttft = seconds_text(ttft)
        return JSONResponse(completion)
      events = sse_data(provider.stream(response, candidate, include_usage))
      pending = await first_content(events)
      ttft = time.perf_counter() - started
    except UpstreamStatus as exc:
      pin.failed(candidate)
      failure = error_response(
        exc.status, "Upstream provider rejected the request", "upstream_error"
      )
      continue
    except ATTEMPT_ERRORS as exc:
      logger.warning("upstream %s failed: %s", candidate, failure_text(exc))
      pin.failed(candidate)
      failure = error_response(
        502, "Upstream provider attempt failed", "upstream_error"
      )
      continue
    rest = models[index + 1 :]
    request.state.via, request.state.pin = candidate, pin.answered(candidate, ttft)
    request.state.ttft = seconds_text(ttft)
    return StreamingResponse(
      relay(pending, events, rest, body, config, include_usage, candidate, pin),
      media_type="text/event-stream",
    )
  return failure


def failure_text(exc: Exception) -> str:
  """The error type and its message, for one log line."""
  detail = error_text(str(exc)) if str(exc) else ""
  return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


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
  status = response.status_code
  if status < 400:
    logger.info("upstream %s %d %s", candidate, status, elapsed(started))
    return provider, response
  raw = await response.aread()
  await response.aclose()
  logger.warning(
    "upstream %s %d %s: %s", candidate, status, elapsed(started), error_text(raw)
  )
  raise UpstreamStatus(status)


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
  pin: Tracker,
) -> AsyncIterator[bytes]:
  """Stream one answer, and continue from the sent text on a failure."""
  identifier, sent, tool = None, [], False
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
      logger.warning("upstream stream failed: %s", failure_text(exc))
    await events.aclose()
    pin.failed(model)
    if tool:
      yield STREAM_FAILED
      return
    prefix = {"role": "assistant", "content": "".join(sent)}
    continued = {**body, "messages": [*body["messages"], prefix]} if sent else body
    while rest:
      candidate = rest.pop(0)
      started = time.perf_counter()
      try:
        provider, response = await attempt(candidate, continued, config)
        events = sse_data(provider.stream(response, candidate, include_usage))
        pending = await first_content(events)
        model = candidate
        pin.answered(model, time.perf_counter() - started)
        break
      except (UpstreamStatus, *ATTEMPT_ERRORS) as exc:
        logger.warning(
          "upstream %s continuation failed: %s", candidate, failure_text(exc)
        )
    else:
      yield STREAM_FAILED
      return


KEY_OFF = "off"


def apply_settings(values: dict[str, dict[str, Any]]) -> None:
  """Use the timeouts, session affinity, and weights of `config/daedalus.yml`."""
  global TIMEOUT_SECONDS, WAIT_SECONDS, SLOW_SECONDS, AFFINITY
  timeouts, affinity, weights = (
    values["timeouts"],
    values["session_affinity"],
    values["weights"],
  )
  TIMEOUT_SECONDS, WAIT_SECONDS = timeouts["request"], timeouts["wait"]
  SLOW_SECONDS, AFFINITY = timeouts["slow"], affinity["enabled"]
  PENALTIES.idle, PENALTIES.enabled = affinity["idle"], weights["enabled"]
  for name in ("success", "fault", "slow", "hourly"):
    setattr(PENALTIES, name, weights[name])
  set_client(None)


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
  commands = parser.add_subparsers(dest="command", metavar="COMMAND")
  serve = commands.add_parser(
    "serve",
    help=f"start the router on {HOST}:PORT; build a missing model store first",
  )
  serve.add_argument(
    "port", nargs="?", default=PORT, type=int, help=f"default {PORT}, or DAEDALUS_PORT"
  )
  serve.add_argument(
    "--catalog", action="store_true", help="rebuild the model store first"
  )
  commands.add_parser(
    "catalog", help="discover provider models and rebuild the model store"
  )
  commands.add_parser(
    "dump", help="write the raw model list of each provider to .daedalus-state/dump"
  )
  key = commands.add_parser(
    "key",
    help=f"set the local API key: a new key without KEY, your KEY, or '{KEY_OFF}' to remove it",
  )
  key.add_argument("key", nargs="?", default="", metavar="KEY")
  args = parser.parse_args(argv)
  if args.command is None:
    parser.print_help()
    return
  if (
    args.command == "key" and args.key not in ("", KEY_OFF) and not keys.valid(args.key)
  ):
    key.error(f"a custom key needs {keys.MIN_LENGTH} or more characters and no spaces")
  setup_logging()
  if args.command == "key":
    set_key(args.key)
  elif args.command == "dump":
    catalog.dump()
  elif args.command == "catalog":
    catalog.refresh()
  else:
    try:
      apply_settings(settings.load())
    except (settings.SettingsError, yaml.YAMLError) as exc:
      parser.exit(2, f"daedalus: {exc}\n")
    if args.catalog or not catalog.has_store():
      catalog.refresh()
    import uvicorn

    uvicorn.run(app, host=HOST, port=args.port, log_config=None, access_log=False)
