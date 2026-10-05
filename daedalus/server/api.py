"""OpenAI-compatible local router for the configured providers."""

import asyncio
import inspect
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import __version__, dashboard, providers, store
from daedalus.catalog import schedule
from daedalus.config import block_for, get_config, settings
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

DAEDALUS_HOST = "DAEDALUS_HOST"
DAEDALUS_PORT = "DAEDALUS_PORT"

HOST = os.environ.get(DAEDALUS_HOST) or "0.0.0.0"
PORT = int(os.environ.get(DAEDALUS_PORT) or 3357)
SLOW_SECONDS = 30.0
AFFINITY = True
# The keywords of `escalation.keywords` as 1 pattern, or None when the list is empty.
KEYWORDS: re.Pattern[str] | None = None
# The keywords of `switch.keywords` as 1 pattern, or None when the list is empty.
SWITCH: re.Pattern[str] | None = None
# The `affinity` settings: the mode names the session pin and the race of the next models.
AFFINITY_MODE = settings.DEFAULTS["affinity"]["mode"]
PARALLEL_ENABLED = False
PARALLEL_COUNT = 1
PARALLEL_CHANCE = 0.05
PARALLEL_SLOW_SECONDS = 30.0
PARALLEL_PENALTY = 0.9
# The upstream statuses that never mean a stream refusal: the key and the rate limit.
NOT_A_REFUSAL = (401, 403, 429)

logger = logging.getLogger("daedalus")


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
  """Set up logging and the catalog schedule, then stop both parts on shutdown."""
  logs.setup_logging()
  rebuilds = (
    asyncio.create_task(schedule.run(CATALOG_REFRESH)) if CATALOG_REFRESH else None
  )
  checks = asyncio.create_task(LIMITS.run()) if LIMIT_CHECKS else None
  prunes = asyncio.create_task(prune_store())
  yield
  for task in (rebuilds, checks, prunes):
    if task is not None:
      task.cancel()
  await upstream.close()


# A request does not prune the pins and tiers that idled: a lock on the store must not
# fail a chat. The timer does that work instead.
PRUNE_SECONDS = 600.0


async def prune_store() -> None:
  """Drop the pins and tiers that idled, on a timer, off the request path."""
  while True:
    await asyncio.sleep(PRUNE_SECONDS)
    try:
      PENALTIES.prune()
    except Exception:
      logger.exception("the pin prune failed")


# The catalog rebuild for the schedule. `daedalus serve` sets it, and tests leave it off.
CATALOG_REFRESH: Callable[[], object] | None = None
# A config-save rebuild uses cached provider snapshots only.
CATALOG_REBUILD_CACHED: Callable[[], object] | None = None
# The hourly balance checks of the Limits page. `daedalus serve` turns them on.
LIMIT_CHECKS = False
app = FastAPI(title="daedalus", version=__version__, lifespan=lifespan)
app.include_router(media.routes)


@app.exception_handler(sqlite3.OperationalError)
async def busy_store(_request: Request, exc: sqlite3.OperationalError) -> JSONResponse:
  """Answer 503 for a lock on the state store, in place of a raw 500."""
  logger.warning("the state store is busy: %s", exc)
  return upstream.error_response(
    503, "The state store is busy. Try again.", "server_error"
  )


@app.exception_handler(sqlite3.DatabaseError)
async def unreadable_store(
  _request: Request, exc: sqlite3.DatabaseError
) -> JSONResponse:
  """Answer 503 for a state store that is not a database, with the command that rebuilds it."""
  logger.error("the state store is not readable: %s", exc)
  return upstream.error_response(
    503, "The model store is not readable. Run `daedalus catalog`.", "server_error"
  )


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
  turn: retries.Turn, model: str, body: dict[str, Any], config: dict[str, Any]
) -> tuple[list[list[str]], str]:
  """The chain of a try again: the same pool for a pool request, and 1 tier up for `daedalus/auto`."""
  lines = request_lines(body)
  if model in router.POOLS:
    order, slot = router.fallback_order(router.POOLS[model]), model
  else:
    tier = retries.next_tier(turn)
    order, slot = (
      router.fallback_order(tier),
      f"{router.RESERVED_MODEL}:{router.TIER_NAMES[tier]}",
    )
  groups = router.chain_groups(config, lines, order)
  groups[0] = retries.fresh_models(turn, groups[0])
  return groups, slot


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


