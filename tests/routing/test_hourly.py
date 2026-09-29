"""Tests of the counted `hourly_requests` of a provider."""

from daedalus.routing import pacing
from daedalus.routing.limits import Limits


def paced_kilo(clock: dict[str, float], cap: object = 3) -> pacing.Pacing:
  """A pacing with a kilo cap and a groq block without one."""
  paced = pacing.Pacing(lambda: clock["now"])
  paced.config = lambda: {"kilo": {"hourly_requests": cap}, "groq": {"rpm": 30}}
  return paced


def test_hour() -> None:
  clock = {"now": 1000.0}
  paced = paced_kilo(clock)
  assert paced.caps() == {"kilo": 3}
  for model in ("kilo/a", "kilo/b", "kilo/a@kilo-code"):
    assert not paced.full("kilo/b", {})
    paced.record(model)
    clock["now"] += 10
  assert paced.full("kilo/b", {}), "3 requests of any kilo model or lane reach the cap"
  assert paced.full("kilo/c@owui", {}), "the cap is for the whole provider"
  assert not paced.full("groq/x", {}), "groq has no cap"
  assert paced.wait(["kilo/b"]) == 3600.0 - 30, "the first request leaves the hour"
  clock["now"] = 1000.0 + 3600
  assert not paced.full("kilo/b", {}), "the first request left the hour"
  paced.record("groq/x")
  assert "groq" not in paced.hour, "no hour counts without a cap"


def test_rate_limit() -> None:
  clock = {"now": 1000.0}
  paced = paced_kilo(clock, 200)
  paced.record("kilo/a")
  clock["now"] += 100
  paced.used_up("kilo/a@owui")
  assert paced.full("kilo/b", {}), "a 429 uses up the rest of the hour"
  assert paced.wait(["kilo/b"]) == 3500.0
  clock["now"] = 1000.0 + 3600
  assert not paced.full("kilo/b", {}), "the hold ends with the hour"
  paced.used_up("groq/x")
  assert "groq" not in paced.spent, "no hold without a cap"
  empty = paced_kilo(clock)
  empty.used_up("kilo/a")
  assert empty.wait(["kilo/a"]) == 3600.0, "with no count, the hold is 1 hour"


def test_off_and_bad_caps() -> None:
  clock = {"now": 1000.0}
  for cap in (0, -5, True, "200", None):
    assert paced_kilo(clock, cap).caps() == {}, f"{cap!r} is not a cap"
  paced = paced_kilo(clock, 1)
  paced.record("kilo/a")
  paced.enabled = False
  assert not paced.full("kilo/a", {}), "pacing is off"
  file_block = pacing.Pacing(lambda: clock["now"])
  file_block.config = lambda: {"kilo": {"_file": {"hourly_requests": 5}}}
  assert file_block.caps() == {"kilo": 5}, "kilo.yml can set it"


def test_limits_row() -> None:
  clock = {"now": 1000.0}
  paced = paced_kilo(clock)
  paced.record("kilo/a")
  clock["now"] += 600
  seen = Limits(clock=lambda: 50_000.0)
  seen.counted = paced.hour_rows
  view = seen.view()
  assert view["lanes"] == [], "the counted limit is not a header row"
  assert view["providers"] == [
    {"name": "kilo", "items": [["Requests left this hour, counted", "2 of 3", 2 / 3]]}
  ], "without a balance, the counter makes the kilo card"
  seen.balances = {"kilo": [("Balance", "$1.00", None)]}
  items = seen.view()["providers"][0]["items"]
  assert items[0][0] == "Balance" and items[1][1] == "2 of 3", "below the balance"
  assert paced.hour_rows(50_000.0)[0]["reset"] == 50_000.0 + 3000
  paced.used_up("kilo/a")
  assert paced.hour_rows(50_000.0)[0]["remaining"] == 0, "a 429 leaves 0"
