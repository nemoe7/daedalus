import asyncio
import os
import sqlite3
import time

from daedalus import store
from daedalus.catalog import schedule
from daedalus.config import settings


def local(text: str) -> float:
  return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))


def test_times() -> None:
  now = local("2026-09-27 15:30")
  assert schedule.last(now) == local("2026-09-27 12:00")
  assert schedule.upcoming(now) == local("2026-09-27 18:00")
  assert schedule.last(local("2026-09-27 03:00")) == local("2026-09-27 00:00")
  assert schedule.last(local("2026-09-27 06:00")) == local("2026-09-27 06:00"), (
    "at the time"
  )
  assert schedule.upcoming(local("2026-09-27 18:00")) == local("2026-09-28 00:00")
  schedule.EVERY = 24.0
  assert schedule.last(local("2026-09-27 05:00")) == local("2026-09-26 06:00"), (
    "the day before"
  )
  assert schedule.upcoming(local("2026-09-27 05:00")) == local("2026-09-27 06:00")
  schedule.EVERY, schedule.ANCHOR = 8.0, 2.5
  assert schedule.last(local("2026-09-27 12:00")) == local("2026-09-27 10:30")
  schedule.EVERY = 0.0
  assert schedule.last(now) is None and schedule.upcoming(now) is None, "0 stops it"
  schedule.EVERY, schedule.ANCHOR = 6.0, 6.0


def test_timezone() -> None:
  """The TZ env var sets the clock."""
  if not hasattr(time, "tzset"):
    return
  original = os.environ.get("TZ")
  try:
    os.environ["TZ"] = "Asia/Manila"
    time.tzset()
    manila = schedule.last(time.time())
    os.environ["TZ"] = "UTC"
    time.tzset()
    utc = schedule.last(time.time())
    assert manila is not None and utc is not None
    assert time.gmtime(utc).tm_hour % 6 == 0, "06:00 UTC"
    assert time.gmtime(manila).tm_hour % 6 == 4, "06:00 in Manila is 22:00 UTC"
  finally:
    if original is None:
      os.environ.pop("TZ", None)
    else:
      os.environ["TZ"] = original
    time.tzset()


def test_due() -> None:
  assert store.built() is None and schedule.due(time.time()), "no store"
  store.write_store([])
  assert schedule.due(time.time()) is False, "a new store"
  with sqlite3.connect(store.MODELS_DB) as database:
    database.execute("UPDATE catalog SET built = ?", (schedule.last(time.time()) - 1,))
  database.close()
  assert schedule.due(time.time()), "a store older than the last rebuild time"
  store.write_store([])
  with sqlite3.connect(store.MODELS_DB) as database:
    database.execute("DROP TABLE catalog")
  database.close()
  assert store.built() is None and schedule.due(time.time()), "a store without the time"


async def test_run() -> None:
  store.MODELS_DB.unlink(missing_ok=True)
  calls: list[str] = []

  def rebuild() -> None:
    calls.append("rebuild")
    if len(calls) == 1:
      raise OSError("disk full")
    store.write_store([])

  schedule.RETRY_SECONDS = 0.01
  task = asyncio.create_task(schedule.run(rebuild))
  await asyncio.sleep(0.3)
  task.cancel()
  assert calls == ["rebuild", "rebuild"], "a retry after a failure, then no rebuild"
  assert schedule.due(time.time()) is False


async def test_manual() -> None:
  release, calls = asyncio.Event(), []

  def refresh() -> None:
    calls.append(1)
    asyncio.run_coroutine_threadsafe(release.wait(), loop).result()

  loop = asyncio.get_running_loop()
  try:
    assert schedule.start(refresh), "a manual rebuild starts"
    assert schedule.BUSY and not schedule.start(refresh), "only 1 rebuild at a time"
    await asyncio.sleep(0.05)
  finally:
    release.set()
  await asyncio.gather(*schedule.TASKS)
  assert calls == [1] and not schedule.BUSY, "the busy mark clears at the end"

  def broken() -> None:
    raise RuntimeError("down")

  assert schedule.start(broken)
  await asyncio.gather(*schedule.TASKS)
  assert not schedule.BUSY, "a failure clears the busy mark too"


def test_settings() -> None:
  values = settings.parse("catalog:\n  every: 12\n  anchor: 0\n")
  assert values["catalog"] == {"every": 12.0, "anchor": 0.0}, values
  assert settings.parse("")["catalog"] == {"every": 6.0, "anchor": 6.0}, "the defaults"
  assert settings.parse("catalog:\n  every: 0\n")["catalog"]["every"] == 0.0, (
    "0 stops it"
  )
  for text, message in (
    ("catalog:\n  every: 5\n", "divide 24"),
    ("catalog:\n  every: -1\n", "0 or more"),
    ("catalog:\n  anchor: 24\n", "from 0 to 23"),
    ("catalog:\n  anchor: 6.5\n", "whole hour"),
    ("catalog:\n  anchor: true\n", "0 or more"),
  ):
    try:
      settings.parse(text)
    except settings.SettingsError as exc:
      assert message in str(exc), exc
    else:
      raise AssertionError(text)
