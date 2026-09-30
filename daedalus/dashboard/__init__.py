"""The dashboard JSON under `/ui/api`, the recent-request list and the access test."""

import hashlib
import hmac
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import (
  FileResponse,
  HTMLResponse,
  JSONResponse,
  Response,
  StreamingResponse,
)

from daedalus import __version__, config, providers, store
from daedalus.catalog import schedule
from daedalus.config import block_for, provider_edit, settings
from daedalus.dashboard.history import SHOWN, History
from daedalus.dashboard.live import Live
from daedalus.routing import router
from daedalus.routing.cooldowns import Cooldowns
from daedalus.routing.limits import Limits
from daedalus.routing.penalties import Penalties
from daedalus.server import headroom
from daedalus.store import keys, saved_env

HISTORY = History(lambda: store.MODELS_DB)
LIVE = Live()
FIELDS = (
  "app",
  "session",
  "key",
  "model",
  "effort",
  "pool",
  "routed",
  "transition",
  "retry",
  "loop",
  "via",
  "ttft",
  "stream",
  "fallbacks",
  "attempts",
  "tokens",
)
AUTO_TIERS = (4, 3, 2, 1)
# The modes whose models have weights: chat, and the modes of the media pools.
WEIGHTED_MODES = frozenset({"chat", *router.MEDIA_POOLS.values()})
MASTER_ENV = "DAEDALUS_MASTER_KEY"
USER_ENV = "DAEDALUS_USERNAME"
PASSWORD_ENV = "DAEDALUS_PASSWORD"
USERNAME = "admin"
COOKIE = "daedalus_session"
# The same session value in a header, for a page in a frame that blocks cookies.
HEADER = "x-daedalus-session"
SESSION_SECONDS = 12 * 3600
REMEMBER_SECONDS = 30 * 86400
UI_DIR = Path(__file__).parent / "ui"
UI_FILES = {
  "app.js": "text/javascript",
  "style.css": "text/css",
  "logo.svg": "image/svg+xml",
  "icon-192.png": "image/png",
  "icon-512.png": "image/png",
  "icon-maskable-512.png": "image/png",
  "apple-touch-icon.png": "image/png",
  "manifest.json": "application/manifest+json",
}
# The browser asks again each time, so a new version of the page applies at once.
FRESH = {"Cache-Control": "no-cache"}
# The files that the Providers editor shows, in tab order. The Settings page has its own form.
FILES = (config.DEFAULT_PATH,)
logger = logging.getLogger("daedalus")


def record(
  request: Request, status: int, seconds: float, cancelled: bool = False
) -> None:
  """Keep one API request for the dashboard, and end it on the live list."""
  found = {key: getattr(request.state, key, None) for key in FIELDS}
  # The dashboard shows the pool names that clients use.
  for key in ("pool", "routed"):
    if found.get(key):
      found[key] = router.pool_name(found[key])
  row = {"at": time.time(), "status": status, "seconds": round(seconds, 3), **found}
  if cancelled:
    row["cancelled"] = True
  HISTORY.add(row)
  if (key := getattr(request.state, "live", None)) is not None:
    LIVE.end(key, row)


def live_update(request: Request, **fields: Any) -> None:
  """Add fields, such as the model, to the live row of a request."""
  if (key := getattr(request.state, "live", None)) is not None:
    LIVE.update(key, **fields)


def live_first(request: Request) -> None:
  """Mark the first token of a request, with the model and the pool that answered."""
  if (key := getattr(request.state, "live", None)) is not None:
    state = request.state
    pool = getattr(state, "pool", None)
    LIVE.first(key, via=state.via, pool=pool and router.pool_name(pool))


def master() -> str | None:
  """The master key from the environment, when it has 16 or more characters and no spaces."""
  key = os.environ.get(MASTER_ENV, "")
  return key if keys.valid(key) else None


def login() -> tuple[str, str]:
  """The dashboard username and password from the environment, else `admin` and the master key."""
  username = os.environ.get(USER_ENV) or USERNAME
  return username, os.environ.get(PASSWORD_ENV) or master() or ""


def secret() -> str | None:
  """The session signing key: a new master key, username or password ends all sessions."""
  key = master()
  return None if key is None else "\n".join((key, *login()))


