"""Session affinity: one pinned model per API key and pool, until it fails or idles."""

import time
from collections.abc import Callable

# A pin with no request for this long expires.
IDLE_SECONDS = 3600.0


class Pins:
  """The pinned model for each (affinity key, slot) pair, in memory."""

  def __init__(
    self, idle: float = IDLE_SECONDS, clock: Callable[[], float] = time.monotonic
  ) -> None:
    self.idle, self.clock = idle, clock
    self.pins: dict[tuple[str, str], tuple[str, float]] = {}

  def pinned(self, key: str, slot: str) -> str | None:
    """The live pin for one pair, after it drops the pins that idled too long."""
    now = self.clock()
    for pair, (_, used) in list(self.pins.items()):
      if now - used > self.idle:
        del self.pins[pair]
    found = self.pins.get((key, slot))
    return found[0] if found else None

  def order(self, key: str, slot: str, models: list[str]) -> list[str]:
    """Put the pin first when it is still in the chain."""
    pin = self.pinned(key, slot)
    if pin not in models:
      return models
    return [pin, *(model for model in models if model != pin)]

  def answered(self, key: str, slot: str, model: str) -> str:
    """Pin the model that answered. Returns `hit`, `new` or `moved` for the log."""
    old = self.pinned(key, slot)
    self.pins[(key, slot)] = (model, self.clock())
    if old == model:
      return "hit"
    return "new" if old is None else "moved"

  def failed(self, key: str, slot: str, model: str) -> bool:
    """Remove the pin when the pinned model failed, and tell if it did."""
    if self.pinned(key, slot) != model:
      return False
    del self.pins[(key, slot)]
    return True
