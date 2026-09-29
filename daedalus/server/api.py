"""OpenAI-compatible local router for the configured providers."""

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import dashboard, providers, store
from daedalus.catalog import schedule
from daedalus.config import block_for, get_config
from daedalus.providers import hooks, signatures
from daedalus.providers.base import error_text
from daedalus.routing import (
  context,
  cooldowns,
  limits,
  loops,
  pacing,
  penalties,
  retries,
  router,
)
from daedalus.server import access, headroom, logs, media, stream, upstream
from daedalus.store import keys

HOST = os.environ.get("DAEDALUS_HOST") or "0.0.0.0"
PORT = int(os.environ.get("DAEDALUS_PORT") or 3357)
SLOW_SECONDS = upstream.WAIT_SECONDS / 2
AFFINITY = True
# The keywords of `escalation.keywords` as 1 pattern, or None when the list is empty.
KEYWORDS: re.Pattern[str] | None = None
# The keywords of `switch.keywords` as 1 pattern, or None when the list is empty.
SWITCH: re.Pattern[str] | None = None

logger = logging.getLogger("daedalus")


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
  """Set up logging and the catalog schedule, then stop both parts on shutdown."""
  logs.setup_logging()
  rebuilds = (
    asyncio.create_task(schedule.run(CATALOG_REFRESH)) if CATALOG_REFRESH else None
  )
  checks = asyncio.create_task(LIMITS.run()) if LIMIT_CHECKS else None
  yield
  for task in (rebuilds, checks):
    if task is not None:
      task.cancel()
  await upstream.close()


# The catalog rebuild for the schedule. `daedalus serve` sets it, and tests leave it off.
CATALOG_REFRESH: Callable[[], object] | None = None
# The hourly balance checks of the Limits page. `daedalus serve` turns them on.
LIMIT_CHECKS = False
app = FastAPI(title="daedalus", version="0.1.0", lifespan=lifespan)
app.include_router(media.routes)


REQUEST_FIELDS = (
  "key",
  "model",
  "pool",
  "via",
  "pin",
  "ttft",
  "fallbacks",
  "retry",
  "loop",
  "saved",
)


# The status of a request that the client closed before the answer started, as in nginx.
CANCELLED = 499


class Watch:
  """The receive side of 1 API request, which cancels the handler when the client goes early."""

  def __init__(self, receive: Any) -> None:
    self.receive = receive
    self.read, self.gone = asyncio.Event(), asyncio.Event()
    self.done = self.cancelled = False

  async def inner(self, *_: Any) -> dict:
    """The receive of the handler. After the body, only `watch` reads from the client."""
    if self.read.is_set():
      await self.gone.wait()
      return {"type": "http.disconnect"}
    message = await self.receive()
    if not message.get("more_body"):
      self.read.set()
    if message["type"] == "http.disconnect":
      self.gone.set()
    return message

  async def watch(self, task: asyncio.Task) -> None:
    """Cancel the handler when the client disconnects before the last byte of the answer."""
    await self.read.wait()
    while not self.gone.is_set():
      if (await self.receive())["type"] == "http.disconnect":
        self.gone.set()
    if not self.done:
      self.cancelled = True
      task.cancel()


class RequestLog:
  """Log each request, and keep each API request for the dashboard after its last byte.

  A plain ASGI class passes the stream chunks on with no extra queue.
  """

  def __init__(self, inner: Any) -> None:
    self.inner = inner

  async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
    if scope["type"] != "http":
      await self.inner(scope, receive, send)
      return
    started = time.perf_counter()
    request = Request(scope)
    api_post = request.method == "POST" and request.url.path.startswith("/v1/")
    if api_post:
      request.state.live = dashboard.LIVE.start(request.url.path)
    status: int | None = None
    watch = Watch(receive)

    async def sending(message: dict) -> None:
      nonlocal status
      if message["type"] == "http.response.start":
        status = message["status"]
        log_line(request, status, started)
      elif message["type"] == "http.response.body" and not message.get("more_body"):
        watch.done = True
      await send(message)

    if not api_post:
      await self.inner(scope, receive, sending)
      return
    task = asyncio.create_task(self.inner(scope, watch.inner, sending))
    watcher = asyncio.create_task(watch.watch(task))
    try:
      await task
    except asyncio.CancelledError:
      if not watch.cancelled:
        raise
    except Exception:
      if status is None:
        dashboard.LIVE.end(request.state.live, None)
      raise
    finally:
      watcher.cancel()
      seconds = time.perf_counter() - started
      if watch.cancelled:
        if status is None:
          log_line(request, CANCELLED, started)
        dashboard.record(request, status or CANCELLED, seconds, cancelled=True)
      elif status is not None:
        dashboard.record(request, status, seconds)


