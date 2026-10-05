"""Model weights, session models (pins) and the reasoning level of the last answer, in the model store."""

import random
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from daedalus.store.database import open_db

# A pin with no request for this long expires.
IDLE_SECONDS = 3600.0
# In a session, the share of first-tier draws for the session model.
STAY = 0.85
SUCCESS, FAULT, SLOW, HOURLY, RATE_LIMIT = 1.5, 0.5, 0.75, 1.212, 0.75
# The lowest weight, so that a model with many faults can recover.
FLOOR = 0.01
TABLES = (
  (
    "CREATE TABLE IF NOT EXISTS weights"
    " (model TEXT PRIMARY KEY, weight REAL NOT NULL, updated REAL NOT NULL)"
  ),
  (
    "CREATE TABLE IF NOT EXISTS pins (key TEXT NOT NULL, slot TEXT NOT NULL,"
    " model TEXT NOT NULL, used REAL NOT NULL, PRIMARY KEY (key, slot))"
  ),
  (
    "CREATE TABLE IF NOT EXISTS tiers"
    " (key TEXT PRIMARY KEY, tier INTEGER NOT NULL, used REAL NOT NULL)"
  ),
  (
    "CREATE TABLE IF NOT EXISTS levels"
    " (key TEXT PRIMARY KEY, effort TEXT NOT NULL, used REAL NOT NULL)"
  ),
)


