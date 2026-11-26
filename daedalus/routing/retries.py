"""Find a try again, and keep the models that answered each message or content."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from daedalus.routing.router import model_key
from daedalus.store import keys

TOP_TIER = 4


@dataclass
class Turn:
  """One chat message: the tier and model of the last answer, and retry count."""

  used: float
  tier: int | None = None
  model: str | None = None
  count: int = 0
  answered: set[str] = field(default_factory=set)


def digest(messages: list[dict]) -> str:
  """Hash of messages without system messages, which can hold time."""
  # A regenerate repeats a message: trailing plain answers drop from the digest,
  # so a regenerate hashes like the request it answers.
  kept = [m for m in messages if m.get("role") != "system"]
  while kept and kept[-1].get("role") == "assistant" and not kept[-1].get("tool_calls"):
    kept.pop()
  return keys.digest(json.dumps(kept, sort_keys=True, ensure_ascii=False))


class Retries:
  """The turns of each key. An idle turn expires."""

  def __init__(
    self, idle: float = 3600, clock: Callable[[], float] = time.time
  ) -> None:
    self.idle = idle
    self.clock = clock
    self.turns: dict[str, Turn] = {}

  def turn(self, key: str) -> Turn:
    """The turn of one key. A repeat of an answered key counts 1 more retry."""
    now = self.clock()
    self.turns = {k: t for k, t in self.turns.items() if t.used >= now - self.idle}
    found = self.turns.get(key)
    if found is not None and found.model is not None:
      found.count, found.used = found.count + 1, now
      return found
    self.turns[key] = Turn(now)
    return self.turns[key]

  def start(self, chat: str, messages: list[dict]) -> Turn:
    """The turn of one chat request. A repeat of an answered message counts 1 more retry."""
    return self.start_digest(chat, digest(messages))

  def start_digest(self, chat: str, value: str) -> Turn:
    """The turn of one request with a chat id and a ready digest."""
    return self.turn(f"{chat}\x00{value}")

  def clear(self) -> None:
    self.turns.clear()


def next_tier(turn: Turn) -> int:
  """The tier above the last answer, and never above tier A."""
  return min((turn.tier or 1) + 1, TOP_TIER)


def fresh_models(turn: Turn, group: list[str]) -> list[str]:
  """First-tier models that did not answer, excluding their copies on other providers. A full list restarts."""
  banned = {model_key(m) for m in turn.answered}
  left = [m for m in group if m not in turn.answered and model_key(m) not in banned]
  if left:
    return left
  turn.answered.clear()
  others = [m for m in group if m != turn.model]
  return others or group


def fresh_groups(turn: Turn, groups: list[list[str]]) -> list[list[str]]:
  """Drop answered models and their copies on other providers from every tier."""
  if not groups:
    return groups
  groups[0] = fresh_models(turn, groups[0])
  banned = {model_key(m) for m in turn.answered}
  if banned:
    for index in range(1, len(groups)):
      groups[index] = [m for m in groups[index] if model_key(m) not in banned]
  return groups


def record(turn: Turn, tier: int | None, model: str) -> None:
  """Keep the tier and the model that answered. A new tier starts a new list of answered models."""
  if tier != turn.tier:
    turn.answered = set()
  turn.tier, turn.model = tier, model
  turn.answered.add(model)
