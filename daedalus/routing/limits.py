"""Provider limits: the rate-limit headers of each answer, and the balances that the provider keys can read."""

import asyncio
import json
import logging
import math
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
RESET_HISTORY = 50
RESET_LIMITS = {
  ("tokens", "minute"),
  ("requests", "minute"),
  ("requests", "hour"),
  ("requests", "day"),
  ("neurons", "day"),
}
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


def reset_event(found: Any) -> dict[str, Any] | None:
  """A stored reset event with its provider, client lane, limit and boundary."""
  if not isinstance(found, dict):
    return None
  provider, client = found.get("provider"), found.get("client")
  kind, span, at = found.get("kind"), found.get("span"), number(found.get("at"))
  if (
    not isinstance(provider, str)
    or not provider
    or (client is not None and not isinstance(client, str))
    or not isinstance(kind, str)
    or not isinstance(span, str)
    or (kind, span) not in RESET_LIMITS
    or at is None
    or not math.isfinite(at)
  ):
    return None
  return {"at": at, "provider": provider, "client": client, "kind": kind, "span": span}


def reset_key(event: Mapping[str, Any]) -> str:
  """The persisted scope of one reset: provider, client lane, kind and span."""
  return json.dumps(
    [event["provider"], event["client"], event["kind"], event["span"]],
    separators=(",", ":"),
  )


def saved_reset_events(found: Any) -> list[dict[str, Any]]:
  """The valid reset events in a stored list."""
  if not isinstance(found, list):
    return []
  return [event for item in found if (event := reset_event(item)) is not None]


def saved_pending_resets(found: Any) -> dict[str, dict[str, Any]]:
  """The newest stored pending reset for each scope."""
  pending: dict[str, dict[str, Any]] = {}
  for event in saved_reset_events(found):
    key = reset_key(event)
    if key not in pending or event["at"] > pending[key]["at"]:
      pending[key] = event
  return pending


def saved_reset_times(found: Any) -> dict[str, float]:
  """The latest emitted reset boundary of each stored scope."""
  if not isinstance(found, dict):
    return {}
  times = {str(key): number(value) for key, value in found.items()}
  return {
    key: value
    for key, value in times.items()
    if value is not None and math.isfinite(value)
  }


class Limits:
  """Provider limits, balances and reset notifications."""

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
    self.pending_resets: dict[str, dict[str, Any]] = {}
    self.emitted_resets: dict[str, float] = {}
    self.reset_history: list[dict[str, Any]] = []
    # The rows that daedalus counts itself, such as `hourly_requests`, for a wall-clock time.
    self.counted: Callable[[float], list[dict[str, Any]]] = lambda now: []

  def connect(self) -> sqlite3.Connection:
    return open_db(self.path(), (TABLE,))

  def save(self) -> None:
    """Write the limit view and reset state for the next start."""
    payload = json.dumps(
      {
        "lanes": self.lanes,
        "balances": self.balances,
        "checked": self.checked,
        "pending_resets": list(self.pending_resets.values()),
        "emitted_resets": self.emitted_resets,
        "reset_history": self.reset_history,
      }
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
    history = saved_reset_events(found.get("reset_history"))
    history.sort(key=lambda event: event["at"], reverse=True)
    self.reset_history = history[:RESET_HISTORY]
    self.emitted_resets = saved_reset_times(found.get("emitted_resets"))
    for event in self.reset_history:
      key = reset_key(event)
      self.emitted_resets[key] = max(self.emitted_resets.get(key, 0.0), event["at"])
    self.pending_resets = saved_pending_resets(found.get("pending_resets"))
    self.pending_resets = {
      key: event
      for key, event in self.pending_resets.items()
      if event["at"] > self.emitted_resets.get(key, 0.0)
    }

  def clear(self) -> None:
    self.lanes.clear()
    self.balances.clear()
    self.pending_resets.clear()
    self.emitted_resets.clear()
    self.reset_history.clear()
    self.checked = None
    database = self.connect()
    with database:
      database.execute("DELETE FROM limits WHERE name = ?", (SAVED,))
    database.close()

  def _schedule_reset(
    self,
    provider: Any,
    client: Any,
    kind: Any,
    span: Any,
    at: Any,
  ) -> bool:
    """Keep a newer pending boundary for one notification scope."""
    event = reset_event(
      {"provider": provider, "client": client, "kind": kind, "span": span, "at": at}
    )
    if event is None:
      return False
    key = reset_key(event)
    if event["at"] <= self.emitted_resets.get(key, 0.0):
      return False
    pending = self.pending_resets.get(key)
    if pending is not None and event["at"] <= pending["at"]:
      return False
    self.pending_resets[key] = event
    return True

  def _refresh_rows(self, now: float) -> bool:
    """Restore remembered allowances whose advertised boundary passed."""
    changed = False
    for seen in self.lanes.values():
      for row in seen["rows"]:
        reset, limit = number(row.get("reset")), number(row.get("limit"))
        if (
          (row.get("kind"), row.get("span")) not in RESET_LIMITS
          or reset is None
          or not math.isfinite(reset)
          or reset > now
          or limit is None
          or not math.isfinite(limit)
        ):
          continue
        row["remaining"], row["reset"] = limit, None
        changed = True
    return changed

  def _materialize_resets(self, now: float) -> bool:
    """Refresh due rows and move due boundaries into notification history."""
    changed = self._refresh_rows(now)
    due = sorted(
      (
        (key, event) for key, event in self.pending_resets.items() if event["at"] <= now
      ),
      key=lambda item: item[1]["at"],
    )
    if not due:
      return changed
    added = []
    for key, event in due:
      del self.pending_resets[key]
      if event["at"] <= self.emitted_resets.get(key, 0.0):
        continue
      self.emitted_resets[key] = event["at"]
      added.append(event)
    if added:
      self.reset_history.extend(added)
      self.reset_history.sort(key=lambda event: event["at"], reverse=True)
      del self.reset_history[RESET_HISTORY:]
    return True

  def resets(self) -> list[dict[str, Any]]:
    """Materialize due reset boundaries and return newest notifications first."""
    now = self.clock()
    changed = self._materialize_resets(now)
    for row in self.counted(now):
      changed |= self._schedule_reset(
        row.get("provider"),
        row.get("client"),
        row.get("kind"),
        row.get("span"),
        row.get("reset"),
      )
    changed |= self._materialize_resets(now)
    if changed:
      self.save()
    return [dict(event) for event in self.reset_history]

  def observe(self, lane: str, headers: Mapping[str, str]) -> None:
    """Keep one answer's rate limits, cooldowns and advertised reset boundaries."""
    now = self.clock()
    changed = self._materialize_resets(now)
    model, client = lanes.split(lane)
    provider = model.partition("/")[0]
    rows = header_rows(provider, headers, now)
    if not rows:
      if changed:
        self.save()
      return
    self.lanes[lane] = {"at": now, "rows": rows}
    for row in rows:
      self._schedule_reset(
        provider, client or None, row["kind"], row["span"], row["reset"]
      )
    ends = [end for row in rows if (end := cooling_end(row, now))]
    if ends and self.cooldowns:
      self.cooldowns.hold(lane, max(ends), "limit")
    self.save()

  async def check(self) -> None:
    """Read provider balances and keep their due or newly advertised reset boundaries."""
    now = self.clock()
    changed = self._materialize_resets(now)
    if self.client is None:
      if changed:
        self.save()
      return
    config, found, failed = self.config(), {}, set()
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
        if name == "cloudflare":
          self._schedule_reset(
            "cloudflare", None, "neurons", "day", next_midnight(now, UTC)
          )
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