class Penalties:
  """Weights for each model and one session model (pin) for each (conversation, slot) pair."""

  def __init__(
    self,
    path: Callable[[], Path],
    clock: Callable[[], float] = time.time,
    pick: Callable[[], float] = random.random,
  ) -> None:
    self.path, self.clock, self.pick = path, clock, pick
    self.enabled, self.idle, self.stay, self.change_on_draw = (
      True,
      IDLE_SECONDS,
      STAY,
      True,
    )
    # The parallel race starts each request with the session model, so the draw does not move it.
    self.race = False
    self.success, self.fault, self.slow, self.hourly = SUCCESS, FAULT, SLOW, HOURLY
    self.rate_limit = RATE_LIMIT

  def connect(self) -> sqlite3.Connection:
    return open_db(self.path(), tuple(TABLES))

  def clear(self) -> None:
    """Remove all weights, pins and levels."""
    database = self.connect()
    with database:
      database.execute("DELETE FROM weights")
      database.execute("DELETE FROM pins")
      database.execute("DELETE FROM tiers")
      database.execute("DELETE FROM levels")
    database.close()

  def reset_weights(self) -> None:
    """Set all weights back to 1. The pins stay."""
    database = self.connect()
    with database:
      database.execute("DELETE FROM weights")
    database.close()

  def weights(self, models: list[str]) -> dict[str, float]:
    """The weight of each model now, with the hourly recovery."""
    if not self.enabled:
      return dict.fromkeys(models, 1.0)
    now = self.clock()
    database = self.connect()
    try:
      rows = dict.fromkeys(models, 1.0)
      for model, weight, updated in database.execute(
        "SELECT model, weight, updated FROM weights"
      ):
        if model in rows:
          hours = max(now - updated, 0.0) / 3600
          rows[model] = min(1.0, max(FLOOR, weight * self.hourly**hours))
      return rows
    finally:
      database.close()

  def record(self, model: str, factor: float) -> float:
    """Apply one factor and return the new weight."""
    return self.record_change(model, factor)[1]

  def record_change(self, model: str, factor: float) -> tuple[float, float]:
    """Apply one factor and return the previous and new weights."""
    if not self.enabled:
      return 1.0, 1.0
    previous = self.weights([model])[model]
    weight = min(1.0, max(FLOOR, previous * factor))
    database = self.connect()
    with database:
      database.execute(
        "INSERT OR REPLACE INTO weights (model, weight, updated) VALUES (?, ?, ?)",
        (model, weight, self.clock()),
      )
    database.close()
    return previous, weight

  def pinned(self, key: str, slot: str) -> str | None:
    """The live pin for one pair. A request never writes: the prune timer drops the idle pins."""
    database = self.connect()
    try:
      row = database.execute(
        "SELECT model FROM pins WHERE key = ? AND slot = ? AND used >= ?",
        (key, slot, self.clock() - self.idle),
      ).fetchone()
    finally:
      database.close()
    return row[0] if row else None

  def prune(self) -> None:
    """Drop the pins, tiers and levels that idled, for the timer."""
    now = self.clock() - self.idle
    database = self.connect()
    with database:
      database.execute("DELETE FROM pins WHERE used < ?", (now,))
      database.execute("DELETE FROM tiers WHERE used < ?", (now,))
      database.execute("DELETE FROM levels WHERE used < ?", (now,))
    database.close()

  def record_level(self, key: str, effort: str) -> None:
    """Keep the reasoning level of the last answer of one conversation."""
    database = self.connect()
    with database:
      database.execute(
        "INSERT OR REPLACE INTO levels (key, effort, used) VALUES (?, ?, ?)",
        (key, effort, self.clock()),
      )
    database.close()

  def last_level(self, key: str) -> str | None:
    """The reasoning level of the last answer of one conversation, or None."""
    database = self.connect()
    try:
      row = database.execute(
        "SELECT effort FROM levels WHERE key = ? AND used >= ?",
        (key, self.clock() - self.idle),
      ).fetchone()
    finally:
      database.close()
    return row[0] if row else None

  def last_pin(self, key: str) -> tuple[str, str] | None:
    """The most recently answered session slot and model for this conversation."""
    database = self.connect()
    try:
      row = database.execute(
        "SELECT slot, model FROM pins WHERE key = ? AND used >= ?"
        " ORDER BY used DESC, rowid DESC LIMIT 1",
        (key, self.clock() - self.idle),
      ).fetchone()
    finally:
      database.close()
    return tuple(row) if row else None

  def highest(self, key: str, tier: int) -> int:
    """The highest tier of one conversation, this request included. Idle entries expire."""
    now = self.clock()
    database = self.connect()
    with database:
      row = database.execute(
        "SELECT tier FROM tiers WHERE key = ? AND used >= ?", (key, now - self.idle)
      ).fetchone()
      top = max(tier, row[0]) if row else tier
      database.execute(
        "INSERT OR REPLACE INTO tiers (key, tier, used) VALUES (?, ?, ?)",
        (key, top, now),
      )
    database.close()
    return top

  def sessions(self) -> int:
    """The count of session models that did not expire."""
    database = self.connect()
    try:
      row = database.execute(
        "SELECT COUNT(*) FROM pins WHERE used >= ?", (self.clock() - self.idle,)
      ).fetchone()
    finally:
      database.close()
    return row[0]

  def pin(self, key: str, slot: str, model: str) -> str:
    """Pin the model that answered. Returns `hit` or `new` for the log."""
    old = self.pinned(key, slot)
    database = self.connect()
    with database:
      database.execute(
        "INSERT OR REPLACE INTO pins (key, slot, model, used) VALUES (?, ?, ?, ?)",
        (key, slot, model, self.clock()),
      )
    database.close()
    return "hit" if old == model else "new"

  def unpin(self, key: str, slot: str, model: str) -> bool:
    """Remove the pin when the pinned model failed, and tell if it did."""
    database = self.connect()
    with database:
      removed = database.execute(
        "DELETE FROM pins WHERE key = ? AND slot = ? AND model = ?", (key, slot, model)
      ).rowcount
    database.close()
    return removed > 0

  def order(
    self, groups: list[list[str]], key: str = "", slot: str | None = None
  ) -> list[str]:
    """The chain: a weighted random model of the first tier, then by weight in each tier."""
    seen: set[str] = set()
    groups = [[m for m in group if not (m in seen or seen.add(m))] for group in groups]
    weights = self.weights(list(seen))
    session = self.pinned(key, slot) if slot else None
    ranked = [
      m
      for group in groups
      for m in sorted(group, key=lambda m: (m != session, -weights[m]))
    ]
    tier = next((group for group in groups if group), [])
    if not self.enabled:
      first = session if session in seen else None
    elif self.race and session in tier:
      first = session
    else:
      draw, rest = dict(weights), sum(weights[m] for m in tier if m != session)
      if session in tier and rest:
        # The session weight that gives it the `stay` share of this tier.
        draw[session] = rest * self.stay / (1 - self.stay)
      first = self.choose(tier, draw)
    if first is None:
      return ranked
    return [first, *(m for m in ranked if m != first)]

  def choose(self, models: list[str], weights: dict[str, float]) -> str | None:
    """A random model, with a probability that follows its weight."""
    if not models:
      return None
    goal = self.pick() * sum(weights[m] for m in models)
    for model in models:
      goal -= weights[model]
      if goal < 0:
        return model
    return models[-1]
