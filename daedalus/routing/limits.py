"""Provider limits: the rate-limit headers of each answer, and the balances that the provider keys can read."""

import asyncio
import json
import logging
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from daedalus import providers, store
from daedalus.config import file_block, main_block
from daedalus.routing import lanes
from daedalus.routing.cooldowns import Cooldowns, header_seconds, next_midnight
from daedalus.store.database import open_db

CHECK_SECONDS = 3600.0
REQUEST_SECONDS = 20.0
# The lane rows and the balance cards of the last look, so a restart keeps the Limits tab.
SAVED = "view"
TABLE = (
  "CREATE TABLE IF NOT EXISTS limits (name TEXT PRIMARY KEY, payload TEXT NOT NULL,"
  " updated REAL NOT NULL)"
)
# A window may sit in the name, as Mistral does, or the name may carry none, as OpenRouter does.
HEADER = re.compile(r"x-ratelimit-(limit|remaining|reset)(?:-(.+))?")
# Groq headers have no window in the name: the requests count per day, the tokens per minute.
GROQ_SPANS = {"requests": "day", "tokens": "minute"}
KINDS = {"req": "requests"}
COOLING_SPANS = ("day", "month")
CLOUDFLARE_FREE = 10_000
CLOUDFLARE_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
ACCOUNT = re.compile(r"/accounts/([^/]+)/")
NEURONS = (
  "query ($account: string!, $start: Time!) { viewer {"
  " accounts(filter: {accountTag: $account}) { aiInferenceAdaptiveGroups(limit: 100,"
  " filter: {datetimeHour_geq: $start}) { sum { totalNeurons } } } } }"
)
logger = logging.getLogger("daedalus")
# A label, a value, and the part of the limit that is left for a bar, or None.
Item = tuple[str, str, float | None]


def number(value: Any) -> float | None:
  """A number from a header or a JSON value, or None."""
  if isinstance(value, bool):
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def label(provider: str, name: str) -> tuple[str, str | None]:
  """The kind and the window of a header group, such as tokens and 5 minute."""
  if not name:
    # A header set with no window counts requests for a day, the OpenRouter way.
    return "requests", "day"
  kind, _, span = name.partition("-")
  if provider == "groq" and not span:
    span = GROQ_SPANS.get(kind, "")
  return KINDS.get(kind, kind), span.replace("-", " ") or None


def header_rows(
  provider: str, headers: Mapping[str, str], now: float
) -> list[dict[str, Any]]:
  """The limit, remaining count and reset time of each rate-limit header group that has both counts."""
  groups: dict[str, dict[str, str]] = {}
  for key, value in headers.items():
    found = HEADER.fullmatch(key.lower())
    if found:
      groups.setdefault(found[2] or "", {})[found[1]] = value
  rows = []
  for name, values in sorted(groups.items()):
    limit, remaining = number(values.get("limit")), number(values.get("remaining"))
    if limit is None or remaining is None:
      continue
    seconds = header_seconds(values["reset"], now) if values.get("reset") else None
    kind, span = label(provider, name)
    rows.append(
      {
        "kind": kind,
        "span": span,
        "limit": limit,
        "remaining": remaining,
        "reset": now + seconds if seconds and seconds > 0 else None,
      }
    )
  return rows


def cooling_end(row: Mapping[str, Any], now: float) -> float | None:
  """The cooldown end of a row with 0 left of a day or a month, or None."""
  if row["remaining"] > 0 or row["span"] not in COOLING_SPANS:
    return None
  if row["reset"]:
    return row["reset"]
  if row["span"] == "day":
    return next_midnight(now, UTC)
  day = datetime.fromtimestamp(now, UTC)
  return datetime(
    day.year + day.month // 12, day.month % 12 + 1, 1, tzinfo=UTC
  ).timestamp()


def money(value: float) -> str:
  return f"${value:,.2f}"


def keyed_block(provider: Any) -> dict[str, Any] | None:
  """The first block of a provider with an API key: the main file, else the provider file."""
  for block in (main_block(provider), file_block(provider)):
    key = block.get("api_key") if block else None
    if isinstance(key, str) and key:
      return block
  return None


async def get_json(
  client: httpx.AsyncClient, method: str, url: str, key: str, **content: Any
) -> Any:
  response = await client.request(
    method,
    url,
    headers={"Authorization": f"Bearer {key}"},
    timeout=REQUEST_SECONDS,
    **content,
  )
  response.raise_for_status()
  return response.json()


async def openrouter(client: httpx.AsyncClient, base: str, key: str) -> Any:
  return (await get_json(client, "GET", f"{base}/key", key)).get("data")


async def kilo(client: httpx.AsyncClient, base: str, key: str) -> Any:
  root = base.split("/api/")[0]
  return await get_json(client, "GET", f"{root}/api/profile/balance", key)


async def pollinations(client: httpx.AsyncClient, base: str, key: str) -> Any:
  return await get_json(
    client, "GET", f"{base.removesuffix('/v1')}/account/balance", key
  )