def tier_pool(tier: int | None) -> str | None:
  """The current client-facing pool name of a tier."""
  return next(
    (
      router.pool_name(name).removeprefix("daedalus/")
      for name, value in router.POOLS.items()
      if value == tier
    ),
    None,
  )


def pool_tier(pool: str) -> int | None:
  """The tier that owns a current client-facing pool name."""
  return next(
    (
      value
      for name, value in router.POOLS.items()
      if router.pool_name(name).removeprefix("daedalus/") == pool
    ),
    None,
  )


def model_pool(config: dict[str, Any], model: str) -> str | None:
  """The pool that owns a provider model, or None when no pool claims it."""
  name, _, slug = model.partition("/")
  block = block_for(config, name, slug)
  tier_name = router.claiming_tier(block, slug) if block else None
  tier = next(
    (value for value, name in router.TIER_NAMES.items() if name == tier_name), None
  )
  return tier_pool(tier)


def previous_auto_pin(key: str, config: dict[str, Any]) -> dict[str, str] | None:
  """The last successful model of a `daedalus/auto` or named-pool session, if one exists."""
  found = PENALTIES.last_pin(key)
  if found is None:
    return None
  slot, model = found
  if slot in router.POOLS:
    return {"pool": model_pool(config, model) or "", "model": model}
  if not slot.startswith(f"{router.RESERVED_MODEL}:"):
    return None
  tier_name = slot.rpartition(":")[2]
  tier = next(
    (value for value, name in router.TIER_NAMES.items() if name == tier_name), None
  )
  pool = tier_pool(tier) or model_pool(config, model)
  return {"pool": pool or "", "model": model}


def pool_models(config: dict[str, Any], groups: list[list[str]], pool: str) -> set[str]:
  """The candidate models of one pool in a filtered tier chain."""
  return {
    model for group in groups for model in group if model_pool(config, model) == pool
  }


def initial_transition_reason(
  config: dict[str, Any],
  previous: dict[str, str],
  candidate: str,
  turn: Any,
  code: str | None,
  messages: list,
  raw: list[list[str]],
  sized: list[list[str]],
  cooled: list[list[str]],
  paced: list[list[str]],
  switched: bool,
) -> str | None:
  """The known cause of a changed first candidate, or None for an unlabelled policy change."""
  if turn is not None and turn.count:
    return code
  pool, model = previous["pool"], previous["model"]
  if not pool:
    return None
  raw_models = pool_models(config, raw, pool)
  if model not in raw_models:
    return "hlt" if not raw_models else None
  if model not in pool_models(config, sized, pool):
    return "ctx"
  if model not in pool_models(config, cooled, pool) or model not in pool_models(
    config, paced, pool
  ):
    return "lmt"
  next_pool = model_pool(config, candidate)
  tier = pool_tier(pool)
  next_tier = pool_tier(next_pool) if next_pool is not None else None
  higher_tier = tier is not None and next_tier is not None and next_tier > tier
  if higher_tier and said(KEYWORDS, messages):
    return "esc"
  prompt_tier = router.required_tier("\n".join(user_turns(messages)))
  if higher_tier and next_tier == prompt_tier:
    return "cls"
  if next_pool == pool and candidate != model and PENALTIES.enabled and not switched:
    return "rnd"
  return None


def route_transition(
  previous_pool: str,
  previous_model: str,
  next_pool: str | None,
  next_model: str,
  reason: str | None,
) -> dict[str, str | None]:
  """The previous and next auto model for the Requests live row."""
  return {
    "from_pool": previous_pool,
    "from_model": previous_model,
    "to_pool": next_pool,
    "to_model": next_model,
    "reason": reason,
  }