def signature(key: str, expires: int) -> str:
  return hmac.new(key.encode(), str(expires).encode(), hashlib.sha256).hexdigest()


def cookie(key: str, now: float, seconds: int = SESSION_SECONDS) -> str:
  """A session value: its expiry time and the signature of that time."""
  expires = int(now + seconds)
  return f"{expires}.{signature(key, expires)}"


def allowed(request: Request, query: bool = False) -> bool:
  """Accept a live session value, as cookie or header, or in the URL when `query` is true."""
  key = secret()
  values = (request.cookies.get(COOKIE, ""), request.headers.get(HEADER, ""))
  if query:
    values += (request.query_params.get("session", ""),)
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


# A new provider file: the name rule, and the text of the file.
NAME = re.compile(r"^[a-z][a-z0-9-]{0,38}$")
NEW_FILE = "# A new provider file: fill in the tier patterns and the model values.\n"
NEW_KEY = "api_key: env:{env}_API_KEY\n"
NEW_MODELS = 'models:\n  "*": {}\n'
NEW_BASE = 'api_base: "" # daedalus does not know this provider; set the OpenAI-compatible base\n'


def config_files() -> tuple[Path, ...]:
  """The provider files that the Providers tab shows: the main file, then each `{provider}.yml`."""
  main = FILES[0]
  return (main, *config.provider_files(main))


# The longest value that the dashboard saves for 1 db:NAME or env:NAME.
MAX_VALUE = 4096


def env_name(provider: str, client: str | None = None) -> str:
  """The default environment name of the key of a provider, or of the key of 1 client."""
  name = f"{provider}_API_KEY" + (f"_{client}" if client else "")
  return re.sub(r"[^A-Z0-9_]", "_", name.upper())


def env_tokens(node: Any) -> list[str]:
  """The names in the env:NAME and db:NAME tokens of a YAML tree."""
  if isinstance(node, str):
    return config.ENV_PATTERN.findall(node) + config.SAVED_PATTERN.findall(node)
  if isinstance(node, dict):
    return [name for value in node.values() for name in env_tokens(value)]
  if isinstance(node, list):
    return [name for value in node for name in env_tokens(value)]
  return []


def env_names() -> dict[str, list[str]]:
  """Each env:NAME and db:NAME of the provider files and of the defaults of the providers in use, with the places that use it."""
  found: dict[str, list[str]] = {}

  def add(name: str, place: str) -> None:
    places = found.setdefault(name, [])
    if place not in places:
      places.append(place)

  used: set[str] = set()
  for path in config_files():
    try:
      content = config.load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
      continue
    if not isinstance(content, dict):
      continue
    used |= {str(key) for key in content} if path == FILES[0] else {path.stem}
    for name in env_tokens(content):
      add(name, path.name)
  for provider in sorted(used & set(providers.PROVIDERS)):
    for name in env_tokens(providers.PROVIDERS[provider].defaults):
      add(name, f"{provider} defaults")
  return dict(sorted(found.items()))


def env_rows() -> list[dict[str, Any]]:
  """The state of each name for the dashboard (saved, env or missing), with masked start and length."""
  rows = []
  for name, places in env_names().items():
    saved = config.SAVED.get(name)
    has_saved = bool(saved)
    has_env = bool(os.environ.get(name))
    state = "saved" if has_saved else "env" if has_env else "missing"
    if saved and len(saved) >= 8:
      start = saved[:4]
      length = len(saved)
      end = saved[-4:] if len(saved) >= 12 else None
    else:
      start = None
      length = len(saved) if saved else 0
      end = saved[-4:] if saved and len(saved) >= 12 else None
    rows.append(
      {
        "name": name,
        "state": state,
        "start": start,
        "end": end,
        "length": length,
        "used": places,
        "has_saved": has_saved,
        "has_env": has_env,
      }
    )
  return rows


def raw_value(value: Any) -> bool:
  """Tell if a key field holds a key itself, not an env:NAME or db:NAME token."""
  return (
    isinstance(value, str)
    and bool(value.strip())
    and config.ENV_PREFIX not in value
    and config.SAVED_PREFIX not in value
  )


