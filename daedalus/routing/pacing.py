"""The counts of each model in the last minute and of each provider in the last hour, against their limits."""

import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from daedalus.config import FILE_KEY
from daedalus.routing import lanes

WINDOW = 60.0
HOUR = 3600.0

Limits = Mapping[str, tuple[float | None, float | None]]


class Pacing:
  """Counts in memory of the requests sent to each model."""

  def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
    self.clock = clock
    self.enabled = True
    self.sent: dict[str, deque[tuple[float, int]]] = {}
    # The provider config, the requests of each provider in the last hour, and the end of a 429 hold.
    self.config: Callable[[], Mapping[str, Any]] = dict
    self.hour: dict[str, deque[float]] = {}
    self.spent: dict[str, float] = {}

  def recent(self, model: str) -> deque[tuple[float, int]]:
    """The requests of one model that are still in the window."""
    found = self.sent.setdefault(model, deque())
    start = self.clock() - WINDOW
    while found and found[0][0] <= start:
      found.popleft()
    return found

  def caps(self) -> dict[str, int]:
    """The `hourly_requests` of each provider block that sets a whole number above 0."""
    found = {}
    for name, block in self.config().items():
      if not isinstance(block, Mapping):
        continue
      own = block.get(FILE_KEY)
      value = block.get(
        "hourly_requests",
        own.get("hourly_requests") if isinstance(own, Mapping) else None,
      )
      if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        found[name] = value
    return found

  def in_hour(self, provider: str) -> deque[float]:
    """The requests of one provider that are still in the hour."""
    found = self.hour.setdefault(provider, deque())
    start = self.clock() - HOUR
    while found and found[0] <= start:
      found.popleft()
    return found

  def hour_end(self, provider: str) -> float | None:
    """The clock time when the provider can take a request again, or None when it can now."""
    cap = self.caps().get(provider)
    if not self.enabled or cap is None:
      return None
    now = self.clock()
    if self.spent.get(provider, 0.0) > now:
      return self.spent[provider]
    found = self.in_hour(provider)
    return found[0] + HOUR if len(found) >= cap else None

  def full(self, model: str, limits: Limits) -> bool:
    """Tell if the model or lane reached the rpm or tpm of the model, or its provider reached `hourly_requests`."""
    name = lanes.split(model)[0]
    if self.hour_end(name.partition("/")[0]) is not None:
      return True
    rpm, tpm = limits.get(name, (None, None))
    if not self.enabled or (rpm is None and tpm is None):
      return False
    found = self.recent(model)
    if rpm is not None and len(found) >= rpm:
      return True
    return tpm is not None and sum(tokens for _, tokens in found) >= tpm

  def wait(self, models: Iterable[str]) -> float:
    """The seconds until the first of these models can take a request again."""
    ends = []
    for model in models:
      hour = self.hour_end(lanes.split(model)[0].partition("/")[0])
      found = self.recent(model)
      minute = found[0][0] + WINDOW if found else None
      if hour is not None or minute is not None:
        ends.append(max(end for end in (hour, minute) if end is not None))
    return max(0.0, min(ends) - self.clock()) if ends else 0.0

  def record(self, model: str, tokens: int = 0) -> None:
    """Count 1 request to the model."""
    if self.enabled:
      self.recent(model).append((self.clock(), tokens))
      provider = lanes.split(model)[0].partition("/")[0]
      if provider in self.caps():
        self.in_hour(provider).append(self.clock())

  def used_up(self, model: str) -> None:
    """After a 429, count the rest of the hour of the provider as used."""
    provider = lanes.split(model)[0].partition("/")[0]
    if provider in self.caps():
      found = self.in_hour(provider)
      self.spent[provider] = (found[0] if found else self.clock()) + HOUR

  def hour_rows(self, now: float) -> list[dict[str, Any]]:
    """A counted rate-limit row for each provider with `hourly_requests`, with wall-clock times from `now`."""
    rows = []
    for provider, cap in sorted(self.caps().items()):
      found, end = self.in_hour(provider), self.hour_end(provider)
      held = self.spent.get(provider, 0.0) > self.clock()
      reset = end if end is not None else (found[0] + HOUR if found else None)
      rows.append(
        {
          "provider": provider,
          "kind": "requests",
          "span": "hour",
          "limit": cap,
          "remaining": 0 if held else max(0, cap - len(found)),
          "reset": now + reset - self.clock() if reset is not None else None,
        }
      )
    return rows

  def clear(self) -> None:
    """Forget all counts."""
    self.sent.clear()
    self.hour.clear()
    self.spent.clear()