def attempted_pool(config: dict[str, Any], candidate: str) -> str | None:
  """The client pool of one model, for the live row of a `daedalus/auto` request."""
  provider_name, _, slug = candidate.partition("/")
  block = block_for(config, provider_name, slug)
  tier = router.claiming_tier(block, slug) if block else None
  return POOL_NAMES.get(tier) if tier is not None else None


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
# The request-level hook files, by point, from the `hooks` group of `config/daedalus.yml`.
REQUEST_HOOKS: dict[str, str] = {}
COOLDOWNS = cooldowns.Cooldowns(lambda: store.MODELS_DB)
PACING = pacing.Pacing()
PACING.config = lambda: get_config()
media.PENALTIES, media.COOLDOWNS = PENALTIES, COOLDOWNS
media.PACING = stream.PACING = PACING
LIMITS = upstream.LIMITS = limits.Limits(
  COOLDOWNS, lambda: get_config(), upstream.get_client
)
LIMITS.counted = PACING.hour_rows


def affinity_state() -> dict[str, Any]:
  """The applied affinity values, for the dashboard status."""
  return {
    "mode": AFFINITY_MODE,
    "enabled": PARALLEL_ENABLED,
    "count": PARALLEL_COUNT,
    "chance": PARALLEL_CHANCE,
    "slow": PARALLEL_SLOW_SECONDS,
  }


app.include_router(dashboard.page())
app.include_router(
  dashboard.routes(
    PENALTIES,
    lambda: get_config(),
    lambda values: apply_settings(values),
    COOLDOWNS,
    lambda: CATALOG_REFRESH,
    LIMITS,
    lambda: CATALOG_REBUILD_CACHED,
    affinity_state,
  )
)


class Tracker:
  """Weights and the session model of one request, keyed by its conversation."""

  def __init__(self, key: str, slot: str | None, client: str | None = None) -> None:
    self.key = key
    self.client = client
    self.slot = slot if AFFINITY else None
    self.session_model = PENALTIES.pinned(key, self.slot) if self.slot else None
    self.preserve_pin = False
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
      self.preserve_pin = False
    return model

  def order(self, groups: list[list[str]]) -> list[str]:
    """The chain: the order groups of each tier, then the weights and the session model."""
    return PENALTIES.order(router.by_order(get_config(), groups), self.key, self.slot)

  def _weight(self, model: str, factor: float, attempt: dict[str, Any] | None) -> None:
    previous, weight = PENALTIES.record_change(model, factor)
    if attempt is not None and previous != weight:
      attempt["weight_change"] = {"from": previous, "to": weight}

  def answered(
    self, model: str, ttft: float, attempt: dict[str, Any] | None = None
  ) -> str | None:
    """Update the weight and pin the model. A slow success removes the pin instead."""
    slow = ttft >= SLOW_SECONDS
    self._weight(model, PENALTIES.slow if slow else PENALTIES.success, attempt)
    COOLDOWNS.succeeded(self.lane(model))
    if not self.slot:
      return None
    if slow:
      if PENALTIES.unpin(self.key, self.slot, model):
        self.preserve_pin = False
      return "slow"
    if self.preserve_pin and model != self.session_model:
      return None
    state = PENALTIES.pin(self.key, self.slot, model)
    if self.switched:
      return "switched"
    return "moved" if self.dropped else state

  def failed(
    self,
    model: str,
    exc: Exception | None = None,
    attempt: dict[str, Any] | None = None,
  ) -> dict[str, Any] | None:
    """Lower the weight, and remove the pin when it names this model. A rate limit also starts a cooldown and ends the counted hour."""
    limited = isinstance(exc, upstream.RateLimitError)
    if limited:
      PACING.used_up(model)
    self._weight(model, PENALTIES.rate_limit if limited else PENALTIES.fault, attempt)
    if self.slot and PENALTIES.unpin(self.key, self.slot, model):
      self.dropped = True
      self.preserve_pin = False
    return COOLDOWNS.start(self.lane(model), exc.headers, exc.body) if limited else None

  def cooled(self, ends: dict[str, float]) -> None:
    """Remove the session model when it is in a cooldown."""
    model = PENALTIES.pinned(self.key, self.slot) if self.slot else None
    if model and COOLDOWNS.until(self.lane(model), ends) is not None:
      self.dropped = PENALTIES.unpin(self.key, self.slot, model)
      if self.dropped:
        self.preserve_pin = False


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