def park_keys(provider: str, block: dict[str, Any]) -> None:
  """Move the keys in the key fields of a form block to the saved values, and put their db:NAME in the block, or keep env:NAME."""

  def is_env_token(value: Any) -> str | None:
    if not isinstance(value, str) or not value.startswith(config.ENV_PREFIX):
      return None
    found = config.ENV_PATTERN.findall(value)
    return found[0] if found else None

  api_key = block.get("api_key")
  if isinstance(api_key, str) and api_key.strip().startswith(config.ENV_PREFIX):
    name = is_env_token(api_key.strip())
    if not name:
      raise ValueError("The env: value must be env:NAME with a valid name.")
    block["api_key"] = config.ENV_PREFIX + name
  elif raw_value(api_key):
    raw = api_key.strip()
    if not raw or len(raw) > MAX_VALUE or any(c.isspace() for c in raw):
      raise ValueError("The value must be 1 word with no spaces.")
    name = env_name(provider)
    saved_env.save(store.MODELS_DB, name, raw)
    block["api_key"] = config.SAVED_PREFIX + name

  account_id = block.get("account_id")
  if isinstance(account_id, str) and account_id.strip().startswith(config.ENV_PREFIX):
    name = is_env_token(account_id.strip())
    if not name:
      raise ValueError("The env: value must be env:NAME with a valid name.")
    block["account_id"] = config.ENV_PREFIX + name
  elif raw_value(account_id):
    raw = account_id.strip()
    if not raw or len(raw) > MAX_VALUE or any(c.isspace() for c in raw):
      raise ValueError("The value must be 1 word with no spaces.")
    # Use CLOUDFLARE_ACCOUNT_ID as default name for account_id, else provider-based
    if provider == "cloudflare":
      name = "CLOUDFLARE_ACCOUNT_ID"
    else:
      name = env_name(provider) + "_ACCOUNT_ID"
    saved_env.save(store.MODELS_DB, name, raw)
    block["account_id"] = config.SAVED_PREFIX + name

  clients = block.get("client_keys")
  if isinstance(clients, dict):
    for client, value in clients.items():
      if isinstance(value, str) and value.strip().startswith(config.ENV_PREFIX):
        name = is_env_token(value.strip())
        if not name:
          raise ValueError("The env: value must be env:NAME with a valid name.")
        clients[client] = config.ENV_PREFIX + name
      elif raw_value(value):
        raw = value.strip()
        if not raw or len(raw) > MAX_VALUE or any(c.isspace() for c in raw):
          raise ValueError("The value must be 1 word with no spaces.")
        name = env_name(provider, str(client))
        saved_env.save(store.MODELS_DB, name, raw)
        clients[client] = config.SAVED_PREFIX + name


def new_file_text(name: str) -> str:
  """The text of a new `{provider}.yml` file for one provider name."""
  env = env_name(name).removesuffix("_API_KEY")
  known = name in providers.PROVIDERS
  text = NEW_FILE + NEW_KEY.format(env=env)
  if name == "cloudflare":
    text += "account_id: env:CLOUDFLARE_ACCOUNT_ID\n"
  text += ("" if known else NEW_BASE) + NEW_MODELS
  return text


def form_blocks(path: Path, text: str) -> tuple[dict[str, Any] | None, str | None]:
  """The provider blocks of a file by provider name and no error, or None and the error line when the YAML is not valid."""
  try:
    found = config.load_yaml(text) if text.strip() else {}
  except yaml.YAMLError as exc:
    return None, config.error_text(exc)
  if not isinstance(found, dict):
    return None, f"{path.name} must hold provider blocks"
  return (found if path == FILES[0] else {path.stem: found}), None


def override_keys() -> list[str]:
  """The keys that a model override can set: the catalog columns, `order`, `pool`, `timeout` and `hooks`."""
  return sorted({*store.COLUMNS, "order", "pool", "timeout", "hooks"})


def provider_defaults() -> dict[str, dict[str, str]]:
  """The `api_type`, `api_base` and `discovery_url` defaults of each known provider, with no values from the environment."""
  found = {name: dict(kind.defaults) for name, kind in providers.PROVIDERS.items()}
  found["*"] = dict(providers.OpenAIProvider.defaults)
  return found


def check_file(path: Path, text: str) -> dict[str, Any] | None:
  """Validate the text of one config file. Returns settings values for the settings file."""
  if path == settings.DEFAULT_PATH:
    return settings.parse(text, path)
  if not isinstance(config.load_yaml(text), dict):
    raise settings.SettingsError(f"{path} must hold provider blocks")
  return None