async def cloudflare(client: httpx.AsyncClient, base: str, key: str) -> Any:
  account = ACCOUNT.search(base)
  if account is None:
    return None
  now = datetime.now(UTC)
  start = datetime(now.year, now.month, now.day, tzinfo=UTC)
  variables = {"account": account[1], "start": start.isoformat().replace("+00:00", "Z")}
  body = {"query": NEURONS, "variables": variables}
  return await get_json(client, "POST", CLOUDFLARE_GRAPHQL, key, json=body)


def share(left: float, limit: float) -> float | None:
  """The part of a limit that is left, from 0 to 1, or None without a limit."""
  return max(0.0, min(1.0, left / limit)) if limit > 0 else None


def openrouter_items(data: Any) -> list[Item]:
  items: list[Item] = []
  left, limit = number(data.get("limit_remaining")), number(data.get("limit"))
  if left is not None and limit is not None:
    items.append(
      ("Credit left", f"{money(left)} of {money(limit)}", share(left, limit))
    )
  used = number(data.get("usage_daily"))
  if used:
    items.append(("Used today", money(used), None))
  free = data.get("free_model_daily_requests")
  free = free if isinstance(free, dict) else {}
  left, limit = number(free.get("remaining")), number(free.get("limit"))
  if left is not None and limit is not None:
    text = f"{floored(left)} of {floored(limit)} left"
    items.append(("Free requests today", text, share(left, limit)))
  return items


def floored(value: float) -> str:
  """A count floored to K, M or B, for example 8K. Below 1,000 it stays whole."""
  for size, mark in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
    if value >= size:
      return f"{int(value // size)}{mark}"
  return f"{value:,.0f}"


def balance_items(
  name: str, text: Callable[[float], str]
) -> Callable[[Any], list[Item]]:
  """The values of a `balance` answer, with 1 label and 1 number format."""

  def items(data: Any) -> list[Item]:
    found = number(data.get("balance"))
    return [] if found is None else [(name, text(found), None)]

  return items


def neuron_items(data: Any) -> list[Item]:
  """The sum of the neurons of all the groups today. No group and no errors means 0."""
  accounts = ((data.get("data") or {}).get("viewer") or {}).get("accounts")
  if data.get("errors") or not accounts:
    return []
  groups = accounts[0].get("aiInferenceAdaptiveGroups")
  if not isinstance(groups, list):
    return []
  values = [number((group.get("sum") or {}).get("totalNeurons")) for group in groups]
  if None in values:
    return []
  used = sum(value for value in values if value is not None)
  left = share(CLOUDFLARE_FREE - used, CLOUDFLARE_FREE)
  # The value and the bar show the same quantity: the neurons that are left today.
  return [
    (
      "Neurons today",
      f"{floored(CLOUDFLARE_FREE - used)} of {floored(CLOUDFLARE_FREE)} left",
      left,
    )
  ]


Reader = Callable[[httpx.AsyncClient, str, str], Awaitable[Any]]
# The balance reader of each provider, and the values it shows.
READERS: dict[str, tuple[Reader, Callable[[Any], list[Item]]]] = {
  "openrouter": (openrouter, openrouter_items),
  "kilo": (kilo, balance_items("Balance", money)),
  "pollinations": (pollinations, balance_items("Pollen", "{:,.2f}".format)),
  "cloudflare": (cloudflare, neuron_items),
}


def saved_lanes(found: Any) -> dict[str, dict[str, Any]]:
  """The lane rows of a stored view: a lane with its time and its list of rows."""
  if not isinstance(found, dict):
    return {}
  return {
    str(lane): {
      "at": seen["at"],
      "rows": [row for row in seen["rows"] if isinstance(row, dict)],
    }
    for lane, seen in found.items()
    if isinstance(seen, dict)
    and isinstance(seen.get("at"), (int, float))
    and isinstance(seen.get("rows"), list)
  }


def saved_balances(found: Any) -> dict[str, list[Item]]:
  """The balance cards of a stored view: a name with its label, value and bar rows."""
  if not isinstance(found, dict):
    return {}
  return {
    str(name): [
      tuple(item) for item in items if isinstance(item, list) and len(item) == 3
    ]
    for name, items in found.items()
    if isinstance(items, list)
  }


