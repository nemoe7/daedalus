"""The dashboard JSON under `/ui/api`, the recent-request list and the access test."""

import asyncio
import hashlib
import hmac
import itertools
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import (
  FileResponse,
  HTMLResponse,
  JSONResponse,
  RedirectResponse,
  Response,
  StreamingResponse,
)

from daedalus import __version__, config, providers, store, updates
from daedalus.catalog import schedule
from daedalus.config import block_for, provider_edit, remote, settings
from daedalus.providers import hooks
from daedalus.routing import lanes, router
from daedalus.routing.cooldowns import Cooldowns
from daedalus.routing.limits import Limits
from daedalus.routing.penalties import Penalties
from daedalus.server import headroom
from daedalus.store import keys, saved_env
from daedalus.store.database import open_db

KEEP = 500
SHOWN = 50
# A lane row warns when no more than this part of its limit is left.
LIMIT_SHARE = 0.25
TABLE = (
  "CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, row TEXT NOT NULL)"
)


class History:
  """The last requests, newest first, in the model store."""

  def __init__(self, path: Callable[[], Path], keep: int = KEEP) -> None:
    self.path, self.keep = path, keep

  def connect(self) -> sqlite3.Connection:
    return open_db(self.path(), (TABLE,))

  def add(self, row: dict[str, Any]) -> None:
    """Keep one request, and drop the rows older than the last `keep` rows."""
    database = self.connect()
    with database:
      cursor = database.execute(
        "INSERT INTO requests (row) VALUES (?)", (json.dumps(row, default=str),)
      )
      database.execute(
        "DELETE FROM requests WHERE id <= ?", (cursor.lastrowid - self.keep,)
      )
    database.close()

  def latest(self, limit: int = SHOWN) -> list[dict[str, Any]]:
    """The newest rows, at most `limit` and at most `keep`."""
    database = self.connect()
    try:
      rows = database.execute(
        "SELECT row FROM requests ORDER BY id DESC LIMIT ?",
        (max(0, min(limit, self.keep)),),
      ).fetchall()
    finally:
      database.close()
    return [json.loads(row) for (row,) in rows]

  def clear(self) -> None:
    database = self.connect()
    with database:
      database.execute("DELETE FROM requests")
    database.close()


# A comment line on an idle stream keeps proxies from closing it.
KEEPALIVE_SECONDS = 15.0
# A stream that falls this many events behind stops, and the page opens a new one.
QUEUE_LIMIT = 1000


def event(kind: str, data: object) -> str:
  return f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"


