"""OpenAI-compatible local router for the configured providers."""

import asyncio
import hmac
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import (
  dashboard,
  keys,
  penalties,
  providers,
  router,
  schedule,
  signatures,
  store,
  stream,
)
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
  """Set up logging and the catalog schedule, then stop both parts on shutdown."""
  setup_logging()
  rebuilds = (
    asyncio.create_task(schedule.run(CATALOG_REFRESH)) if CATALOG_REFRESH else None
  )
  yield
  if rebuilds is not None:
    rebuilds.cancel()
  if _client is not None:
    await _client.aclose()
    set_client(None)


# The catalog rebuild for the schedule. `daedalus serve` sets it, and tests leave it off.
CATALOG_REFRESH: Callable[[], object] | None = None
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
  return keys.bearer(request.headers.get("authorization", ""))


def check_api_key(request: Request) -> JSONResponse | None:
  """Reject the request unless the bearer token is the master key or an API key."""
  token, master = bearer(request), dashboard.master()
  if master is not None and hmac.compare_digest(token.encode(), master.encode()):
    request.state.key = "master"
    return None
  name = keys.find(store.MODELS_DB, token)
  if name is not None:
    request.state.key = name
    return None
  return error_response(
    401,
    "Send the master key or an API key as 'Authorization: Bearer <key>'.",
    "authentication_error",
  )


@app.middleware("http")
async def log_request(request: Request, call_next):
  started = time.perf_counter()
  response = await call_next(request)
  models = [
    f"{key}={getattr(request.state, key)}"
    for key in ("key", "model", "pool", "via", "pin", "ttft", "fallbacks")
    if getattr(request.state, key, None)
  ]
  line = " ".join(
    [request.method, request.url.path, str(response.status_code), elapsed(started)]
  )
  # The dashboard polls every few seconds, so its reads go to the debug log.
  quiet = request.method == "GET" and response.status_code < 400
  quiet = quiet and (request.url.path == "/" or request.url.path.startswith("/ui/"))
  logger.log(logging.DEBUG if quiet else logging.INFO, " ".join([line, *models]))
  if request.url.path == "/v1/chat/completions":
    dashboard.record(request, response.status_code, time.perf_counter() - started)
  return response


def user_text(messages: object, last: bool = True) -> str:
  """The text of the last or first user turn, whatever shape its content takes."""
  if not isinstance(messages, list):
    return ""
  for message in reversed(messages) if last else messages:
    if not isinstance(message, dict) or message.get("role") != "user":
      continue
    content = message.get("content")
    if isinstance(content, str):
      return content
    if isinstance(content, list):
      parts = [part.get("text", "") for part in content if isinstance(part, dict)]
      return "\n".join(part for part in parts if part)
  return ""


def last_user_text(messages: object) -> str:
  """The text of the last user turn."""
  return user_text(messages)


def session_key(token: str, messages: object) -> str:
  """The conversation key: the hash of the bearer token and the first user message."""
  return keys.digest(f"{token}\n{user_text(messages, last=False)}")


@app.get("/health")
async def health() -> dict[str, str]:
  return {"status": "ok"}


@app.get("/v1/models")
async def models(request: Request) -> Response:
  denied = check_api_key(request)
  if denied is not None:
    return denied
  names = [
    router.RESERVED_MODEL,
    *router.POOLS,
    router.PRAKTOS,
    *store.read_models(),
  ]
  data = [{"id": name, "object": "model", "owned_by": "daedalus"} for name in names]
  return JSONResponse({"object": "list", "data": data})


def chain(
  model: str, body: dict[str, Any], config: dict[str, Any]
) -> tuple[list[list[str]], str | None] | None:
  """The tier groups to try and the pin slot for one request, or None for an unknown name."""
  if model == router.PRAKTOS or (model == router.RESERVED_MODEL and body.get("tools")):
    lines = store.read_models(tools_only=True)
    groups = router.chain_groups(config, lines, router.PRAKTOS_TIERS)
    if any(groups):
      return groups, router.PRAKTOS
    logger.warning("praktos has no member; using the daedalus/auto chain")
    model = router.RESERVED_MODEL
  if model in router.POOLS:
    order = router.fallback_order(router.POOLS[model])
    return router.chain_groups(config, store.read_models(), order), model
  if model == router.RESERVED_MODEL:
    tier = router.required_tier(last_user_text(body["messages"]))
    order = router.fallback_order(tier)
    groups = router.chain_groups(config, store.read_models(), order)
    return groups, f"{model}:{router.TIER_NAMES[tier]}"
  if model.partition("/")[0] in config:
    return [[model]], None
  return None


