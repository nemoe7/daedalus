"""OpenAI-compatible local router for the configured providers."""

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import (
  access,
  context,
  dashboard,
  headroom,
  keys,
  logs,
  penalties,
  providers,
  router,
  schedule,
  signatures,
  store,
  stream,
  upstream,
)
from daedalus.config import get_config
from daedalus.providers.base import error_text

HOST = os.environ.get("DAEDALUS_HOST") or "0.0.0.0"
PORT = int(os.environ.get("DAEDALUS_PORT") or 3357)
SLOW_SECONDS = upstream.WAIT_SECONDS / 2
AFFINITY = True

logger = logging.getLogger("daedalus")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
  """Set up logging and the catalog schedule, then stop both parts on shutdown."""
  logs.setup_logging()
  rebuilds = (
    asyncio.create_task(schedule.run(CATALOG_REFRESH)) if CATALOG_REFRESH else None
  )
  yield
  if rebuilds is not None:
    rebuilds.cancel()
  await upstream.close()


# The catalog rebuild for the schedule. `daedalus serve` sets it, and tests leave it off.
CATALOG_REFRESH: Callable[[], object] | None = None
app = FastAPI(title="daedalus", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def log_request(request: Request, call_next):
  started = time.perf_counter()
  response = await call_next(request)
  models = [
    f"{key}={getattr(request.state, key)}"
    for key in ("key", "model", "pool", "via", "pin", "ttft", "fallbacks", "saved")
    if getattr(request.state, key, None)
  ]
  line = " ".join(
    [request.method, request.url.path, str(response.status_code), logs.elapsed(started)]
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
  denied = access.check_api_key(request)
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
  denied = access.check_api_key(request)
  if denied is not None:
    return denied
  try:
    body = await request.json()
  except ValueError:
    return upstream.error_response(400, "Invalid JSON", "invalid_request_error")
  if not isinstance(body, dict) or not isinstance(body.get("model"), str):
    return upstream.error_response(400, "A model is required", "invalid_request_error")
  model = body["model"]
  request.state.model = model
  if not isinstance(body.get("messages"), list):
    return upstream.error_response(
      400, "messages must be a list", "invalid_request_error"
    )
  if not model or any(not isinstance(message, dict) for message in body["messages"]):
    return upstream.error_response(
      400, "Invalid model or messages", "invalid_request_error"
    )
  if "stream" in body and not isinstance(body["stream"], bool):
    return upstream.error_response(
      400, "stream must be boolean", "invalid_request_error"
    )
  if body.get("stream_options") is not None and not isinstance(
    body["stream_options"], dict
  ):
    return upstream.error_response(
      400, "stream_options must be an object", "invalid_request_error"
    )
  config = get_config()
  found = chain(model, body, config)
  if found is None:
    return upstream.error_response(
      400, "Unknown provider or pool", "invalid_request_error"
    )
  if model == router.RESERVED_MODEL and found[1]:
    request.state.pool = routed_pool(found[1])
  pin = Tracker(session_key(access.bearer(request), body["messages"]), found[1])
  tokens, limits = context.input_tokens(body), store.input_limits()
  attempts: list[dict[str, Any]] = []
  request.state.attempts = attempts
  # Too-small models leave the tiers before the draw, so they cannot be drawn or pinned.
  groups = [
    [m for m in group if not context.too_large(m, tokens, limits)] for group in found[0]
  ]
  models = pin.order(groups)
  if not models and any(found[0]):
    return context.too_long(tokens)
  if models:
    body, saved = await headroom.compress(body, models[0])
    if saved is not None:
      request.state.saved = str(saved)
  include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
  failure = upstream.error_response(
    502, "No model answered the request", "upstream_error"
  )
  for index, candidate in enumerate(models):
    request.state.fallbacks = str(index)
    started = time.perf_counter()
    try:
      provider, response = await upstream.attempt(candidate, body, config)
      if not body.get("stream"):
        raw = await response.aread()
        await response.aclose()
        answer = json.loads(raw)
        if not isinstance(answer, dict) or answer.get("error"):
          raise providers.ProviderError(f"Invalid upstream answer: {error_text(raw)}")
        completion = provider.completion(answer, candidate)
        ttft = time.perf_counter() - started
        request.state.via, request.state.pin = candidate, pin.answered(candidate, ttft)
        request.state.ttft = logs.seconds_text(ttft)
        attempts.append(upstream.note(candidate, "answered", started))
        return JSONResponse(completion)
      events = stream.sse_data(provider.stream(response, candidate, include_usage))
      pending = await stream.first_content(events)
      ttft = time.perf_counter() - started
    except upstream.UpstreamStatus as exc:
      attempts.append(upstream.failure_note(candidate, started, exc))
      pin.failed(candidate)
      failure = upstream.error_response(
        exc.status, "Upstream provider rejected the request", "upstream_error"
      )
      continue
    except stream.ATTEMPT_ERRORS as exc:
      logger.warning("upstream %s failed: %s", candidate, upstream.failure_text(exc))
      attempts.append(upstream.failure_note(candidate, started, exc))
      pin.failed(candidate)
      failure = upstream.error_response(
        502, "Upstream provider attempt failed", "upstream_error"
      )
      continue
    rest = models[index + 1 :]
    request.state.via, request.state.pin = candidate, pin.answered(candidate, ttft)
    request.state.ttft = logs.seconds_text(ttft)
    attempts.append(upstream.note(candidate, "answered", started))
    return StreamingResponse(
      stream.relay(
        pending, events, rest, body, config, include_usage, candidate, pin, attempts
      ),
      media_type="text/event-stream",
    )
  return failure


def apply_settings(values: dict[str, dict[str, Any]]) -> None:
  """Use the values of `config/daedalus.yml`."""
  global SLOW_SECONDS, AFFINITY
  timeouts, affinity, weights = (
    values["timeouts"],
    values["session_affinity"],
    values["weights"],
  )
  upstream.TIMEOUT_SECONDS, upstream.WAIT_SECONDS = (
    timeouts["request"],
    timeouts["wait"],
  )
  SLOW_SECONDS, AFFINITY = timeouts["slow"], affinity["enabled"]
  PENALTIES.idle, PENALTIES.enabled = affinity["idle"], weights["enabled"]
  signatures.IDLE_SECONDS = affinity["idle"]
  PENALTIES.stay = affinity["stay"]
  headroom.TIMEOUT_SECONDS = values["headroom"]["timeout"]
  schedule.EVERY, schedule.ANCHOR = (
    values["catalog"]["every"],
    values["catalog"]["anchor"],
  )
  for name in ("success", "fault", "slow", "hourly"):
    setattr(PENALTIES, name, weights[name])
  upstream.set_client(None)
