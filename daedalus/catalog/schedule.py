"""Catalog rebuilds at fixed local times, with a rebuild at once for a missed time."""

import asyncio
import logging
import time
from collections.abc import Callable

from daedalus import store

logger = logging.getLogger("daedalus")

# Hours between rebuilds, and the local hour that the rebuild times start from. 0 stops it.
EVERY = 6.0
ANCHOR = 6.0
# The longest sleep, so that a settings change or a clock jump applies soon.
TICK_SECONDS = 60.0
RETRY_SECONDS = 300.0
# True while a rebuild runs, so that a manual rebuild and the schedule do not overlap.
BUSY = False
# The manual rebuild tasks, so that the event loop keeps a reference to each.
TASKS: set[asyncio.Task[None]] = set()


def times(day: time.struct_time, offset: int) -> list[float]:
  """The rebuild times of the local day `offset` days from `day`."""
  first = ANCHOR % EVERY
  found = []
  hour = first
  while hour < 24:
    minutes = round(hour * 60)
    local = (day.tm_year, day.tm_mon, day.tm_mday + offset, 0, minutes, 0, 0, 0, -1)
    found.append(time.mktime(local))
    hour += EVERY
  return found


def last(now: float) -> float | None:
  """The latest rebuild time at or before `now`. None when the schedule is off."""
  if EVERY <= 0:
    return None
  day = time.localtime(now)
  return max(when for offset in (-1, 0) for when in times(day, offset) if when <= now)


def upcoming(now: float) -> float | None:
  """The first rebuild time after `now`. None when the schedule is off."""
  if EVERY <= 0:
    return None
  day = time.localtime(now)
  return min(when for offset in (0, 1) for when in times(day, offset) if when > now)


def due(now: float) -> bool:
  """Tell if the store is older than the latest rebuild time."""
  mark = last(now)
  if mark is None:
    return False
  built = store.built()
  return built is None or built < mark


async def rebuild(refresh: Callable[[], object], reason: str) -> None:
  """Run 1 rebuild in a worker thread, with the busy mark set."""
  global BUSY
  BUSY = True
  logger.info("catalog rebuild: %s", reason)
  try:
    await asyncio.to_thread(refresh)
  finally:
    BUSY = False


def start(refresh: Callable[[], object]) -> bool:
  """Start a manual rebuild. False when a rebuild runs now."""
  global BUSY
  if BUSY:
    return False
  # The mark goes on before the task runs, so that a second click finds it.
  BUSY = True

  async def manual() -> None:
    try:
      await rebuild(refresh, "manual")
    except Exception:
      logger.exception("catalog rebuild failed")

  task = asyncio.create_task(manual())
  TASKS.add(task)
  task.add_done_callback(TASKS.discard)
  return True


async def run(refresh: Callable[[], object]) -> None:
  """Rebuild the catalog when it is due, until the task stops."""
  while True:
    now = time.time()
    if BUSY or not due(now):
      following = upcoming(now)
      wait = TICK_SECONDS if following is None else following - now
      await asyncio.sleep(max(1.0, min(wait, TICK_SECONDS)))
      continue
    try:
      await rebuild(refresh, "scheduled")
    except Exception:
      logger.exception("catalog rebuild failed, next try in %ds", RETRY_SECONDS)
      await asyncio.sleep(RETRY_SECONDS)