def log_line(request: Request, status: int, started: float) -> None:
  """Write the log line of one request when its answer starts."""
  values = {key: getattr(request.state, key, None) for key in REQUEST_FIELDS}
  if values.get("pool"):
    values["pool"] = router.pool_name(values["pool"])
  models = [f"{key}={value}" for key, value in values.items() if value]
  line = " ".join(
    [request.method, request.url.path, str(status), logs.elapsed(started)]
  )
  # The dashboard polls every few seconds, so its reads go to the debug log.
  quiet = request.method == "GET" and status < 400
  quiet = quiet and (request.url.path == "/" or request.url.path.startswith("/ui/"))
  logger.log(logging.DEBUG if quiet else logging.INFO, " ".join([line, *models]))


app.add_middleware(RequestLog)


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


def said(pattern: re.Pattern[str] | None, messages: list) -> bool:
  """Tell if the last message is a user turn with a keyword of `pattern`."""
  last = messages[-1] if messages else None
  if pattern is None or not isinstance(last, dict) or last.get("role") != "user":
    return False
  return any(pattern.search(text) for text in user_turns([last]))


def has_image(messages: object) -> bool:
  """Tell if a message of the conversation holds an image."""
  if not isinstance(messages, list):
    return False
  return any(
    isinstance(part, dict) and part.get("type") == "image_url"
    for message in messages
    if isinstance(message, dict) and isinstance(message.get("content"), list)
    for part in message["content"]
  )


