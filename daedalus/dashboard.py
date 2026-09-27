"""The dashboard JSON under `/ui/api`, the recent-request list and the access test."""

import hashlib
import hmac
import os
import time
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from daedalus import config, keys, router, schedule, settings, store
from daedalus.penalties import Penalties

# The chat requests that the dashboard shows. A restart clears them.
RECENT: deque[dict[str, Any]] = deque(maxlen=50)
FIELDS = ("model", "pool", "via", "ttft", "fallbacks")
AUTO_TIERS = (4, 3, 2, 1)
MASTER_ENV = "DAEDALUS_MASTER_KEY"
USERNAME = "admin"
COOKIE = "daedalus_session"
# The same session value in a header, for a page in a frame that blocks cookies.
HEADER = "x-daedalus-session"
SESSION_SECONDS = 12 * 3600
REMEMBER_SECONDS = 30 * 86400
UI_DIR = Path(__file__).parent / "ui"
UI_FILES = {"app.js": "text/javascript", "style.css": "text/css"}
# The browser asks again each time, so a new version of the page applies at once.
FRESH = {"Cache-Control": "no-cache"}
# The files that the editor shows, in tab order.
FILES = (settings.DEFAULT_PATH, config.DEFAULT_PATH)


def record(request: Request, status: int, seconds: float) -> None:
  """Keep one chat request for the dashboard."""
  found = {key: getattr(request.state, key, None) for key in FIELDS}
  RECENT.appendleft({"at": time.time(), "status": status, "seconds": seconds, **found})


def master() -> str | None:
  """The master key from the environment, when it follows the local key rule."""
  key = os.environ.get(MASTER_ENV, "")
  return key if keys.valid(key) else None


def signature(key: str, expires: int) -> str:
  return hmac.new(key.encode(), str(expires).encode(), hashlib.sha256).hexdigest()


def cookie(key: str, now: float, seconds: int = SESSION_SECONDS) -> str:
  """A session value: its expiry time and the master key signature of that time."""
  expires = int(now + seconds)
  return f"{expires}.{signature(key, expires)}"


def allowed(request: Request) -> bool:
  """Accept a request with a live session value, as cookie or header, from the master key."""
  key = master()
  values = (request.cookies.get(COOKIE, ""), request.headers.get(HEADER, ""))
  return key is not None and any(live(key, value) for value in values)


def live(key: str, value: str) -> bool:
  expires, _, signed = value.partition(".")
  if not expires.isdigit() or int(expires) < time.time():
    return False
  return hmac.compare_digest(signed, signature(key, int(expires)))


def denied() -> JSONResponse:
  return failure(401, "Log in to the dashboard.", "authentication_error")


def failure(status: int, message: str, kind: str) -> JSONResponse:
  error = {"message": message, "type": kind, "code": status}
  return JSONResponse(status_code=status, content={"error": error})


async def json_body(request: Request) -> Any:
  """The JSON body. Other content types give None, so a cross-site form cannot post."""
  if (
    request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
  ):
    return None
  try:
    return await request.json()
  except ValueError:
    return None


def secure(request: Request) -> bool:
  """Tell if the browser uses https, also behind a proxy that ends TLS."""
  forwarded = request.headers.get("x-forwarded-proto", "")
  origin = request.headers.get("origin", "")
  return "https" in (request.url.scheme, forwarded) or origin.startswith("https://")


def partition(response: Response) -> None:
  """Mark the last cookie as partitioned. Starlette does this only on Python 3.14."""
  name, value = response.raw_headers[-1]
  if name == b"set-cookie":
    response.raw_headers[-1] = (name, value + b"; Partitioned")


def check_file(path: Path, text: str) -> dict[str, Any] | None:
  """Validate the text of one config file. Returns settings values for the settings file."""
  if path == settings.DEFAULT_PATH:
    return settings.parse(text, path)
  if not isinstance(yaml.safe_load(text), dict):
    raise settings.SettingsError(f"{path} must hold provider blocks")
  return None


def tier_map(config: Mapping[str, Any], lines: list[str]) -> dict[str, str]:
  """The highest tier that claims each model."""
  found: dict[str, str] = {}
  for tier in AUTO_TIERS:
    for line in router.candidates(config, router.TIER_NAMES[tier], lines):
      found.setdefault(line, router.TIER_NAMES[tier])
  return found


def page() -> APIRouter:
  """The dashboard page and its script and style. They hold no data."""
  pages = APIRouter()

  @pages.get("/", include_in_schema=False)
  async def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html", media_type="text/html", headers=FRESH)

  @pages.get("/ui/{name}", include_in_schema=False)
  async def asset(name: str) -> Response:
    if name not in UI_FILES:
      return failure(404, "Not found.", "invalid_request_error")
    return FileResponse(UI_DIR / name, media_type=UI_FILES[name], headers=FRESH)

  return pages


def routes(
  penalties: Penalties,
  get_config: Callable[[], Mapping[str, Any]],
  apply: Callable[[dict[str, Any]], None],
) -> APIRouter:
  """The dashboard endpoints. All except login need a session."""
  api = APIRouter(prefix="/ui/api")

  @api.post("/login")
  async def log_in(request: Request) -> JSONResponse:
    key = master()
    if key is None:
      return failure(503, f"Set {MASTER_ENV}: 16 or more characters.", "server_error")
    body = await json_body(request)
    if not isinstance(body, dict):
      return failure(400, "Send a username and a password.", "invalid_request_error")
    user = hmac.compare_digest(
      str(body.get("username", "")).encode(), USERNAME.encode()
    )
    password = hmac.compare_digest(str(body.get("password", "")).encode(), key.encode())
    if not (user and password):
      return failure(401, "Wrong username or password.", "authentication_error")
    # Without "remember", the browser drops the cookie on close and the value ends after 12 h.
    remember = body.get("remember") is True
    seconds = REMEMBER_SECONDS if remember else SESSION_SECONDS
    session = cookie(key, time.time(), seconds)
    response = JSONResponse({"ok": True, "session": session})
    response.set_cookie(
      COOKIE,
      session,
      max_age=seconds if remember else None,
      httponly=True,
      # Over https, the cookie also works in a frame on another site, such as a preview.
      samesite="none" if secure(request) else "strict",
      secure=secure(request),
    )
    if secure(request):
      partition(response)
    return response

  @api.post("/logout")
  async def log_out(request: Request) -> JSONResponse:
    response = JSONResponse({"ok": True})
    https = secure(request)
    response.delete_cookie(COOKIE, secure=https, samesite="none" if https else "strict")
    if https:
      partition(response)
    return response

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
        "sessions": penalties.sessions(),
        "catalog": {"built": store.built(), "next": schedule.upcoming(time.time())},
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

  @api.get("/files")
  async def files(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    found = [
      {
        "path": str(path),
        "text": path.read_text(encoding="utf-8") if path.exists() else "",
      }
      for path in FILES
    ]
    return JSONResponse(found)

  @api.put("/files")
  async def save(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    names = {str(path): path for path in FILES}
    if not isinstance(body, dict) or body.get("path") not in names:
      return failure(400, "Unknown config file.", "invalid_request_error")
    path, text = names[body["path"]], body.get("text")
    if not isinstance(text, str):
      return failure(400, "The file text must be a string.", "invalid_request_error")
    try:
      values = check_file(path, text)
    except (settings.SettingsError, yaml.YAMLError) as exc:
      return failure(422, str(exc), "invalid_request_error")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    if values is None:
      config.load_config(path)
    else:
      apply(values)
    return JSONResponse({"ok": True})

  return api