def tier_map(config: Mapping[str, Any], lines: list[str]) -> dict[str, str]:
  """The tier that claims each model eligible for the pools."""
  found: dict[str, str] = {}
  for line in lines:
    if not router.pooled(config, line):
      continue
    name, _, slug = line.partition("/")
    block = block_for(config, name, slug)
    tier = router.claiming_tier(block, slug) if block else None
    if tier:
      found[line] = tier
  return found


def versioned_index() -> str:
  """The page with a content hash on each asset link, so no cache serves an old file."""
  text = (UI_DIR / "index.html").read_text(encoding="utf-8")
  for name in UI_FILES:
    tag = hashlib.sha256((UI_DIR / name).read_bytes()).hexdigest()[:12]
    text = text.replace(f'"ui/{name}"', f'"ui/{name}?v={tag}"')
  return text


def page() -> APIRouter:
  """The dashboard page and its script and style. They hold no data."""
  pages = APIRouter()

  @pages.get("/", include_in_schema=False)
  async def index() -> HTMLResponse:
    return HTMLResponse(versioned_index(), headers=FRESH)

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
  cooldowns: Cooldowns | None = None,
  refresh: Callable[[], Callable[[], object] | None] = lambda: None,
  limits: Limits | None = None,
  rebuild_cached: Callable[[], Callable[[], object] | None] = lambda: None,
) -> APIRouter:
  """The dashboard endpoints. All except login need a session."""
  api = APIRouter(prefix="/ui/api")

  def request_cached_rebuild() -> None:
    task = rebuild_cached()
    if task is not None:
      schedule.request(task)

  @api.post("/login")
  async def log_in(request: Request) -> JSONResponse:
    key = secret()
    if key is None:
      return failure(503, f"Set {MASTER_ENV}: 16 or more characters.", "server_error")
    username, password = login()
    body = await json_body(request)
    if not isinstance(body, dict):
      return failure(400, "Send a username and a password.", "invalid_request_error")
    user = hmac.compare_digest(
      str(body.get("username", "")).encode(), username.encode()
    )
    known = hmac.compare_digest(
      str(body.get("password", "")).encode(), password.encode()
    )
    if not (user and known):
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

  @api.get("/login")
  async def login_form() -> JSONResponse:
    """The login form hints, with no session: the username to fill in, if the password is the master key, and the version."""
    return JSONResponse(
      {
        "username": None if os.environ.get(USER_ENV) else USERNAME,
        "master": not os.environ.get(PASSWORD_ENV),
        "version": __version__,
      }
    )

  @api.post("/logout")
  async def log_out(request: Request) -> JSONResponse:
    response = JSONResponse({"ok": True})
    https = secure(request)
    response.delete_cookie(COOKIE, secure=https, samesite="none" if https else "strict")
    if https:
      partition(response)
    return response

  def members(
    groups: list[list[str]], order: tuple[int | None, ...], ends: Mapping[str, float]
  ) -> list[dict]:
    lines = [line for group in groups for line in group]
    weights = penalties.weights(lines)
    return [
      {
        "id": line,
        "tier": router.TIER_NAMES[tier] if tier else None,
        "weight": weights[line],
        "cooldown": Cooldowns.until(line, ends),
      }
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
        "version": __version__,
        "models": len(store.read_models()),
        "sessions": penalties.sessions(),
        "catalog": {
          "built": store.built(),
          "next": schedule.upcoming(time.time()),
          "rebuilding": schedule.BUSY,
        },
      }
    )

  @api.post("/reset")
  async def reset(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    penalties.reset_weights()
    if cooldowns is not None:
      cooldowns.clear()
    logger.info("reset: all weights and cooldowns")
    return JSONResponse({"ok": True})

  @api.post("/catalog")
  async def rebuild_catalog(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    task = refresh()
    if task is None:
      return failure(503, "This process has no catalog rebuild.", "server_error")
    if not schedule.start(task):
      return failure(409, "A catalog rebuild runs now.", "invalid_request_error")
    return JSONResponse({"ok": True}, status_code=202)

  # With no limits, the page shows an empty list.
  seen = limits or Limits()

  @api.get("/limits")
  async def limit_view(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(seen.view())

  @api.post("/limits")
  async def limit_check(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    await seen.check()
    return JSONResponse(seen.view())

  @api.get("/pools")
  async def pools(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    config, lines = get_config(), store.read_models()
    ends = cooldowns.ends() if cooldowns else {}
    limits = {row["id"]: row.get("max_input_tokens") for row in store.model_rows()}
    found = [
      {
        "name": router.RESERVED_MODEL,
        "members": members(
          router.chain_groups(config, lines, AUTO_TIERS), AUTO_TIERS, ends
        ),
      },
    ]
    for name, tier in sorted(router.POOLS.items(), key=lambda item: -item[1]):
      order = (tier,)
      found.append(
        {
          "name": name,
          "members": members(router.chain_groups(config, lines, order), order, ends),
        }
      )
    for name, mode in router.MEDIA_POOLS.items():
      media = [m for m in store.mode_models(mode) if router.pooled(config, m)]
      found.append(
        {"name": name, "mode": mode, "members": members([media], (None,), ends)}
      )
    for pool in found:
      pool["shown"] = router.pool_name(pool["name"])
      sizes = [limits.get(member["id"]) or 0 for member in pool["members"]]
      pool["context"] = max(sizes, default=0) or None
    return JSONResponse(found)

  @api.get("/models")
  async def models(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    rows = store.model_rows()
    chat = [row["id"] for row in rows if row["mode"] == "chat"]
    weighted = [row["id"] for row in rows if row["mode"] in WEIGHTED_MODES]
    config = get_config()
    tiers, weights = tier_map(config, chat), penalties.weights(weighted)
    ends = cooldowns.ends() if cooldowns else {}
    return JSONResponse(
      [
        {
          **row,
          "tier": tiers.get(row["id"]),
          "weight": weights.get(row["id"]),
          "cooldown": Cooldowns.until(row["id"], ends),
          "client_cooldowns": Cooldowns.clients(row["id"], ends),
          "order": router.cached_order(config, row["id"]),
        }
        for row in rows
      ]
    )

  @api.get("/requests")
  async def requests(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    try:
      limit = int(request.query_params.get("limit", SHOWN))
    except ValueError:
      limit = SHOWN
    return JSONResponse(HISTORY.latest(limit))

  @api.get("/requests/live")
  async def live(request: Request) -> Response:
    # `EventSource` cannot send headers, so a frame without cookies sends the session in the URL.
    if not allowed(request, query=True):
      return denied()
    return StreamingResponse(
      LIVE.events(request.is_disconnected),
      media_type="text/event-stream",
      headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

  @api.get("/keys")
  async def key_list(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(keys.listing(store.MODELS_DB))

  @api.post("/keys")
  async def key_add(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    try:
      key = keys.add(
        store.MODELS_DB, body.get("name") if isinstance(body, dict) else None
      )
    except keys.KeyNameError as exc:
      return failure(400, str(exc), "invalid_request_error")
    return JSONResponse({"name": body["name"].strip(), "key": key}, status_code=201)

  @api.delete("/keys/{name:path}")
  async def key_delete(request: Request, name: str) -> Response:
    if not allowed(request):
      return denied()
    if not keys.delete(store.MODELS_DB, name):
      return failure(404, f"No key has the name {name!r}.", "invalid_request_error")
    return Response(status_code=204)

  @api.get("/files")
  async def files(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    main = FILES[0]
    found = [
      {
        "path": str(path),
        "text": path.read_text(encoding="utf-8") if path.exists() else "",
        "main": path == main,
      }
      for path in config_files()
    ]
    for file in found:
      file["blocks"], file["error"] = form_blocks(Path(file["path"]), file["text"])
    return JSONResponse(found)

  @api.get("/provider-keys")
  async def provider_keys(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(override_keys())

  @api.get("/provider-defaults")
  async def defaults(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(provider_defaults())

  @api.put("/providers")
  async def save_form(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    names = {str(path): path for path in config_files()}
    if not isinstance(body, dict) or body.get("path") not in names:
      return failure(400, "Unknown config file.", "invalid_request_error")
    path, blocks = names[body["path"]], body.get("blocks")
    if not isinstance(blocks, dict) or not all(
      isinstance(b, dict) for b in blocks.values()
    ):
      return failure(400, "Each provider block must be a map.", "invalid_request_error")
    document = blocks if path == FILES[0] else blocks.get(path.stem)
    if not isinstance(document, dict):
      return failure(
        400, f"{path} needs the block {path.stem}.", "invalid_request_error"
      )
    try:
      if path == FILES[0]:
        for provider, block in document.items():
          park_keys(str(provider), block)
      else:
        park_keys(path.stem, document)
    except ValueError as exc:
      return failure(400, str(exc), "invalid_request_error")
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    return write_config(path, provider_edit.merge_text(old, document))

  @api.get("/env")
  async def env_list(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(env_rows())

  @api.put("/env")
  async def env_save(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    name = body.get("name") if isinstance(body, dict) else None
    value = body.get("value") if isinstance(body, dict) else None
    if name not in env_names():
      return failure(
        400,
        "No provider file uses this name.",
        "invalid_request_error",
      )
    value = value.strip() if isinstance(value, str) else ""
    if not value or len(value) > MAX_VALUE or any(c.isspace() for c in value):
      return failure(
        400, "The value must be 1 word with no spaces.", "invalid_request_error"
      )
    saved_env.save(store.MODELS_DB, name, value)
    config.load_config(FILES[0])
    request_cached_rebuild()
    return JSONResponse(env_rows())

  @api.delete("/env")
  async def env_clear(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not saved_env.clear(store.MODELS_DB, name):
      return failure(400, "No saved value with this name.", "invalid_request_error")
    config.load_config(FILES[0])
    request_cached_rebuild()
    return JSONResponse(env_rows())

  @api.put("/files")
  async def save(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    names = {str(path): path for path in config_files()}
    if not isinstance(body, dict) or body.get("path") not in names:
      return failure(400, "Unknown config file.", "invalid_request_error")
    path, text = names[body["path"]], body.get("text")
    if not isinstance(text, str):
      return failure(400, "The file text must be a string.", "invalid_request_error")
    return write_config(path, text)

  def write_config(path: Path, text: str) -> JSONResponse:
    """Write a valid text in 1 step, reload it, and send it back."""
    try:
      values = check_file(path, text)
    except (settings.SettingsError, yaml.YAMLError) as exc:
      return failure(422, config.error_text(exc), "invalid_request_error")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    if values is None:
      config.load_config(path)
      request_cached_rebuild()
    else:
      apply(values)
    return JSONResponse({"ok": True, "text": text})

  @api.post("/files")
  async def make_file(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not NAME.match(name):
      return failure(
        400,
        "The provider name must use lowercase letters, digits and dashes.",
        "invalid_request_error",
      )
    path = FILES[0].parent / f"{name}.yml"
    if path in config_files():
      return failure(400, f"{path} exists.", "invalid_request_error")
    text = new_file_text(name)
    path.write_text(text, encoding="utf-8")
    config.load_config(FILES[0])
    request_cached_rebuild()
    return JSONResponse({"path": str(path), "text": text})

  @api.delete("/files")
  async def drop_file(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    name = body.get("path") if isinstance(body, dict) else None
    found = {str(Path(p)): Path(p) for p in config.provider_files(FILES[0])}
    if not isinstance(name, str) or name not in found:
      return failure(400, "Only a {provider}.yml file can go.", "invalid_request_error")
    found[name].unlink()
    config.load_config(FILES[0])
    request_cached_rebuild()
    return JSONResponse({"ok": True})

  @api.get("/settings")
  async def settings_values(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    path = settings.DEFAULT_PATH
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    raw = yaml.safe_load(text)
    return JSONResponse(
      {
        "path": str(path),
        "headroom_available": await headroom.available(),
        "defaults": settings.DEFAULTS,
        "file": raw if isinstance(raw, dict) else {},
        "text": text,
      }
    )

  @api.put("/settings")
  async def settings_save(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    body = await json_body(request)
    body = body if isinstance(body, dict) else {}
    path = settings.DEFAULT_PATH
    # The YAML view sends the file text. The form sends the changed values.
    if isinstance(body.get("text"), str):
      return write_config(path, body["text"])
    changes = body.get("changes")
    if not isinstance(changes, dict):
      return failure(400, "The changes must be an object.", "invalid_request_error")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
      text = settings.update_text(text, changes)
    except settings.SettingsError as exc:
      return failure(422, str(exc), "invalid_request_error")
    return write_config(path, text)

  return api
