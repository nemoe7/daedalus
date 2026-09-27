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

from daedalus import dashboard, providers, store
from daedalus.catalog import schedule
from daedalus.config import get_config
from daedalus.providers import signatures
from daedalus.providers.base import error_text
from daedalus.routing import context, penalties, retries, router
from daedalus.server import access, headroom, logs, media, stream, upstream
from daedalus.store import keys

HOST = os.environ.get("DAEDALUS_HOST") or "0.0.0.0"
PORT = int(os.environ.get("DAEDALUS_PORT") or 3357)
SLOW_SECONDS = upstream.WAIT_SECONDS / 2
AFFINITY = True

logger = logging.getLogger("daedalus")


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
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
app.include_router(media.routes)


@app.middleware("http")
async def log_request(request: Request, call_next):
  started = time.perf_counter()
  response = await call_next(request)
  models = [
    f"{key}={getattr(request.state, key)}"
    for key in (
      "key",
      "model",
      "pool",
      "via",
      "pin",
      "ttft",
      "fallbacks",
      "retry",
      "saved",
    )
    if getattr(request.state, key, None)
  ]
  line = " ".join(
    [request.method, request.url.path, str(response.status_code), logs.elapsed(started)]
  )
  # The dashboard polls every few seconds, so its reads go to the debug log.
  quiet = request.method == "GET" and response.status_code < 400
  quiet = quiet and (request.url.path == "/" or request.url.path.startswith("/ui/"))
  logger.log(logging.DEBUG if quiet else logging.INFO, " ".join([line, *models]))
  if request.method == "POST" and request.url.path.startswith("/v1/"):
    dashboard.record(request, response.status_code, time.perf_counter() - started)
  return response


def user_turns(messages: object) -> list[str]:
  """The text of each user turn, oldest first, whatever shape its content takes."""
  if not isinstance(messages, list):
    return []
  texts = []
  for message in messages:
    if not isinstance(message, dict) or message.get("role") != "user":
      continue
    content = message.get("content")
    if isinstance(content, list):
      parts = [part.get("text", "") for part in content if isinstance(part, dict)]
      content = "\n".join(part for part in parts if isinstance(part, str) and part)
    if isinstance(content, str):
      texts.append(content)
  return texts


def used_tools(messages: list) -> bool:
  """Tell if the conversation already has a tool call or a tool result."""
  return any(
    isinstance(m, dict)
    and (
      m.get("role") in ("tool", "function")
      or m.get("tool_calls")
      or m.get("function_call")
    )
    for m in messages
  )


def first_user_text(messages: object) -> str:
  texts = user_turns(messages)
  return texts[0] if texts else ""


def session_key(token: str, messages: object) -> str:
  """The conversation key: the hash of the bearer token and the first user message."""
  return keys.digest(f"{token}\n{first_user_text(messages)}")


@app.get("/health")
async def health() -> dict[str, str]:
  return {"status": "ok"}


@app.get("/v1/models")
async def models(request: Request) -> Response:
  denied = access.check_api_key(request)
  if denied is not None:
    return denied
  chat = store.read_models()
  known = set(chat)
  others = [m for m in store.read_models(routable_only=False) if m not in known]
  info = store.model_info()
  fields = {name: info.get(name, {}) for name in chat}
  config = get_config()
  for name, tier in router.POOLS.items():
    members = router.candidates(config, router.TIER_NAMES[tier], chat)
    fields[name] = pool_info([fields[m] for m in members])
  fields[router.RESERVED_MODEL] = fields["daedalus/sophos"]
  media_pools = [
    name for name, mode in router.MEDIA_POOLS.items() if store.mode_models(mode)
  ]
  names = [router.RESERVED_MODEL, *router.POOLS, *chat, *media_pools, *others]
  data = [
    {"id": name, "object": "model", "owned_by": "daedalus", **fields.get(name, {})}
    for name in names
  ]
  return JSONResponse({"object": "list", "data": data})


def pool_info(members: list[dict[str, int | bool]]) -> dict[str, int | bool]:
  """The highest limit and any true flag among the members of one pool."""
  found: dict[str, int | bool] = {}
  for fields in members:
    for name, value in fields.items():
      found[name] = max(found.get(name, value), value)
  return found