@dataclass
class Try:
  """One racing attempt of a request: its model, its upstream effort and its task."""

  model: str
  sent: dict[str, Any]
  task: asyncio.Task


async def first_winner(tries: list[Try]) -> Try | None:
  """The first try with content, or None when all of them failed. A pair that lands together keeps the first try."""
  waiting = [take.task for take in tries]
  while waiting:
    done, waiting = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
    winner = next(
      (take for take in tries if take.task in done and take.task.exception() is None),
      None,
    )
    if winner is not None:
      return winner
  return None


@app.post("/v1/hook/{file:path}")
async def hook_call(request: Request, file: str) -> Response:
  """Run the `on_http` function of 1 hook file, and answer with the dict it returns.

  The file sits under `config/hooks`, and its path names it, for example `owui_auto_reasoning.py`.
  Any valid key may call it, so treat a hook file as admin code.
  """
  denied = access.check_api_key(request)
  if denied is not None:
    return denied
  try:
    body = await request.json()
  except ValueError:
    return upstream.error_response(400, "Invalid JSON", "invalid_request_error")
  if not isinstance(body, dict):
    return upstream.error_response(
      400, "The body must be an object", "invalid_request_error"
    )
  path = hooks.resolve(f"hooks/{file}")
  if path is None or not path.is_file():
    return upstream.error_response(404, f"No hook file {file}", "invalid_request_error")
  found = hooks.load(path)
  handler = getattr(found, hooks.POINTS["on-http"], None)
  if not callable(handler):
    return upstream.error_response(
      400, f"{file} has no on_http function", "invalid_request_error"
    )
  messages = body.get("messages")
  key = session_key(access.bearer(request), messages)
  call: dict[str, Any] = {}
  # A file that names `pin` gets the last pin of the chat: an older file keeps its 4 values.
  if "pin" in inspect.signature(handler).parameters:
    call["pin"] = PENALTIES.last_pin(key) if key else None
  try:
    answer = handler(
      body,
      key=key,
      prompt=first_user_text(messages),
      headers=dict(request.headers),
      **call,
    )
  except Exception as exc:
    logger.exception("on-http hook %s failed", path)
    return upstream.error_response(500, f"{file} failed: {exc}", "server_error")
  if not isinstance(answer, dict):
    return upstream.error_response(500, f"{file} returned no dict", "server_error")
  return JSONResponse(answer)


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
  request.state.session = key[:7]
  previous = (
    previous_auto_pin(key, config)
    if (model == router.RESERVED_MODEL or model in router.POOLS) and AFFINITY
    else None
  )
  turn = None
  code = None
  if REQUEST_HOOKS.get("on-request"):
    value = hooks.run_request(
      "on-request",
      REQUEST_HOOKS,
      model,
      {"key": None, "digest": retries.digest(body["messages"]), "count": None},
      headers=dict(request.headers),
    )
    found_key = value.get("key")
    if isinstance(found_key, str) and found_key:
      turn = RETRIES.turn(found_key)
      if turn.count:
        # The hook writes the code of the row: it runs again with the count filled in.
        value["count"] = turn.count
        value = hooks.run_request(
          "on-request", REQUEST_HOOKS, model, value, headers=dict(request.headers)
        )
        written = value.get("code")
        code = written if isinstance(written, str) and written else None
  if turn is not None and turn.count:
    request.state.code = code
    found = retry_chain(turn, model, body, config)
    request.state.retry = str(turn.count)
  else:
    found = chain(model, body, config, key)
  if found is None:
    return upstream.error_response(
      400, "Unknown provider or pool", "invalid_request_error"
    )
  # The tools filter can empty a pool: say why, before the request reaches an upstream.
  if body.get("tools") and found[1] and not any(found[0]):
    return upstream.error_response(
      400, "No model of this pool takes tools", "invalid_request_error"
    )
  name = model.partition("/")[0]
  if name in config and not router.keyed(config, model):
    return upstream.error_response(
      400, f"Missing api_key for {name}", "invalid_request_error"
    )
  # A hook file of the prompt point sets the reasoning effort of the attempt. The base sets
  # none, so the client value and the catalog default decide. A chain with no reasoning model
  # stays out of it.
  chain_models = [candidate for group in found[0] for candidate in group]
  flags = store.reasoning_flags()
  reasoners = [candidate for candidate in chain_models if flags.get(candidate, True)]
  prompt = "\n".join(user_turns(body["messages"]))
  if reasoners and prompt.strip():
    tier_name = (
      router.TIER_NAMES[router.POOLS[model]]
      if model in router.POOLS
      else (found[1] or "").rpartition(":")[2] or None
    )
    tier = next(
      (key for key, known in router.TIER_NAMES.items() if known == tier_name), None
    )
    paths = hooks.request_files(REQUEST_HOOKS, "on-prompt")
    values: dict[str, str | None] = {}
    if paths:
      # The heuristics read costs time, so only a hook file pays for it.
      values = hooks.run_files(
        "on-prompt",
        paths,
        values,
        messages=body["messages"],
        prompt=prompt,
        model=model,
        tier=tier,
        tier_name=tier_name,
        slot=found[1],
        reasoning=reasoners,
        effort=providers.effort_text(body.get("reasoning_effort")),
        body=body,
        config=config,
        key=key,
        app=getattr(request.state, "app", None),
      )
    chosen = providers.effort_text(values.get("reasoning_effort"))
    if chosen and providers.effort_text(body.get("reasoning_effort")) is None:
      # A hook file answers above the catalog default.
      body = {**body, "reasoning_effort": chosen}
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
  raw_groups = found[0]
  sized_groups = [
    [m for m in group if not context.too_large(m, tokens, limits)]
    for group in raw_groups
  ]
  if any(raw_groups) and not any(sized_groups):
    return context.too_long(tokens)
  ends = COOLDOWNS.ends()
  cooled_groups, wait = without_cooling(sized_groups, ends, pin.lane)
  if wait is not None:
    return upstream.cooling_response(wait)
  pin.cooled(ends)
  paces = store.pace_limits()
  paced = [
    [m for m in group if not PACING.full(pin.lane(m), paces)] for group in cooled_groups
  ]
  if any(cooled_groups) and not any(paced):
    return upstream.cooling_response(
      PACING.wait(pin.lane(m) for group in cooled_groups for m in group)
    )
  models = pin.order(paced)
  if (
    not PENALTIES.change_on_draw
    and PENALTIES.enabled
    and pin.session_model
    and not pin.dropped
    and not pin.switched
    and pin.session_model in {model for group in paced for model in group}
    and models
    and models[0] != pin.session_model
  ):
    pin.preserve_pin = True
  if old in models:
    # A switch keyword gives the session another model. The old model is the last fallback.
    models = [m for m in models if m != old] + [old]
  if looping in models:
    # The model with the tool loop is the last fallback of this request.
    models = [m for m in models if m != looping] + [looping]
  first_transition = None
  if previous and models:
    candidate_pool = model_pool(config, models[0]) or getattr(
      request.state, "pool", None
    )
    if models[0] != previous["model"] or candidate_pool != previous["pool"]:
      reason = initial_transition_reason(
        config,
        previous,
        models[0],
        turn,
        getattr(request.state, "code", None),
        body["messages"],
        raw_groups,
        sized_groups,
        cooled_groups,
        paced,
        pin.switched,
      )
      first_transition = route_transition(
        previous["pool"], previous["model"], candidate_pool, models[0], reason
      )
  if models and router.headroom_allowed(config, models[0]):
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
  # A request to a pool or to `daedalus/auto` can race the next models of its own chain. A
  # non-stream client reads our own stream from each model that can send one, so its race
  # needs no stream on the request. Only a flagged-off model holds the gate.
  raceable = bool(body.get("stream")) or any(
    router.streams_allowed(config, name) for name in models[: 1 + PARALLEL_COUNT]
  )
  runners = (
    models[1 : 1 + PARALLEL_COUNT]
    if PARALLEL_ENABLED and found[1] and raceable and len(models) > 1
    else []
  )
  # A request that may race reads our own stream from each model, so a non-stream client gets 1
  # buffered body and can race too. A request with no race keeps its plain whole body.
  racing = bool(runners)
  # Why the racers did or did not start, for the row of the dashboard.
  if not PARALLEL_ENABLED:
    request.state.race = "off"
  elif not found[1]:
    request.state.race = "pool"
  elif not raceable:
    request.state.race = "stream"
  elif len(models) <= 1:
    request.state.race = "single"

  async def start_candidate(model: str, sent: dict[str, Any]) -> dict[str, Any]:
    """Start 1 model and wait for its first content, or for the whole answer without a stream."""
    response = None
    began = time.perf_counter()
    try:
      # A non-stream client reads our own stream when this request may race, and buffers the
      # answer into 1 body. A model that cannot send a stream answers its plain body.
      buffered = (
        not body.get("stream") and racing and router.streams_allowed(config, model)
      )
      asked = {**body, "stream": True} if buffered else body
      try:
        provider, response = await upstream.in_time(
          upstream.attempt(model, asked, config, sent, pin.client),
          deadline,
        )
      except upstream.UpstreamStatus as exc:
        # A status that names the request shape is a stream refusal. Try this model once
        # without a stream, and buffer its whole body. The key, the rate limit and a provider
        # fault keep their own paths.
        if not buffered or exc.status in NOT_A_REFUSAL or exc.status >= 500:
          raise
        attempts.append(upstream.note(model, "stream refused", began, exc.detail))
        logger.info("upstream %s refused the stream, retrying without one", model)
        buffered, response = False, None
        provider, response = await upstream.in_time(
          upstream.attempt(model, body, config, sent, pin.client),
          deadline,
        )
      wait = router.model_wait(config, model, upstream.WAIT_SECONDS)
      if not buffered and not body.get("stream"):
        raw = await upstream.in_time(upstream.read_body(response, wait), deadline)
        return {
          "provider": provider,
          "response": response,
          "wait": wait,
          "raw": raw,
          "at": time.perf_counter(),
        }
      events = stream.sse_data(provider.stream(response, model, True, wait), wait)
      pending = await upstream.in_time(stream.first_content(events), deadline)
      return {
        "provider": provider,
        "response": response,
        "wait": wait,
        "events": events,
        "pending": pending,
        "at": time.perf_counter(),
      }
    except BaseException:
      # An attempt that ends early, a cancel included, gives its connection back.
      if response is not None:
        await response.aclose()
      raise

  async def race_models(
    first: str, others: list[str], sent: dict[str, Any], started: float
  ) -> Try:
    """Race the models for the first content. Each loser stops, with its own fault or with the race factor."""
    takes = [Try(first, sent, asyncio.create_task(start_candidate(first, sent)))]
    try:
      # A draw starts the other models now. Without it, only a first model with no content starts them.
      quick = bool(PARALLEL_CHANCE) and PENALTIES.pick() < PARALLEL_CHANCE
      if not quick:
        await asyncio.wait([takes[0].task], timeout=PARALLEL_SLOW_SECONDS)
      if quick or not takes[0].task.done():
        request.state.race = "drawn" if quick else "slow"
        for model in others:
          PACING.record(pin.lane(model), tokens)
          other: dict[str, Any] = {}
          takes.append(
            Try(model, other, asyncio.create_task(start_candidate(model, other)))
          )
      winner = await first_winner(takes)
      for take in takes:
        if take is winner:
          continue
        if not take.task.done():
          take.task.cancel()
          with suppress(asyncio.CancelledError):
            await take.task
          exc = None
        else:
          exc = take.task.exception()
          if exc is None:
            content = take.task.result()
            if "events" in content:
              await content["events"].aclose()
            else:
              # A non-stream loser is already whole, and its body is dropped with its connection.
              await content["response"].aclose()
        if exc is not None:
          if take.model != first:
            # A racing model that failed takes its own fault, as a fallback does.
            attempts.append(upstream.failure_note(take.model, started, exc) | take.sent)
            pin.failed(take.model, exc, attempts[-1])
          continue
        attempts.append(upstream.note(take.model, "lost race", started) | take.sent)
        PENALTIES.record(take.model, PARALLEL_PENALTY)
        logger.info("parallel %s lost the race", take.model)
      if winner is None:
        raise takes[0].task.exception()
      if len(takes) == 1:
        request.state.race = "fast"
      # The row reads the race: the winner carries the mark, and a runner that wins takes the pin.
      winner.sent["race"] = "won"
      if winner.model != first:
        request.state.transition = "rce"
      return winner
    finally:
      # The winner keeps its content. The other tasks are over.
      for take in takes:
        take.task.cancel()

  index = 0
  previous_attempt: tuple[str, str] | None = None
  fallback_reason = "err"
  while index < len(models):
    candidate = models[index]
    candidate_pool = model_pool(config, candidate) or getattr(request.state, "pool", "")
    transition = first_transition if index == 0 else None
    if index and previous_attempt:
      transition = route_transition(
        previous_attempt[1],
        previous_attempt[0],
        candidate_pool,
        candidate,
        fallback_reason,
      )
    request.state.transition = transition
    request.state.fallbacks = str(index)
    # The pool of the tried model lands on the live row now, not at the first token.
    shown: dict[str, Any] = {
      "trying": candidate,
      "fallbacks": index,
      "session": request.state.session,
      "transition": transition,
    }
    pool = getattr(request.state, "pool", None) or attempted_pool(config, candidate)
    if pool:
      shown["pool"] = router.pool_name(pool)
    dashboard.live_update(request, **shown)
    started, sent = time.perf_counter(), {}
    PACING.record(pin.lane(candidate), tokens)
    try:
      # The request limit caps the wait for an answer, for all attempts.
      if not runners or index:
        content = await start_candidate(candidate, sent)
      else:
        winner = await race_models(candidate, runners, sent, started)
        candidate, sent = winner.model, winner.sent
        content = winner.task.result()
      wait = content["wait"]
      if not body.get("stream"):
        events = content.get("events")
        if events is None:
          raw = content["raw"]
          answer = json.loads(raw)
          if not isinstance(answer, dict) or answer.get("error"):
            raise providers.ProviderError(f"Invalid upstream answer: {error_text(raw)}")
          answer = content["provider"].completion(answer, candidate)
        else:
          # A non-stream client behind our own stream: buffer the events into 1 answer.
          answer = await upstream.in_time(
            stream.cache(content["pending"], events, candidate), deadline
          )
        completion = hooks.run("on-answer", config, candidate, answer)
        if (channel := loops.answer_loop(completion)) is not None:
          raise loops.LoopError(channel)
        loops.save(loops.answer_calls(completion), candidate)
        request.state.tokens.update(
          stream.provider_count(completion.get("usage")) or {}
        )
        ttft = time.perf_counter() - started
        attempts.append(
          upstream.note(candidate, "answered", started, ended=content["at"]) | sent
        )
        request.state.via, request.state.pin = (
          candidate,
          pin.answered(candidate, ttft, attempts[-1]),
        )
        request.state.ttft = logs.seconds_text(ttft)
        served(request, config, candidate)
        dashboard.live_first(request)
        remember(request, turn, candidate)
        return JSONResponse(completion)
      events, pending = content["events"], content["pending"]
      ttft = time.perf_counter() - started
    except asyncio.TimeoutError:
      attempts.append(upstream.late_note(candidate, started) | sent)
      pin.failed(candidate, attempt=attempts[-1])
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
      pin.failed(candidate, attempt=attempts[-1])
      failure = upstream.error_response(
        504 if direct_wait else 502,
        "Upstream provider timed out"
        if direct_wait
        else "Upstream provider attempt failed",
        "upstream_error",
      )
      previous_attempt = (candidate, candidate_pool)
      fallback_reason = "err"
      index += 1
      continue
    except upstream.UpstreamStatus as exc:
      attempts.append(upstream.failure_note(candidate, started, exc) | sent)
      if started_cooldown := pin.failed(candidate, exc, attempts[-1]):
        attempts[-1]["cooldown"] = started_cooldown
      failure = upstream.error_response(
        exc.status, "Upstream provider rejected the request", "upstream_error"
      )
      previous_attempt = (candidate, candidate_pool)
      fallback_reason = "lmt" if isinstance(exc, upstream.RateLimitError) else "err"
      index += 1
      continue
    except stream.ATTEMPT_ERRORS as exc:
      logger.warning("upstream %s failed: %s", candidate, upstream.failure_text(exc))
      attempts.append(upstream.failure_note(candidate, started, exc) | sent)
      pin.failed(candidate, attempt=attempts[-1])
      failure = upstream.error_response(
        502, "Upstream provider attempt failed", "upstream_error"
      )
      previous_attempt = (candidate, candidate_pool)
      fallback_reason = "err"
      index += 1
      continue
    rest = [m for m in models[index + 1 :] if m != candidate]
    attempts.append(
      upstream.note(candidate, "answered", started, ended=content["at"]) | sent
    )
    request.state.via, request.state.pin = (
      candidate,
      pin.answered(candidate, ttft, attempts[-1]),
    )
    request.state.ttft = logs.seconds_text(ttft)
    served(request, config, candidate)
    dashboard.live_first(request)
    remember(request, turn, candidate)

    def stream_transition(previous_model: str, next_model: str, reason: str) -> None:
      previous_pool = model_pool(config, previous_model) or ""
      next_pool = model_pool(config, next_model) or ""
      transition = route_transition(
        previous_pool, previous_model, next_pool, next_model, reason
      )
      fallbacks = len(attempts)
      request.state.transition = transition
      request.state.fallbacks = str(fallbacks)
      dashboard.live_update(
        request,
        trying=next_model,
        fallbacks=fallbacks,
        transition=transition,
      )

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
        stream_transition if model == router.RESERVED_MODEL else None,
        requested=model,
        stream_context={
          "pool": getattr(request.state, "pool", "") or "",
          "code": getattr(request.state, "code", "") or "",
          "previous": (previous or {}).get("model", ""),
        },
        hook_files=hooks.request_files(REQUEST_HOOKS, "on-chunk"),
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
  global SLOW_SECONDS, AFFINITY, AFFINITY_MODE, KEYWORDS, SWITCH
  global PARALLEL_ENABLED, PARALLEL_COUNT, PARALLEL_CHANCE, PARALLEL_SLOW_SECONDS
  global PARALLEL_PENALTY, REQUEST_HOOKS
  # The settings key each pool by its generic name. Its default value gives the built-in name.
  router.set_pool_names(
    {settings.DEFAULTS["pools"][key]: name for key, name in values["pools"].items()}
  )
  REQUEST_HOOKS = dict(values["request_hooks"])
  router.set_headroom(values["headroom"]["enabled"])
  timeouts, affinity, weights = (
    values["timeouts"],
    values["affinity"],
    values["weights"],
  )
  # 1 mode names the pin and the race: none is neither, session is the pin, race is both.
  AFFINITY_MODE = affinity["mode"]
  AFFINITY = AFFINITY_MODE != "none"
  PARALLEL_ENABLED = AFFINITY_MODE == "race"
  (
    PARALLEL_COUNT,
    PARALLEL_CHANCE,
    PARALLEL_SLOW_SECONDS,
    PARALLEL_PENALTY,
  ) = (
    affinity["count"],
    affinity["chance"],
    affinity["slow"],
    affinity["penalty"],
  )
  PENALTIES.race = PARALLEL_ENABLED
  upstream.TIMEOUT_SECONDS, upstream.WAIT_SECONDS = (
    timeouts["request"],
    timeouts["wait"],
  )
  SLOW_SECONDS = timeouts["slow"]
  PENALTIES.idle, PENALTIES.enabled = affinity["idle"], weights["enabled"]
  PENALTIES.change_on_draw = affinity["change_on_draw"]
  signatures.IDLE_SECONDS = RETRIES.idle = media.REPEATS.idle = affinity["idle"]
  loops.IDLE_SECONDS = affinity["idle"]
  loops.CALLS = values["loops"]["calls"]
  loops.REPEATS = values["loops"]["repeats"]
  loops.SHORTEST = values["loops"]["shortest"]
  loops.LONGEST = values["loops"]["longest"]
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
