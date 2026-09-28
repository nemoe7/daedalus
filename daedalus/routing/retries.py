"""Find a try again, and keep the models that answered each message or content."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from daedalus.store import keys

# Open WebUI sends it when `ENABLE_FORWARD_USER_INFO_HEADERS` is true.
CHAT_HEADER = "x-openwebui-chat-id"
TOP_TIER = 4


@dataclass
class Turn:
  """One chat message: the tier and model of the last answer, and the retry count."""

  used: float
  tier: int | None = None
  model: str | None = None
  count: int = 0
  answered: set[str] = field(default_factory=set)


def digest(messages: list[dict]) -> str:
  """The hash of the messages without the system messages, which can hold the time."""
  kept = [m for m in messages if m.get("role") != "system"]
  return keys.digest(json.dumps(kept, sort_keys=True, ensure_ascii=False))


class Retries:
  """The messages of each chat. Idle messages expire."""

  def __init__(
    self, idle: float = 3600, clock: Callable[[], float] = time.time
  ) -> None:
    self.idle = idle
    self.clock = clock
    self.turns: dict[tuple[str, str], Turn] = {}

  def start(self, chat: str, messages: list[dict]) -> Turn:
    """The turn of one chat request. A repeat of an answered message counts 1 more retry."""
    return self.start_digest(chat, digest(messages))

  def start_digest(self, chat: str, value: str) -> Turn:
    """The turn of one request with a ready digest."""
    now = self.clock()
    self.turns = {k: t for k, t in self.turns.items() if t.used >= now - self.idle}
    found = self.turns.get((chat, value))
    if found is not None and found.model is not None:
      found.count, found.used = found.count + 1, now
      return found
    self.turns[(chat, value)] = Turn(now)
    return self.turns[(chat, value)]

  def clear(self) -> None:
    self.turns.clear()


def next_tier(turn: Turn) -> int:
  """The tier above the last answer, and never above tier A."""
  return min((turn.tier or 1) + 1, TOP_TIER)


def fresh_models(turn: Turn, group: list[str]) -> list[str]:
  """The models of the first tier that did not answer this message. After all of them, the list starts again."""
  left = [m for m in group if m not in turn.answered]
  if left:
    return left
  turn.answered.clear()
  others = [m for m in group if m != turn.model]
  return others or group


def record(turn: Turn, tier: int | None, model: str) -> None:
  """Keep the tier and the model that answered. A new tier starts a new list of answered models."""
  if tier != turn.tier:
    turn.answered = set()
  turn.tier, turn.model = tier, model
  turn.answered.add(model)
