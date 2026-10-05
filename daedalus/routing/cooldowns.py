"""Rate-limit cooldowns: the end of each cooldown, kept in the model store."""

import email.utils
import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from daedalus.routing import lanes
from daedalus.store.database import open_db

FIRST, LONGEST = 60.0, 21600.0
# A provider reset time above 1 day is a bad header: a model must not leave for ever.
LONGEST_RESET = 86400.0
PACIFIC = ZoneInfo("America/Los_Angeles")
# Cloudflare error 4006: the daily free neurons of the account are used up.
CLOUDFLARE_DAILY = 4006
TABLE = (
  "CREATE TABLE IF NOT EXISTS cooldowns (model TEXT PRIMARY KEY, until REAL NOT NULL,"
  " span REAL NOT NULL, reason TEXT NOT NULL)"
)
logger = logging.getLogger("daedalus")
DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def duration(text: str) -> float | None:
  """The seconds of a duration such as `38s`, `1.5s`, `2m59.56s` or `250ms`."""
  text = text.strip()
  parts = DURATION.findall(text)
  if not parts or "".join(n + u for n, u in parts) != text:
    return None
  scale = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
  return sum(float(number) * scale[unit] for number, unit in parts)


def next_midnight(now: float, zone: ZoneInfo | Any) -> float:
  """The time of the next 00:00 in a time zone."""
  local = datetime.fromtimestamp(now, zone)
  start = datetime(local.year, local.month, local.day, tzinfo=zone) + timedelta(days=1)
  return start.timestamp()


def parsed(body: bytes) -> dict[str, Any]:
  try:
    found = json.loads(body)
  except (ValueError, UnicodeDecodeError):
    return {}
  return found if isinstance(found, dict) else {}


def error_message(body: dict[str, Any]) -> str:
  """The message of the error body, or an empty text."""
  error = body.get("error")
  return str(error.get("message", "")) if isinstance(error, dict) else ""


def error_headers(body: dict[str, Any]) -> dict[str, str]:
  """The headers of the error body, such as the reset of an OpenRouter daily cap."""
  error = body.get("error")
  metadata = error.get("metadata") if isinstance(error, dict) else None
  headers = metadata.get("headers") if isinstance(metadata, dict) else None
  if not isinstance(headers, dict):
    return {}
  return {str(name).lower(): str(value) for name, value in headers.items()}


