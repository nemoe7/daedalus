"""The requests and input tokens of each model in the last minute, against its rpm and tpm."""

import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping

WINDOW = 60.0

Limits = Mapping[str, tuple[float | None, float | None]]


class Pacing:
  """Counts in memory of the requests sent to each model."""

  def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
    self.clock = clock
    self.enabled = True
    self.sent: dict[str, deque[tuple[float, int]]] = {}

  def recent(self, model: str) -> deque[tuple[float, int]]:
    """The requests of one model that are still in the window."""
    found = self.sent.setdefault(model, deque())
    start = self.clock() - WINDOW
    while found and found[0][0] <= start:
      found.popleft()
    return found

  def full(self, model: str, limits: Limits) -> bool:
    """Tell if the model reached its rpm or tpm."""
    rpm, tpm = limits.get(model, (None, None))
    if not self.enabled or (rpm is None and tpm is None):
      return False
    found = self.recent(model)
    if rpm is not None and len(found) >= rpm:
      return True
    return tpm is not None and sum(tokens for _, tokens in found) >= tpm

  def wait(self, models: Iterable[str]) -> float:
    """The seconds until the oldest request of these models leaves the window."""
    oldest = [self.recent(m)[0][0] for m in models if self.recent(m)]
    return min(oldest) + WINDOW - self.clock() if oldest else 0.0

  def record(self, model: str, tokens: int = 0) -> None:
    """Count 1 request to the model."""
    if self.enabled:
      self.recent(model).append((self.clock(), tokens))

  def clear(self) -> None:
    """Forget all counts."""
    self.sent.clear()
