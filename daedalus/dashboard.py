"""The dashboard JSON under `/ui/api`, the recent-request list and the access test."""

import base64
import binascii
import os
import secrets
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from daedalus import keys, router, settings, store
from daedalus.penalties import Penalties

# The chat requests that the dashboard shows. A restart clears them.
RECENT: deque[dict[str, Any]] = deque(maxlen=50)
FIELDS = ("model", "pool", "via", "ttft", "fallbacks")
AUTO_TIERS = (4, 3, 2, 1)


def record(request: Request, status: int, seconds: float) -> None:
  """Keep one chat request for the dashboard."""
  found = {key: getattr(request.state, key, None) for key in FIELDS}
  RECENT.appendleft({"at": time.time(), "status": status, "seconds": seconds, **found})


def login() -> tuple[str, str] | None:
  """The dashboard username and password, when both env vars have a value."""
  user = os.environ.get("DAEDALUS_USERNAME", "")
  password = os.environ.get("DAEDALUS_PASSWORD", "")
  return (user, password) if user and password else None


def basic(request: Request) -> tuple[str, str] | None:
  """The username and password of an HTTP Basic header."""
  header = request.headers.get("authorization", "")
  if not header.lower().startswith("basic "):
    return None
  try:
    text = base64.b64decode(header[6:].strip(), validate=True).decode()
  except (binascii.Error, UnicodeDecodeError):
    return None
  user, separator, password = text.partition(":")
  return (user, password) if separator else None


def same(given: str, expected: str) -> bool:
  return secrets.compare_digest(given.encode(), expected.encode())


def allowed(request: Request) -> bool:
  """Accept the local key or the login. With neither one set, accept all requests."""
  stored, expected = keys.stored_hash(store.MODELS_DB), login()
  if stored is None and expected is None:
    return True
  if stored is not None and keys.matches(
    store.MODELS_DB, keys.bearer(request.headers.get("authorization", ""))
  ):
    return True
  given = basic(request)
  if expected is None or given is None:
    return False
  user, password = same(given[0], expected[0]), same(given[1], expected[1])
  return user and password


def denied() -> JSONResponse:
  message = "Send the local API key as a bearer token, or the dashboard login."
  error = {"message": message, "type": "authentication_error", "code": 401}
  return JSONResponse(status_code=401, content={"error": error})


def tier_map(config: Mapping[str, Any], lines: list[str]) -> dict[str, str]:
  """The highest tier that claims each model."""
  found: dict[str, str] = {}
  for tier in AUTO_TIERS:
    for line in router.candidates(config, router.TIER_NAMES[tier], lines):
      found.setdefault(line, router.TIER_NAMES[tier])
  return found


def routes(
  penalties: Penalties, get_config: Callable[[], Mapping[str, Any]]
) -> APIRouter:
  """The dashboard endpoints. Each one needs the access test."""
  api = APIRouter(prefix="/ui/api")

  def members(groups: list[list[str]], order: tuple[int, ...]) -> list[dict]:
    lines = [line for group in groups for line in group]
    weights = penalties.weights(lines)
    return [
      {"id": line, "tier": router.TIER_NAMES[tier], "weight": weights[line]}
      for tier, group in zip(order, groups, strict=True)
      for line in group
    ]

  @api.get("/status")
  async def status(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(
      {
        "healthy": True,
        "models": len(store.read_models()),
        "key": keys.stored_hash(store.MODELS_DB) is not None,
        "login": login() is not None,
        "sessions": penalties.sessions(),
      }
    )

  @api.get("/pools")
  async def pools(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    config, lines = get_config(), store.read_models()
    found = [
      {
        "name": router.RESERVED_MODEL,
        "members": members(router.chain_groups(config, lines, AUTO_TIERS), AUTO_TIERS),
      },
      {
        "name": router.PRAKTOS,
        "members": members(
          router.chain_groups(
            config, store.read_models(tools_only=True), router.PRAKTOS_TIERS
          ),
          router.PRAKTOS_TIERS,
        ),
      },
    ]
    for name, tier in sorted(router.POOLS.items(), key=lambda item: -item[1]):
      order = (tier,)
      found.append(
        {
          "name": name,
          "members": members(router.chain_groups(config, lines, order), order),
        }
      )
    return JSONResponse(found)

  @api.get("/models")
  async def models(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    rows = store.model_rows()
    tiers = tier_map(get_config(), [row["id"] for row in rows])
    weights = penalties.weights([row["id"] for row in rows])
    return JSONResponse(
      [
        {**row, "tier": tiers.get(row["id"]), "weight": weights[row["id"]]}
        for row in rows
      ]
    )

  @api.get("/requests")
  async def requests(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(list(RECENT))

  @api.get("/settings")
  async def current(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    path = settings.DEFAULT_PATH
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return JSONResponse({"path": str(path), "text": text})

  return api