class Live:
  """The requests in flight, and a queue for each open event stream."""

  def __init__(self) -> None:
    self.rows: dict[int, dict[str, Any]] = {}
    self.queues: set[asyncio.Queue[str | None]] = set()
    self.ids = itertools.count(1)

  def start(self, path: str) -> int:
    """Add a request that has just arrived, and return its id."""
    key = next(self.ids)
    now = time.time()
    self.rows[key] = {
      "id": key,
      "path": path,
      "started": now,
      "attempt_started": now,
      "first": None,
    }
    self.send("start", self.view(key))
    return key

  def update(self, key: int, **fields: Any) -> None:
    """Add request fields, such as the model, to a request in flight."""
    if key in self.rows:
      row = self.rows[key]
      if "trying" in fields and fields["trying"] != row.get("trying"):
        row["attempt_started"] = time.time()
        row["first"] = None
      row.update(fields)
      self.send("update", self.view(key))

  def first(self, key: int, **fields: Any) -> None:
    """Keep the time of the first token, or of the full answer without a stream."""
    if key in self.rows:
      self.rows[key].update(fields, first=time.time())
      self.send("first", self.view(key))

  def end(self, key: int, row: dict[str, Any] | None) -> None:
    """Remove a finished request, and send the row that the history keeps."""
    if self.rows.pop(key, None) is not None:
      self.send("end", {"id": key, "row": row})

  def view(self, key: int) -> dict[str, Any]:
    """A request as the page gets it, with ages in seconds instead of clock times."""
    row, now = self.rows[key], time.time()
    shown = {
      name: value
      for name, value in row.items()
      if name not in {"started", "attempt_started", "first"}
    }
    # The clocks count in whole milliseconds, so no age carries a fraction of a ms.
    shown["age"] = round((now - row["started"]) * 1000) / 1000
    shown["attempt_age"] = round((now - row["attempt_started"]) * 1000) / 1000
    shown["ttft"] = (
      None
      if row["first"] is None
      else round((row["first"] - row["attempt_started"]) * 1000) / 1000
    )
    return shown

  def send(self, kind: str, data: object) -> None:
    text = event(kind, data)
    for queue in list(self.queues):
      if queue.qsize() < QUEUE_LIMIT:
        queue.put_nowait(text)
      else:
        self.queues.discard(queue)
        queue.put_nowait(None)

  async def events(
    self,
    gone: Callable[[], Awaitable[bool]],
    keepalive: float = KEEPALIVE_SECONDS,
  ) -> AsyncIterator[str]:
    """All requests in flight, then each change, until the page closes the stream."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    self.queues.add(queue)
    try:
      yield event("live", [self.view(key) for key in self.rows])
      while not await gone():
        try:
          text = await asyncio.wait_for(queue.get(), keepalive)
        except TimeoutError:
          text = ": keepalive\n\n"
        if text is None:
          return
        yield text
    finally:
      self.queues.discard(queue)


HISTORY = History(lambda: store.MODELS_DB)
LIVE = Live()
# The parsed config files, by path and file time. A save changes the time.
_FILES_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}
FIELDS = (
  "app",
  "session",
  "key",
  "model",
  "effort",
  "pool",
  "routed",
  "transition",
  "race",
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
DAEDALUS_MASTER_KEY = "DAEDALUS_MASTER_KEY"
DAEDALUS_USERNAME = "DAEDALUS_USERNAME"
DAEDALUS_PASSWORD = "DAEDALUS_PASSWORD"
USERNAME = "admin"
COOKIE = "daedalus_session"
# The same session value in a header, for a page in a frame that blocks cookies.
HEADER = "x-daedalus-session"
SESSION_SECONDS = 12 * 3600
REMEMBER_SECONDS = 30 * 86400
# A failed login waits, and 5 failures in a row lock the client out for a minute.
LOGIN_DELAY = 0.5
LOGIN_LIMIT = 5
LOGIN_LOCK = 60.0
LOGIN_FAILURES: dict[str, tuple[int, float]] = {}
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
# The path of each file of the page, so a request name never reaches the file system.
UI_PATHS = {name: UI_DIR / name for name in UI_FILES}
# The path of each model mark, 1 file for each name in the app.js set.
ICON_PATHS = {path.name: path for path in (UI_DIR / "icons").glob("*.svg")}
# The browser asks again each time, so a new version of the page applies at once.
FRESH = {"Cache-Control": "no-cache"}
# The answers of a session stay in 1 browser cache, and each read revalidates them.
CACHE = {"Cache-Control": "no-cache, private"}
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
  # The seconds of the row count in whole milliseconds, as the clocks of the live list do.
  row = {
    "at": time.time(),
    "status": status,
    "seconds": round(seconds * 1000) / 1000,
    **found,
  }
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
  key = os.environ.get(DAEDALUS_MASTER_KEY, "")
  return key if keys.valid(key) else None


def login() -> tuple[str, str]:
  """The dashboard username and password from the environment, else `admin` and the master key."""
  username = os.environ.get(DAEDALUS_USERNAME) or USERNAME
  return username, os.environ.get(DAEDALUS_PASSWORD) or master() or ""


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


def login_client(request: Request) -> str:
  """The client of a login try: its address, else the word `client`."""
  return request.client.host if request.client else "client"


def login_locked(client: str) -> float:
  """The seconds left of the login lockout of a client, and 0 when it may try again."""
  count, at = LOGIN_FAILURES.get(client, (0, 0.0))
  left = at + LOGIN_LOCK - time.time()
  return left if count >= LOGIN_LIMIT and left > 0 else 0.0


async def login_failed(client: str) -> JSONResponse:
  """Count a failed try, wait, and refuse the login."""
  count, _ = LOGIN_FAILURES.get(client, (0, 0.0))
  LOGIN_FAILURES[client] = (count + 1, time.time())
  await asyncio.sleep(LOGIN_DELAY)
  return failure(401, "Wrong username or password.", "authentication_error")


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
  """Mark the set-cookie header as partitioned."""
  for index in range(len(response.raw_headers) - 1, -1, -1):
    name, value = response.raw_headers[index]
    if name.lower() == b"set-cookie":
      if b"; Partitioned" not in value and b"; partitioned" not in value:
        response.raw_headers[index] = (name, value + b"; Partitioned")
      break


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
  """Each env:NAME and db:NAME of the provider files and of the defaults of the providers in use, with
  the places that use it.
  """
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
  """Move the keys in the key fields of a form block to the saved values, and put their db:NAME in the
  block, or keep env:NAME.
  """

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
  """The provider blocks of a file by provider name and no error, or None and the error line when the
  YAML is not valid.
  """
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
  """The `api_type`, `api_base` and `discovery_url` defaults of each known provider, with no values from
  the environment.
  """
  found = {name: dict(kind.defaults) for name, kind in providers.PROVIDERS.items()}
  found["*"] = dict(providers.OpenAIProvider.defaults)
  return found


def check_file(path: Path, text: str) -> dict[str, Any] | None:
  """Validate the text of one config file. Returns settings values for the settings file."""
  if path == settings.DEFAULT_PATH:
    return settings.parse(text, path)
  loaded = config.load_yaml(text)
  if not isinstance(loaded, dict):
    raise settings.SettingsError(f"{path} must hold provider blocks")
  problems = config.file_shape_problems(loaded, path.stem)
  if problems:
    raise settings.SettingsError(f"{path}: {'; '.join(problems)}")
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


def model_tag(
  built: float | None,
  config: Mapping[str, Any],
  rows: list[dict[str, Any]],
  weights: Mapping[str, float],
  ends: Mapping[str, float],
) -> str:
  """The stamp of the model list: the catalog time, the config, the rows and the cooling state.

  The weights carry 2 decimals, as the page shows them, so a slow recovery keeps the stamp.
  """
  shown = {model: round(weight, 2) for model, weight in weights.items()}
  text = json.dumps([built, config, rows, shown, ends], default=str, sort_keys=True)
  return f'"{hashlib.sha256(text.encode()).hexdigest()[:16]}"'


def versioned_index() -> str:
  """The page with a content hash on each asset link, so no cache serves an old file."""
  text = (UI_DIR / "index.html").read_text(encoding="utf-8")
  for name in UI_FILES:
    tag = hashlib.sha256((UI_DIR / name).read_bytes()).hexdigest()[:12]
    text = text.replace(f'"ui/{name}"', f'"ui/{name}?v={tag}"')
  return text


def page() -> APIRouter:
  """The dashboard page, its script and style, and the model marks. They hold no data."""
  pages = APIRouter()

  @pages.get("/", include_in_schema=False)
  async def index() -> HTMLResponse:
    return HTMLResponse(versioned_index(), headers=FRESH)

  @pages.get("/ui", include_in_schema=False)
  @pages.get("/ui/", include_in_schema=False)
  async def ui_index() -> RedirectResponse:
    return RedirectResponse("/", status_code=307)

  @pages.get("/ui/{name}", include_in_schema=False)
  async def asset(name: str) -> Response:
    if name not in UI_PATHS:
      return failure(404, "Not found.", "invalid_request_error")
    return FileResponse(UI_PATHS[name], media_type=UI_FILES[name], headers=FRESH)

  @pages.get("/ui/icons/{name}", include_in_schema=False)
  async def model_mark(name: str) -> Response:
    path = ICON_PATHS.get(name)
    if path is None:
      return failure(404, "Not found.", "invalid_request_error")
    return FileResponse(path, media_type="image/svg+xml", headers=FRESH)

  return pages


def members(
  penalties: Penalties,
  groups: list[list[str]],
  order: tuple[int | None, ...],
  ends: Mapping[str, float],
) -> list[dict]:
  """The member rows of 1 pool: the id, the tier, the weight and the cooldown."""
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


def limit_warnings(seen: Limits) -> list[dict[str, Any]]:
  """The lane rows near their limit, the lowest share of limit left first."""
  found = []
  for lane, seen_lanes in seen.lanes.items():
    model, client = lanes.split(lane)
    for row in seen_lanes.get("rows", []):
      limit, remaining = row.get("limit"), row.get("remaining")
      if not limit or remaining is None:
        continue
      share = remaining / limit
      if share <= LIMIT_SHARE:
        found.append({"model": model, "client": client or None, **row, "share": share})
  return sorted(found, key=lambda row: (row["share"], row["model"]))


def write_config(
  path: Path,
  text: str,
  apply: Callable[[dict[str, Any]], None],
  rebuild: Callable[[], None],
) -> JSONResponse:
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
    # A provider file is 1 block of the config: the main file is the one to load.
    config.load_config(FILES[0])
    rebuild()
  else:
    apply(values)
  return JSONResponse({"ok": True, "text": text})


def unlink_file(path: Path, label: str) -> JSONResponse | None:
  """Remove 1 file, and answer the failure when the file stays."""
  try:
    path.unlink()
  except OSError:
    return failure(409, f"The file {label} could not be deleted.", "server_error")
  return None


def login_routes(api: APIRouter) -> None:
  """The login and the logout endpoints, the 1 group without a session."""

  @api.post("/login")
  async def log_in(request: Request) -> JSONResponse:
    key = secret()
    if key is None:
      return failure(
        503, f"Set {DAEDALUS_MASTER_KEY}: 16 or more characters.", "server_error"
      )
    username, password = login()
    body = await json_body(request)
    if not isinstance(body, dict):
      return failure(400, "Send a username and a password.", "invalid_request_error")
    # A locked client waits before another try, so a list of keys runs no faster than a guess.
    client = login_client(request)
    left = login_locked(client)
    if left:
      return failure(
        429,
        f"Too many failed tries. Wait {int(left) + 1} seconds.",
        "rate_limit_error",
      )
    user = hmac.compare_digest(
      str(body.get("username", "")).encode(), username.encode()
    )
    known = hmac.compare_digest(
      str(body.get("password", "")).encode(), password.encode()
    )
    if not (user and known):
      return await login_failed(client)
    LOGIN_FAILURES.pop(client, None)
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
  async def login_form(request: Request) -> JSONResponse:
    """The login hints: the username, the master-key note, the version, and whether a session is live."""
    return JSONResponse(
      {
        "username": None if os.environ.get(DAEDALUS_USERNAME) else USERNAME,
        "master": not os.environ.get(DAEDALUS_PASSWORD),
        "version": __version__,
        "session": allowed(request),
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


def state_routes(
  api: APIRouter,
  penalties: Penalties,
  cooldowns: Cooldowns | None,
  affinity: Callable[[], dict[str, Any]],
  refresh: Callable[[], Callable[[], object] | None],
  seen: Limits,
) -> None:
  """The state endpoints: the health, the hooks, the reset, the catalog and the notifications."""

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
        "affinity": affinity(),
        "catalog": {
          "built": store.built(),
          "next": schedule.upcoming(time.time()),
          "rebuilding": schedule.BUSY,
        },
      }
    )

  @api.get("/hooks")
  async def hook_rows(request: Request) -> JSONResponse:
    """The legend rows of the enabled request hook files."""
    if not allowed(request):
      return denied()
    found = settings.load().get("hooks") or {}
    entries = {key: value for key, value in found.items() if key.startswith("on-")}
    return JSONResponse({"legend": hooks.init_rows(entries)})

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

  @api.get("/notifications")
  async def notifications(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(
      {
        "rebuilds": store.recent_rebuilds(50),
        "update": updates.read(),
        "limits": limit_warnings(seen),
      }
    )

  @api.post("/updates")
  async def update_check(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    return JSONResponse(await asyncio.to_thread(updates.check_now))


def limit_routes(api: APIRouter, seen: Limits) -> None:
  """The limit endpoints: the view, and the view after a fresh read."""

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


def pool_routes(
  api: APIRouter,
  penalties: Penalties,
  get_config: Callable[[], Mapping[str, Any]],
  cooldowns: Cooldowns | None,
) -> None:
  """The pool and the model endpoints."""

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
          penalties, router.chain_groups(config, lines, AUTO_TIERS), AUTO_TIERS, ends
        ),
      },
    ]
    for name, tier in sorted(router.POOLS.items(), key=lambda item: -item[1]):
      order = (tier,)
      found.append(
        {
          "name": name,
          "members": members(
            penalties, router.chain_groups(config, lines, order), order, ends
          ),
        }
      )
    for name, mode in router.MEDIA_POOLS.items():
      media = [m for m in store.mode_models(mode) if router.pooled(config, m)]
      found.append(
        {
          "name": name,
          "mode": mode,
          "members": members(penalties, [media], (None,), ends),
        }
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
    weights = penalties.weights(weighted)
    ends = cooldowns.ends() if cooldowns else {}
    # The stamp of the list: a poll of an unchanged state answers 304 and skips the rows.
    tag = model_tag(store.built(), config, rows, weights, ends)
    if request.headers.get("if-none-match") == tag:
      return Response(status_code=304, headers={**CACHE, "ETag": tag})
    tiers = tier_map(config, chat)
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
      ],
      headers={**CACHE, "ETag": tag},
    )


def history_routes(api: APIRouter) -> None:
  """The recent-request list and its live stream."""

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


def key_routes(api: APIRouter) -> None:
  """The API-key endpoints."""

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


def file_routes(
  api: APIRouter,
  apply: Callable[[dict[str, Any]], None],
  rebuild: Callable[[], None],
) -> None:
  """The provider-file endpoints: the files, the keys, the defaults and the saved values."""

  @api.get("/files")
  async def files(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    main = FILES[0]
    shadows = config.file_shadows(main)
    found = []
    for path in config_files():
      key = str(path)
      stamp = path.stat().st_mtime_ns if path.exists() else -1
      hit = _FILES_CACHE.get(key)
      if hit is None or hit[0] != stamp:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        blocks, error = form_blocks(path, text)
        hit = (stamp, {"path": key, "text": text, "blocks": blocks, "error": error})
        _FILES_CACHE[key] = hit
      entry = dict(hit[1])
      entry["main"] = path == main
      # A block of the main file keeps its provider keys, so the tab names the winner.
      entry["shadow"] = shadows.get(path.name)
      found.append(entry)
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
    return write_config(path, provider_edit.merge_text(old, document), apply, rebuild)

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
    rebuild()
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
    rebuild()
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
    return write_config(path, text, apply, rebuild)

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
    folder = str(FILES[0].parent.resolve())
    resolved = os.path.realpath(os.path.join(folder, f"{name}.yml"))
    if not resolved.startswith(folder + os.sep):
      return failure(
        400, "The name must stay in the provider folder.", "invalid_request_error"
      )
    path = Path(resolved)
    if path in config_files():
      return failure(400, f"{path} exists.", "invalid_request_error")
    text = new_file_text(name)
    path.write_text(text, encoding="utf-8")
    config.load_config(FILES[0])
    rebuild()
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
    answer = unlink_file(found[name], name)
    if answer is not None:
      return answer
    config.load_config(FILES[0])
    rebuild()
    return JSONResponse({"ok": True})


def settings_routes(
  api: APIRouter,
  apply: Callable[[dict[str, Any]], None],
  rebuild: Callable[[], None],
) -> None:
  """The settings and the hook-file endpoints."""

  @api.get("/settings")
  async def settings_values(request: Request) -> JSONResponse:
    if not allowed(request):
      return denied()
    path = settings.DEFAULT_PATH
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    raw = yaml.safe_load(text)
    records = remote.read_records()
    entries = {
      key: value
      for key, value in (raw or {}).get("hooks", {}).items()
      if key.startswith("on-")
    }
    return JSONResponse(
      {
        "path": str(path),
        "headroom_available": await headroom.available(),
        "hook_files": hooks.hook_files(),
        "hook_rows": [
          {**row, "record": records.get(row["name"], {})} for row in hooks.rows(entries)
        ],
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
      return write_config(path, body["text"], apply, rebuild)
    changes = body.get("changes")
    if not isinstance(changes, dict):
      return failure(400, "The changes must be an object.", "invalid_request_error")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
      text = settings.update_text(text, changes)
    except settings.SettingsError as exc:
      return failure(422, str(exc), "invalid_request_error")
    return write_config(path, text, apply, rebuild)

  @api.post("/hooks/scan")
  async def hooks_scan(request: Request) -> JSONResponse:
    """Read 1 source and answer with its hook files, without a write."""
    if not allowed(request):
      return denied()
    body = await json_body(request)
    if not isinstance(body, dict):
      return failure(400, "A repo is needed.", "invalid_request_error")
    found = remote.scan(body)
    if found is None:
      return failure(
        400, f"The source {body.get('repo')!r} did not read.", "invalid_request_error"
      )
    return JSONResponse(found)

  @api.post("/hooks/update")
  async def hooks_update(request: Request) -> JSONResponse:
    """Read the sources of `hooks.sources` now, and answer with the version of each file."""
    if not allowed(request):
      return denied()
    body = await json_body(request)
    source = body.get("source") if isinstance(body, dict) else None
    take = body.get("take") if isinstance(body, dict) else None
    group = settings.load()["hooks"]
    entries = [source] if isinstance(source, dict) else group["sources"]
    if not entries:
      return failure(400, "No source in hooks.sources.", "invalid_request_error")
    hooks.set_installed(group["dir"], group["disabled"])
    before = remote.read_records()
    report: list[str] = []
    moved = remote.update(
      entries,
      hooks.folder(),
      take=take if isinstance(take, list) else None,
      report=report,
    )
    for line in report:
      logger.warning("hooks update: %s", line)
    after = remote.read_records()
    repos = {
      repo
      for entry in entries
      if isinstance(entry, dict) and (repo := remote.repo_name(entry.get("repo")))
    }
    return JSONResponse(
      {
        "moved": moved,
        "report": report,
        "hooks": [
          {
            "name": name,
            "moved": name in moved,
            "before": before.get(name, {}),
            "after": record,
          }
          for name, record in sorted(after.items())
          if record["repo"] in repos
        ],
      }
    )

  @api.delete("/hooks/file")
  async def hooks_file_delete(request: Request) -> JSONResponse:
    """Remove 1 hook file from the hook folder, and leave its record out of the lock."""
    if not allowed(request):
      return denied()
    body = await json_body(request)
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name.strip():
      return failure(400, "A file name is needed.", "invalid_request_error")
    group = settings.load()["hooks"]
    hooks.set_installed(group["dir"], group["disabled"])
    path = hooks.resolve(name)
    if path is None:
      return failure(
        404, f"The hook {name} is not a file of the folder.", "invalid_request_error"
      )
    answer = unlink_file(path, name)
    if answer is not None:
      return answer
    records = remote.read_records()
    key = path.relative_to(hooks.folder()).as_posix()
    if key in records:
      del records[key]
      remote.write_records(records)
    return JSONResponse({"ok": True})


def routes(
  penalties: Penalties,
  get_config: Callable[[], Mapping[str, Any]],
  apply: Callable[[dict[str, Any]], None],
  cooldowns: Cooldowns | None = None,
  refresh: Callable[[], Callable[[], object] | None] = lambda: None,
  limits: Limits | None = None,
  rebuild_cached: Callable[[], Callable[[], object] | None] = lambda: None,
  affinity: Callable[[], dict[str, Any]] = dict,
) -> APIRouter:
  """The dashboard endpoints. All except login need a session."""
  api = APIRouter(prefix="/ui/api")

  def request_cached_rebuild() -> None:
    task = rebuild_cached()
    if task is not None:
      schedule.request(task)

  # With no limits, the page shows an empty list.
  seen = limits or Limits()
  login_routes(api)
  state_routes(api, penalties, cooldowns, affinity, refresh, seen)
  limit_routes(api, seen)
  pool_routes(api, penalties, get_config, cooldowns)
  history_routes(api)
  key_routes(api)
  file_routes(api, apply, request_cached_rebuild)
  settings_routes(api, apply, request_cached_rebuild)
  return api