# Parts that hold binary data. Their text does not count as input tokens.
BINARY_KEYS = frozenset({"image_url", "input_audio", "file"})


def text_length(node: object) -> int:
  """The characters of all strings in a message tree, without binary parts."""
  if isinstance(node, str):
    return len(node)
  if isinstance(node, list):
    return sum(text_length(item) for item in node)
  if isinstance(node, dict):
    return sum(
      text_length(value) for key, value in node.items() if key not in BINARY_KEYS
    )
  return 0


def input_tokens(body: dict[str, Any]) -> int:
  """An estimate of the input tokens: the characters of the messages and tools, divided by 4."""
  characters = text_length(body.get("messages")) + text_length(body.get("tools"))
  return -(-characters // 4)


def too_large(candidate: str, tokens: int, limits: dict[str, int]) -> bool:
  """Tell if the input does not fit the model, and log the skip."""
  limit = limits.get(candidate)
  if limit is None or tokens <= limit:
    return False
  logger.warning("skip %s: input ~%d tokens > limit %d", candidate, tokens, limit)
  return True


def routed_pool(slot: str) -> str:
  """The short name of the pool that serves a `daedalus/auto` slot."""
  if slot == router.PRAKTOS:
    return slot.rpartition("/")[2]
  names = {router.TIER_NAMES[tier]: name for name, tier in router.POOLS.items()}
  return names[slot.rpartition(":")[2]].rpartition("/")[2]


PENALTIES = penalties.Penalties(lambda: store.MODELS_DB)
app.include_router(dashboard.page())
app.include_router(
  dashboard.routes(
    PENALTIES, lambda: get_config(), lambda values: apply_settings(values)
  )
)


class Tracker:
  """Weights and the session model of one request, keyed by its conversation."""

  def __init__(self, key: str, slot: str | None) -> None:
    self.key = key
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
  denied = check_api_key(request)
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
  pin = Tracker(session_key(bearer(request), body["messages"]), found[1])
  models = pin.order(found[0])
  include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
  failure = error_response(502, "No model answered the request", "upstream_error")
  tokens, limits, tried = input_tokens(body), store.input_limits(), False
  for index, candidate in enumerate(models):
    request.state.fallbacks = str(index)
    if too_large(candidate, tokens, limits):
      continue
    tried = True
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
      events = stream.sse_data(provider.stream(response, candidate, include_usage))
      pending = await stream.first_content(events)
      ttft = time.perf_counter() - started
    except UpstreamStatus as exc:
      pin.failed(candidate)
      failure = error_response(
        exc.status, "Upstream provider rejected the request", "upstream_error"
      )
      continue
    except stream.ATTEMPT_ERRORS as exc:
      logger.warning("upstream %s failed: %s", candidate, failure_text(exc))
      pin.failed(candidate)
      failure = error_response(
        502, "Upstream provider attempt failed", "upstream_error"
      )
      continue
    rest = [m for m in models[index + 1 :] if not too_large(m, tokens, limits)]
    request.state.via, request.state.pin = candidate, pin.answered(candidate, ttft)
    request.state.ttft = seconds_text(ttft)
    return StreamingResponse(
      stream.relay(pending, events, rest, body, config, include_usage, candidate, pin),
      media_type="text/event-stream",
    )
  if models and not tried:
    return too_long(tokens)
  return failure


def too_long(tokens: int) -> JSONResponse:
  """The OpenAI error for an input that no model in the chain can take."""
  message = (
    f"The input (~{tokens} tokens) is larger than the context window of each model"
  )
  body = {
    "error": {
      "message": message,
      "type": "invalid_request_error",
      "code": "context_length_exceeded",
    }
  }
  return JSONResponse(body, status_code=400)


def failure_text(exc: Exception) -> str:
  """The error type and its message, for one log line."""
  detail = error_text(str(exc)) if str(exc) else ""
  return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class UpstreamStatus(Exception):
  def __init__(self, status: int) -> None:
    super().__init__(status)
    self.status = status


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
  signatures.IDLE_SECONDS = affinity["idle"]
  PENALTIES.stay = affinity["stay"]
  schedule.EVERY, schedule.ANCHOR = (
    values["catalog"]["every"],
    values["catalog"]["anchor"],
  )
  for name in ("success", "fault", "slow", "hourly"):
    setattr(PENALTIES, name, weights[name])
  set_client(None)