def gemini_details(body: dict[str, Any]) -> list[dict[str, Any]]:
  error = body.get("error")
  details = error.get("details") if isinstance(error, dict) else None
  return (
    [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []
  )


def daily_end(model: str, body: dict[str, Any], now: float) -> tuple[str, float] | None:
  """Rule 1: the cooldown key and end of a daily limit, or None. The model can be a lane."""
  provider = model.partition("/")[0]
  client = lanes.split(model)[1]
  if provider == "gemini":
    for detail in gemini_details(body):
      for violation in detail.get("violations") or []:
        if isinstance(violation, dict) and "PerDay" in str(
          violation.get("quotaId", "")
        ):
          return model, next_midnight(now, PACIFIC)
  if provider == "cloudflare":
    errors = body.get("errors")
    codes = (
      [e.get("code") for e in errors if isinstance(e, dict)]
      if isinstance(errors, list)
      else []
    )
    if CLOUDFLARE_DAILY in codes:
      whole = lanes.join("cloudflare/*", client) if client else "cloudflare/*"
      return whole, next_midnight(now, UTC)
  if "limit_rpd" in error_message(body):
    # A requests-per-day cap holds the model to the reset of the body, or to 00:00 UTC.
    seconds = reset_seconds({}, body, now)
    return model, now + seconds if seconds else next_midnight(now, UTC)
  return None


def header_seconds(value: str, now: float) -> float | None:
  """The seconds to wait from a reset header: seconds, a Unix time, a duration or a date."""
  value = value.strip()
  try:
    number = float(value)
  except ValueError:
    number = None
  if number is not None:
    # A value this large is a Unix time in ms, and the range below it is a Unix time in s.
    if number > 1e12:
      return number / 1000 - now
    return number - now if number > 1e9 else number
  found = duration(value)
  if found is not None:
    return found
  try:
    return email.utils.parsedate_to_datetime(value).timestamp() - now
  except (TypeError, ValueError):
    return None


def capped(seconds: float) -> float:
  """Cut a provider reset time at the cap, and log the cut."""
  if seconds > LONGEST_RESET:
    logger.warning(
      "the reset time %.0fs is above the cap of %.0fs: it is cut",
      seconds,
      LONGEST_RESET,
    )
    return LONGEST_RESET
  return seconds


def reset_seconds(
  headers: Mapping[str, str], body: dict[str, Any], now: float
) -> float | None:
  """Rule 2: the seconds to the reset time that the provider gives, cut at the cap."""
  for source in (headers, error_headers(body)):
    for name in ("retry-after", "x-ratelimit-reset"):
      if source.get(name):
        seconds = header_seconds(source[name], now)
        if seconds is not None and seconds > 0:
          return capped(seconds)
  for detail in gemini_details(body):
    if str(detail.get("@type", "")).endswith("RetryInfo"):
      seconds = duration(str(detail.get("retryDelay", "")))
      if seconds:
        return capped(seconds)
  return None


def key_of(model: str) -> list[str]:
  """The cooldown keys that apply to one model or lane: its own key and its provider key."""
  client = lanes.split(model)[1]
  whole = f"{model.partition('/')[0]}/*"
  return [model, lanes.join(whole, client) if client else whole]


class Cooldowns:
  """The cooldown end of each model, and the last backoff span, in the model store."""

  def __init__(
    self, path: Callable[[], Path], clock: Callable[[], float] = time.time
  ) -> None:
    self.path, self.clock = path, clock
    self.first, self.longest = FIRST, LONGEST

  def connect(self) -> sqlite3.Connection:
    return open_db(self.path(), (TABLE,))

  def clear(self) -> None:
    database = self.connect()
    with database:
      database.execute("DELETE FROM cooldowns")
    database.close()

  def ends(self) -> dict[str, float]:
    """The end of each cooldown that has not ended."""
    database = self.connect()
    try:
      rows = database.execute(
        "SELECT model, until FROM cooldowns WHERE until > ?", (self.clock(),)
      ).fetchall()
    finally:
      database.close()
    return dict(rows)

  @staticmethod
  def until(model: str, ends: Mapping[str, float]) -> float | None:
    """The cooldown end of one model, from its own row or its provider row."""
    found = [ends[key] for key in key_of(model) if key in ends]
    return max(found) if found else None

  @staticmethod
  def clients(model: str, ends: Mapping[str, float]) -> dict[str, float]:
    """The cooldown end of each client lane of one model, from its own rows or its provider rows."""
    whole = f"{model.partition('/')[0]}/*"
    found: dict[str, float] = {}
    for key, end in ends.items():
      base, client = lanes.split(key)
      if client and base in (model, whole):
        found[client] = max(end, found.get(client, 0.0))
    return found

  def start(
    self, model: str, headers: Mapping[str, str], body: bytes
  ) -> dict[str, Any]:
    """Start and log the cooldown of a rate limit, and return its seconds and its reason."""
    seconds, reason = self.begin(model, headers, body)
    logger.info("cooldown %s %.3fs reason=%s", model, seconds, reason)
    return {"seconds": round(seconds, 3), "reason": reason}

  def begin(
    self, model: str, headers: Mapping[str, str], body: bytes
  ) -> tuple[float, str]:
    """Write the cooldown of a rate limit, and return its seconds and its reason."""
    now, content = self.clock(), parsed(body)
    database = self.connect()
    try:
      with database:
        daily = daily_end(model, content, now)
        if daily is not None:
          key, until = daily
          database.execute(
            "INSERT INTO cooldowns (model, until, span, reason) VALUES (?, ?, 0, 'daily')"
            " ON CONFLICT (model) DO UPDATE SET until = excluded.until, reason = 'daily'",
            (key, until),
          )
          return until - now, "daily"
        seconds, reason = reset_seconds(headers, content, now), "reset"
        span = None
        if seconds is None:
          row = database.execute(
            "SELECT span FROM cooldowns WHERE model = ?", (model,)
          ).fetchone()
          last = row[0] if row else 0.0
          span = min(last * 2, self.longest) if last else self.first
          seconds, reason = span, "backoff"
        database.execute(
          "INSERT INTO cooldowns (model, until, span, reason) VALUES (?, ?, ?, ?)"
          " ON CONFLICT (model) DO UPDATE SET until = excluded.until,"
          " span = COALESCE(?, span), reason = excluded.reason",
          (model, now + seconds, span or 0.0, reason, span),
        )
        return seconds, reason
    finally:
      database.close()

  def hold(self, model: str, until: float, reason: str) -> None:
    """Write a cooldown with a known end, such as the reset of a daily limit. A longer cooldown stays."""
    database = self.connect()
    with database:
      database.execute(
        "INSERT INTO cooldowns (model, until, span, reason) VALUES (?, ?, 0, ?)"
        " ON CONFLICT (model) DO UPDATE SET until = MAX(until, excluded.until),"
        " reason = CASE WHEN excluded.until > until THEN excluded.reason ELSE reason END",
        (model, until, reason),
      )
    database.close()
    logger.info("cooldown %s %.3fs reason=%s", model, until - self.clock(), reason)

  def succeeded(self, model: str) -> None:
    """Set the backoff of a model back to the first span."""
    database = self.connect()
    with database:
      database.execute("UPDATE cooldowns SET span = 0 WHERE model = ?", (model,))
    database.close()