def chain(
  model: str, body: dict[str, Any], config: dict[str, Any], key: str = ""
) -> tuple[list[list[str]], str | None] | None:
  """The tier groups to try and the pin slot for one request, or None for an unknown name."""
  if model in router.POOLS:
    order, slot = router.fallback_order(router.POOLS[model]), model
  elif model == router.RESERVED_MODEL:
    tier = router.required_tier("\n".join(user_turns(body["messages"])))
    if used_tools(body["messages"]):
      tier = max(tier, router.POOLS["daedalus/koinos"])
    if key and AFFINITY:
      # A conversation keeps the highest tier that it got, so a short "continue" stays up.
      tier = PENALTIES.highest(key, tier)
    order, slot = router.fallback_order(tier), f"{model}:{router.TIER_NAMES[tier]}"
  elif model.partition("/")[0] in config:
    return [[model]], None
  else:
    return None
  # A request with tools skips the models that cannot call tools, and no log shows it.
  lines = store.read_models(tools_only=bool(body.get("tools")))
  return router.chain_groups(config, lines, order), slot


def retry_chain(
  turn: retries.Turn, body: dict[str, Any], config: dict[str, Any]
) -> tuple[list[list[str]], str]:
  """The chain of a try again: 1 tier above the last answer, without the tier A models that answered."""
  tier = retries.next_tier(turn)
  lines = store.read_models(tools_only=bool(body.get("tools")))
  groups = router.chain_groups(config, lines, router.fallback_order(tier))
  groups[0] = retries.fresh_models(turn, groups[0])
  return groups, f"{router.RESERVED_MODEL}:{router.TIER_NAMES[tier]}"


def remember(request: Request, turn: retries.Turn | None, candidate: str) -> None:
  """Keep the tier of the pool that answered, for the next try again of this message."""
  if turn is None:
    return
  pool = getattr(request.state, "pool", None)
  retries.record(turn, router.POOLS.get(f"daedalus/{pool}"), candidate)


# The short pool name of each tier name, for example moros for TIER-D.
POOL_NAMES = {
  router.TIER_NAMES[tier]: name.rpartition("/")[2]
  for name, tier in router.POOLS.items()
}


def routed_pool(slot: str) -> str:
  """The short name of the pool that serves a `daedalus/auto` slot."""
  return POOL_NAMES[slot.rpartition(":")[2]]


def served(request: Request, config: dict[str, Any], candidate: str) -> None:
  """Show the pool of the model that answers, and keep the first pool when it differs."""
  first = getattr(request.state, "pool", None)
  provider_name, _, slug = candidate.partition("/")
  provider = config.get(provider_name)
  tier = router.claiming_tier(provider, slug) if isinstance(provider, dict) else None
  if first is None or POOL_NAMES.get(tier, first) == first:
    return
  request.state.pool, request.state.routed = POOL_NAMES[tier], first


PENALTIES = penalties.Penalties(lambda: store.MODELS_DB)
RETRIES = retries.Retries()
media.PENALTIES = PENALTIES
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
  key = session_key(access.bearer(request), body["messages"])
  turn = None
  chat_id = request.headers.get(retries.CHAT_HEADER)
  if model == router.RESERVED_MODEL and chat_id:
    turn = RETRIES.start(chat_id, body["messages"])
  if turn is not None and turn.count:
    found = retry_chain(turn, body, config)
    request.state.retry = str(turn.count)
  else:
    found = chain(model, body, config, key)
  if found is None:
    return upstream.error_response(
      400, "Unknown provider or pool", "invalid_request_error"
    )
  if model == router.RESERVED_MODEL and found[1]:
    request.state.pool = routed_pool(found[1])
  pin = Tracker(key, found[1])
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
        served(request, config, candidate)
        remember(request, turn, candidate)
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
    served(request, config, candidate)
    remember(request, turn, candidate)
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
  signatures.IDLE_SECONDS = RETRIES.idle = media.REPEATS.idle = affinity["idle"]
  PENALTIES.stay = affinity["stay"]
  headroom.TIMEOUT_SECONDS = values["headroom"]["timeout"]
  schedule.EVERY, schedule.ANCHOR = (
    values["catalog"]["every"],
    values["catalog"]["anchor"],
  )
  for name in ("success", "fault", "slow", "hourly"):
    setattr(PENALTIES, name, weights[name])
  upstream.set_client(None)