def request_lines(body: dict[str, Any]) -> list[str]:
  """The chat models for one request. Tool and image requests skip the models without that feature."""
  return store.read_models(
    tools_only=bool(body.get("tools")), vision_only=has_image(body.get("messages"))
  )


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
    name
    for name, mode in router.MEDIA_POOLS.items()
    if any(router.pooled(config, m) for m in store.mode_models(mode))
  ]
  names = [router.RESERVED_MODEL, *router.POOLS, *chat, *media_pools, *others]
  data = [
    {
      "id": router.pool_name(name),
      "object": "model",
      "owned_by": "daedalus",
      **fields.get(name, {}),
    }
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
    prompt = "\n".join(user_turns(body["messages"]))
    tier, floor = router.required_tier(prompt), router.POOLS["daedalus/koinos"]
    if key and AFFINITY:
      # A conversation keeps the highest tier that it got, so a short "continue" stays up.
      tier = PENALTIES.highest(key, tier)
    # A session at koinos or higher skips the scan for tool calls.
    if tier < floor and used_tools(body["messages"]):
      tier = floor
      if key and AFFINITY:
        PENALTIES.highest(key, tier)
    if said(KEYWORDS, body["messages"]):
      tier = min(tier + 1, max(router.POOLS.values()))
      if key and AFFINITY:
        PENALTIES.highest(key, tier)
    order, slot = router.fallback_order(tier), f"{model}:{router.TIER_NAMES[tier]}"
  elif model.partition("/")[0] in config:
    return [[model]], None
  else:
    return None
  # A request with tools or images skips the models that cannot take it, and no log shows it.
  return router.chain_groups(config, request_lines(body), order), slot


def retry_chain(
  turn: retries.Turn, body: dict[str, Any], config: dict[str, Any]
) -> tuple[list[list[str]], str]:
  """The chain of a try again: 1 tier above the last answer, without the tier A models that answered."""
  tier = retries.next_tier(turn)
  lines = request_lines(body)
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
  block = block_for(config, provider_name, slug)
  tier = router.claiming_tier(block, slug) if block else None
  if first is None or POOL_NAMES.get(tier, first) == first:
    return
  request.state.pool, request.state.routed = POOL_NAMES[tier], first


PENALTIES = penalties.Penalties(lambda: store.MODELS_DB)
RETRIES = retries.Retries()
COOLDOWNS = cooldowns.Cooldowns(lambda: store.MODELS_DB)
PACING = pacing.Pacing()
PACING.config = lambda: get_config()
media.PENALTIES, media.COOLDOWNS = PENALTIES, COOLDOWNS
media.PACING = stream.PACING = PACING
LIMITS = upstream.LIMITS = limits.Limits(
  COOLDOWNS, lambda: get_config(), upstream.get_client
)
LIMITS.counted = PACING.hour_rows
app.include_router(dashboard.page())
app.include_router(
  dashboard.routes(
    PENALTIES,
    lambda: get_config(),
    lambda values: apply_settings(values),
    COOLDOWNS,
    lambda: CATALOG_REFRESH,
    LIMITS,
  )
)


class Tracker:
  """Weights and the session model of one request, keyed by its conversation."""

  def __init__(self, key: str, slot: str | None, client: str | None = None) -> None:
    self.key = key
    self.client = client
    self.slot = slot if AFFINITY else None
    self.dropped = False
    self.switched = False

  def lane(self, model: str) -> str:
    """The cooldown and pacing key of the model for this client."""
    return router.lane(get_config(), model, self.client)

  def switch(self) -> str | None:
    """Remove the session model, and return it."""
    model = PENALTIES.pinned(self.key, self.slot) if self.slot else None
    if model:
      self.switched = PENALTIES.unpin(self.key, self.slot, model)
    return model

  def order(self, groups: list[list[str]]) -> list[str]:
    """The chain: the order groups of each tier, then the weights and the session model."""
    return PENALTIES.order(router.by_order(get_config(), groups), self.key, self.slot)

  def answered(self, model: str, ttft: float) -> str | None:
    """Update the weight and pin the model. A slow success removes the pin instead."""
    slow = ttft >= SLOW_SECONDS
    PENALTIES.record(model, PENALTIES.slow if slow else PENALTIES.success)
    COOLDOWNS.succeeded(self.lane(model))
    if not self.slot:
      return None
    if slow:
      PENALTIES.unpin(self.key, self.slot, model)
      return "slow"
    state = PENALTIES.pin(self.key, self.slot, model)
    if self.switched:
      return "switched"
    return "moved" if self.dropped else state

  def failed(self, model: str, exc: Exception | None = None) -> dict[str, Any] | None:
    """Lower the weight, and remove the pin when it names this model. A rate limit also starts a cooldown and ends the counted hour."""
    limited = isinstance(exc, upstream.RateLimitError)
    if limited:
      PACING.used_up(model)
    PENALTIES.record(model, PENALTIES.rate_limit if limited else PENALTIES.fault)
    if self.slot and PENALTIES.unpin(self.key, self.slot, model):
      self.dropped = True
    return COOLDOWNS.start(self.lane(model), exc.headers, exc.body) if limited else None

  def cooled(self, ends: dict[str, float]) -> None:
    """Remove the session model when it is in a cooldown."""
    model = PENALTIES.pinned(self.key, self.slot) if self.slot else None
    if model and COOLDOWNS.until(self.lane(model), ends) is not None:
      self.dropped = PENALTIES.unpin(self.key, self.slot, model)


def tool_loop(request: Request, messages: list, pin: Tracker) -> str | None:
  """Find a tool loop, give a fault to the model that made the repeated call, and return that model."""
  found = loops.repeated_call(messages)
  if found is None:
    return None
  request.state.loop = str(found[1])
  model = loops.maker(found[0])
  if model:
    pin.failed(model)
  return model


def without_cooling(
  groups: list[list[str]], ends: dict[str, float], lane: Callable[[str], str]
) -> tuple[list[list[str]], float | None]:
  """The groups without the models in a cooldown, and the seconds to the first end when none is left."""
  left = [
    [m for m in group if COOLDOWNS.until(lane(m), ends) is None] for group in groups
  ]
  if any(left) or not any(groups):
    return left, None
  first = min(COOLDOWNS.until(lane(m), ends) or 0.0 for group in groups for m in group)
  return left, first - time.time()


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
  request.state.effort = providers.effort_text(body.get("reasoning_effort"))
  request.state.stream = body.get("stream") is True
  dashboard.live_update(
    request, model=model, effort=request.state.effort, stream=request.state.stream
  )
  if not isinstance(body.get("messages"), list):
    return upstream.error_response(
      400, "messages must be a list", "invalid_request_error"
    )
  if not model or any(not isinstance(message, dict) for message in body["messages"]):
    return upstream.error_response(
      400, "Invalid model or messages", "invalid_request_error"
    )
  model = router.built_in(model)
  if model is None:
    return upstream.error_response(
      400, "Unknown provider or pool", "invalid_request_error"
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
  name = model.partition("/")[0]
  if name in config and not router.keyed(config, model):
    return upstream.error_response(
      400, f"Missing api_key for {name}", "invalid_request_error"
    )
  if model == router.RESERVED_MODEL and found[1]:
    request.state.pool = routed_pool(found[1])
  pin = Tracker(key, found[1], getattr(request.state, "key", None))
  old = pin.switch() if said(SWITCH, body["messages"]) else None
  looping = tool_loop(request, body["messages"], pin)
  tokens, limits = context.input_tokens(body), store.input_limits()
  request.state.tokens = {"input": tokens, "estimate": True}
  attempts: list[dict[str, Any]] = []
  request.state.attempts = attempts
  # Too-small models leave the tiers before the draw, so they cannot be drawn or pinned.
  groups = [
    [m for m in group if not context.too_large(m, tokens, limits)] for group in found[0]
  ]
  if any(found[0]) and not any(groups):
    return context.too_long(tokens)
  ends = COOLDOWNS.ends()
  groups, wait = without_cooling(groups, ends, pin.lane)
  if wait is not None:
    return upstream.cooling_response(wait)
  pin.cooled(ends)
  paces = store.pace_limits()
  paced = [
    [m for m in group if not PACING.full(pin.lane(m), paces)] for group in groups
  ]
  if any(groups) and not any(paced):
    return upstream.cooling_response(
      PACING.wait(pin.lane(m) for group in groups for m in group)
    )
  models = pin.order(paced)
  if old in models:
    # A switch keyword gives the session another model. The old model is the last fallback.
    models = [m for m in models if m != old] + [old]
  if looping in models:
    # The model with the tool loop is the last fallback of this request.
    models = [m for m in models if m != looping] + [looping]
  if models:
    body, saved = await headroom.compress(body, models[0])
    if saved is not None:
      request.state.saved = str(saved)
  include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
  failure = upstream.error_response(
    502, "No model answered the request", "upstream_error"
  )
  deadline = time.perf_counter() + upstream.TIMEOUT_SECONDS
  direct_wait = (
    model not in router.POOLS
    and model != router.RESERVED_MODEL
    and (router.model_setting(config, model, "timeout") is not None)
  )
  index = 0
  while index < len(models):
    candidate = models[index]
    request.state.fallbacks = str(index)
    dashboard.live_update(request, trying=candidate, fallbacks=index)
    started, sent = time.perf_counter(), {}
    PACING.record(pin.lane(candidate), tokens)
    response = None
    try:
      # The request limit caps the wait for an answer, for all attempts.
      provider, response = await upstream.in_time(
        upstream.attempt(candidate, body, config, sent, pin.client), deadline
      )
      wait = router.model_wait(config, candidate, upstream.WAIT_SECONDS)
      if not body.get("stream"):
        raw = await upstream.in_time(upstream.read_body(response, wait), deadline)
        answer = json.loads(raw)
        if not isinstance(answer, dict) or answer.get("error"):
          raise providers.ProviderError(f"Invalid upstream answer: {error_text(raw)}")
        completion = hooks.run(
          "on-answer", config, candidate, provider.completion(answer, candidate)
        )
        if (channel := loops.answer_loop(completion)) is not None:
          raise loops.LoopError(channel)
        loops.save(loops.answer_calls(completion), candidate)
        request.state.tokens.update(
          stream.provider_count(completion.get("usage")) or {}
        )
        ttft = time.perf_counter() - started
        request.state.via, request.state.pin = candidate, pin.answered(candidate, ttft)
        request.state.ttft = logs.seconds_text(ttft)
        served(request, config, candidate)
        dashboard.live_first(request)
        remember(request, turn, candidate)
        attempts.append(upstream.note(candidate, "answered", started) | sent)
        return JSONResponse(completion)
      events = stream.sse_data(provider.stream(response, candidate, True, wait), wait)
      pending = await upstream.in_time(stream.first_content(events), deadline)
      ttft = time.perf_counter() - started
    except asyncio.TimeoutError:
      attempts.append(upstream.late_note(candidate, started) | sent)
      pin.failed(candidate)
      if response is not None:
        await response.aclose()
      failure = upstream.error_response(
        504, "Upstream provider timed out", "upstream_error"
      )
      break
    except httpx.ReadTimeout as exc:
      if direct_wait and time.perf_counter() < deadline:
        attempts.append(upstream.failure_note(candidate, started, exc) | sent)
        await asyncio.sleep(min(0.1, max(0, deadline - time.perf_counter())))
        continue
      attempts.append(upstream.failure_note(candidate, started, exc) | sent)
      pin.failed(candidate)
      failure = upstream.error_response(
        504 if direct_wait else 502,
        "Upstream provider timed out"
        if direct_wait
        else "Upstream provider attempt failed",
        "upstream_error",
      )
      index += 1
      continue
    except upstream.UpstreamStatus as exc:
      attempts.append(upstream.failure_note(candidate, started, exc) | sent)
      if started_cooldown := pin.failed(candidate, exc):
        attempts[-1]["cooldown"] = started_cooldown
      failure = upstream.error_response(
        exc.status, "Upstream provider rejected the request", "upstream_error"
      )
      index += 1
      continue
    except stream.ATTEMPT_ERRORS as exc:
      logger.warning("upstream %s failed: %s", candidate, upstream.failure_text(exc))
      attempts.append(upstream.failure_note(candidate, started, exc) | sent)
      pin.failed(candidate)
      failure = upstream.error_response(
        502, "Upstream provider attempt failed", "upstream_error"
      )
      index += 1
      continue
    rest = models[index + 1 :]
    request.state.via, request.state.pin = candidate, pin.answered(candidate, ttft)
    request.state.ttft = logs.seconds_text(ttft)
    served(request, config, candidate)
    dashboard.live_first(request)
    remember(request, turn, candidate)
    attempts.append(upstream.note(candidate, "answered", started) | sent)
    return StreamingResponse(
      stream.relay(
        pending,
        events,
        rest,
        body,
        config,
        include_usage,
        candidate,
        pin,
        attempts,
        request.state.tokens,
      ),
      media_type="text/event-stream",
    )
  return failure


def keyword_pattern(keywords: list[str]) -> re.Pattern[str] | None:
  """One pattern that finds any keyword as a whole word or phrase, in any case."""
  if not keywords:
    return None
  # A space in a phrase also matches more spaces or a line break.
  words = "|".join(
    re.escape(word).replace(r"\ ", r"\s+")
    for word in sorted(keywords, key=len, reverse=True)
  )
  return re.compile(rf"(?<!\w)(?:{words})(?!\w)", re.IGNORECASE)


def apply_settings(values: dict[str, dict[str, Any]]) -> None:
  """Use the values of `config/daedalus.yml`."""
  global SLOW_SECONDS, AFFINITY, KEYWORDS, SWITCH
  router.set_pool_names(values["pools"])
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
  loops.IDLE_SECONDS = affinity["idle"]
  PENALTIES.stay = affinity["stay"]
  headroom.TIMEOUT_SECONDS = values["headroom"]["timeout"]
  schedule.EVERY, schedule.ANCHOR = (
    values["catalog"]["every"],
    values["catalog"]["anchor"],
  )
  for name in ("success", "fault", "slow", "hourly", "rate_limit"):
    setattr(PENALTIES, name, weights[name])
  PACING.enabled = values["pacing"]["enabled"]
  KEYWORDS = keyword_pattern(values["escalation"]["keywords"])
  SWITCH = keyword_pattern(values["switch"]["keywords"])
  COOLDOWNS.first, COOLDOWNS.longest = (
    values["cooldown"]["first"],
    values["cooldown"]["longest"],
  )
  upstream.set_client(None)