class Limits:
  """The last rate-limit headers of each lane, and the provider balances of the last check."""

  def __init__(
    self,
    cooldowns: Cooldowns | None = None,
    config: Callable[[], Mapping[str, Any]] = dict,
    client: Callable[[], httpx.AsyncClient] | None = None,
    clock: Callable[[], float] = time.time,
    path: Callable[[], Path] | None = None,
  ) -> None:
    self.cooldowns, self.config, self.client, self.clock = (
      cooldowns,
      config,
      client,
      clock,
    )
    self.path: Callable[[], Path] = path or (lambda: store.MODELS_DB)
    self.lanes: dict[str, dict[str, Any]] = {}
    self.balances: dict[str, list[Item]] = {}
    self.checked: float | None = None
    # The rows that daedalus counts itself, such as `hourly_requests`, for a wall-clock time.
    self.counted: Callable[[float], list[dict[str, Any]]] = lambda now: []

  def connect(self) -> sqlite3.Connection:
    return open_db(self.path(), (TABLE,))

  def save(self) -> None:
    """Write the lane rows and the balance cards, so the next start shows the last look."""
    payload = json.dumps(
      {"lanes": self.lanes, "balances": self.balances, "checked": self.checked}
    )
    database = self.connect()
    with database:
      database.execute(
        "INSERT INTO limits (name, payload, updated) VALUES (?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET payload = excluded.payload,"
        " updated = excluded.updated",
        (SAVED, payload, self.clock()),
      )
    database.close()

  def restore(self) -> None:
    """Load the lane rows and the balance cards of the last run. A bad row is dropped."""
    if not self.path().exists():
      return
    database = self.connect()
    try:
      row = database.execute(
        "SELECT payload FROM limits WHERE name = ?", (SAVED,)
      ).fetchone()
    except sqlite3.OperationalError:
      return
    finally:
      database.close()
    try:
      found = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
      return
    if not isinstance(found, dict):
      return
    self.lanes = saved_lanes(found.get("lanes")) or self.lanes
    self.balances = saved_balances(found.get("balances")) or self.balances
    checked = found.get("checked")
    if isinstance(checked, (int, float)) and not isinstance(checked, bool):
      self.checked = float(checked)

  def clear(self) -> None:
    self.lanes.clear()
    self.balances.clear()
    self.checked = None
    database = self.connect()
    with database:
      database.execute("DELETE FROM limits WHERE name = ?", (SAVED,))
    database.close()

  def observe(self, lane: str, headers: Mapping[str, str]) -> None:
    """Keep the rate-limit headers of one answer. 0 left of a day or a month cools the lane down to the reset."""
    now = self.clock()
    rows = header_rows(lane.partition("/")[0], headers, now)
    if not rows:
      return
    self.lanes[lane] = {"at": now, "rows": rows}
    ends = [end for row in rows if (end := cooling_end(row, now))]
    if ends and self.cooldowns:
      self.cooldowns.hold(lane, max(ends), "limit")
    self.save()

  async def check(self) -> None:
    """Read the balance of each provider key that has a balance endpoint."""
    if self.client is None:
      return
    now, config, found, failed = self.clock(), self.config(), {}, set()
    for name, (read, items) in READERS.items():
      block = keyed_block(config.get(name))
      if block is None:
        continue
      merged = providers.settings(name, block)
      try:
        data = await read(
          self.client(), str(merged["api_base"]).rstrip("/"), merged["api_key"]
        )
        shown = items(data) if isinstance(data, dict) else []
      except (httpx.HTTPError, ValueError, AttributeError, IndexError) as exc:
        logger.info("limits %s: %s", name, exc)
        failed.add(name)
        continue
      if shown:
        found[name] = shown
      if name == "openrouter" and isinstance(data, dict):
        self.free_used_up(data, now)
    # A read that fails now keeps the last card of that provider, the way a restart keeps
    # the rows: a refused key must not empty the Limits tab.
    kept = {name: items for name, items in self.balances.items() if name in failed}
    self.balances, self.checked = {**kept, **found}, now
    self.save()

  def free_used_up(self, data: Mapping[str, Any], now: float) -> None:
    """Cool down the OpenRouter free models to the next UTC day when no free request is left."""
    free = data.get("free_model_daily_requests")
    if (
      not isinstance(free, dict)
      or number(free.get("remaining")) != 0
      or not self.cooldowns
    ):
      return
    end = next_midnight(now, UTC)
    for model in store.read_models(routable_only=False):
      if model.startswith("openrouter/") and model.endswith(":free"):
        self.cooldowns.hold(model, end, "limit")

  async def run(self) -> None:
    """Check the balances at the start and then each hour, until the task stops."""
    while True:
      try:
        await self.check()
      except Exception:
        logger.exception("limit check failed")
      await asyncio.sleep(CHECK_SECONDS)

  def view(self) -> dict[str, Any]:
    """The balances and the header rows for the dashboard."""
    rows = []
    for lane, seen in sorted(self.lanes.items()):
      model, client = lanes.split(lane)
      rows.append({"model": model, "client": client or None, **seen})
    shown = [
      {"name": name, "items": [list(item) for item in items]}
      for name, items in self.balances.items()
    ]
    # A counted limit goes into the card of its provider, below the balance.
    for row in self.counted(self.clock()):
      card = next((c for c in shown if c["name"] == row["provider"]), None)
      if card is None:
        card = {"name": row["provider"], "items": []}
        shown.append(card)
      left, limit = row["remaining"], row["limit"]
      card["items"].append(
        [
          "Requests left this hour",
          f"{left:,} of {limit:,}",
          share(left, limit),
        ]
      )
    return {"checked": self.checked, "providers": shown, "lanes": rows}
