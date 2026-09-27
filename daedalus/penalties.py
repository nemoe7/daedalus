"""Model weights and session models (pins), kept in the model store."""

import random
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

# A pin with no request for this long expires.
IDLE_SECONDS = 3600.0
# In a session, each model other than the session model uses its weight times this.
OTHERS = 0.05
SUCCESS, FAULT, SLOW, HOURLY = 1.5, 0.5, 0.75, 1.2
TABLES = (
  (
    "CREATE TABLE IF NOT EXISTS weights"
    " (model TEXT PRIMARY KEY, weight REAL NOT NULL, updated REAL NOT NULL)"
  ),
  (
    "CREATE TABLE IF NOT EXISTS pins (key TEXT NOT NULL, slot TEXT NOT NULL,"
    " model TEXT NOT NULL, used REAL NOT NULL, PRIMARY KEY (key, slot))"
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
    self.enabled, self.idle, self.others = True, IDLE_SECONDS, OTHERS
    self.success, self.fault, self.slow, self.hourly = SUCCESS, FAULT, SLOW, HOURLY

  def connect(self) -> sqlite3.Connection:
    target = Path(self.path())
    target.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(target, timeout=5)
    for statement in TABLES:
      database.execute(statement)
    return database

  def clear(self) -> None:
    """Remove all weights and pins."""
    database = self.connect()
    with database:
      database.execute("DELETE FROM weights")
      database.execute("DELETE FROM pins")
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
          rows[model] = min(1.0, weight * self.hourly**hours)
      return rows
    finally:
      database.close()

  def record(self, model: str, factor: float) -> float:
    """Multiply the weight by the factor of one event, 1 at most."""
    if not self.enabled:
      return 1.0
    weight = min(1.0, self.weights([model])[model] * factor)
    database = self.connect()
    with database:
      database.execute(
        "INSERT OR REPLACE INTO weights (model, weight, updated) VALUES (?, ?, ?)",
        (model, weight, self.clock()),
      )
    database.close()
    return weight

  def pinned(self, key: str, slot: str) -> str | None:
    """The live pin for one pair, after it drops the pins that idled too long."""
    database = self.connect()
    with database:
      database.execute("DELETE FROM pins WHERE used < ?", (self.clock() - self.idle,))
      row = database.execute(
        "SELECT model FROM pins WHERE key = ? AND slot = ?", (key, slot)
      ).fetchone()
    database.close()
    return row[0] if row else None

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
    if session in seen:
      weights = {m: w if m == session else w * self.others for m, w in weights.items()}
    ranked = [m for group in groups for m in sorted(group, key=lambda m: -weights[m])]
    if not self.enabled:
      first = session if session in seen else None
    else:
      first = self.choose(next((group for group in groups if group), []), weights)
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
