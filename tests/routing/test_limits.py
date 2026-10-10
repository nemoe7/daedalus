"""Tests of the rate-limit headers and the provider balances."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from daedalus import store
from daedalus.routing import limits
from daedalus.routing.cooldowns import Cooldowns

NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC).timestamp()
GROQ = {
  "x-ratelimit-limit-requests": "14400",
  "x-ratelimit-remaining-requests": "0",
  "x-ratelimit-reset-requests": "2m59.56s",
  "x-ratelimit-limit-tokens": "18000",
  "x-ratelimit-remaining-tokens": "0",
  "x-ratelimit-reset-tokens": "7.66s",
}
MISTRAL = {
  "x-ratelimit-limit-tokens-minute": "50000",
  "x-ratelimit-remaining-tokens-minute": "49000",
  "x-ratelimit-limit-tokens-month": "4000000",
  "x-ratelimit-remaining-tokens-month": "0",
  "x-ratelimit-limit-req-minute": "60",
}


@pytest.fixture
def cooldowns(tmp_path: Path) -> Cooldowns:
  return Cooldowns(lambda: tmp_path / "models.sqlite3", clock=lambda: NOW)


def test_rows() -> None:
  """Groq headers have no window in the name, but Mistral headers do. A group with no remaining count does not show."""
  rows = limits.header_rows("groq", GROQ, NOW)
  assert [(r["kind"], r["span"]) for r in rows] == [
    ("requests", "day"),
    ("tokens", "minute"),
  ]
  assert rows[0]["reset"] == pytest.approx(NOW + 179.56), rows[0]
  rows = limits.header_rows("mistral", MISTRAL, NOW)
  spans = [(r["kind"], r["span"], r["remaining"]) for r in rows]
  assert spans == [("tokens", "minute", 49000), ("tokens", "month", 0)], spans
  assert limits.header_rows("gemini", {"content-type": "x"}, NOW) == []


def test_cooldown(cooldowns: Cooldowns) -> None:
  """0 left of a day or a month starts a cooldown until the reset. 0 left of a minute does not."""
  found = limits.Limits(cooldowns, clock=lambda: NOW)
  found.observe("groq/llama", httpx.Headers(GROQ))
  found.observe("mistral/large#kilo", httpx.Headers(MISTRAL))
  ends = cooldowns.ends()
  assert ends["groq/llama"] == pytest.approx(NOW + 179.56), ends
  month = datetime(2026, 10, 1, tzinfo=UTC).timestamp()
  assert ends["mistral/large#kilo"] == month, ends
  found.observe(
    "groq/small", httpx.Headers({**GROQ, "x-ratelimit-remaining-requests": "5"})
  )
  assert "groq/small" not in cooldowns.ends(), "0 tokens of a minute is no cooldown"
  view = found.view()
  lane = next(item for item in view["lanes"] if item["model"] == "mistral/large")
  assert lane["client"] == "kilo" and len(lane["rows"]) == 2, lane


def answer(request: httpx.Request) -> httpx.Response:
  assert request.headers["authorization"] == "Bearer k", request.headers
  if request.url.host == "openrouter.ai":
    data = {
      "limit": 10,
      "limit_remaining": 7.5,
      "usage_daily": 0.25,
      "free_model_daily_requests": {"used": 50, "limit": 50, "remaining": 0},
    }
    return httpx.Response(200, json={"data": data})
  if request.url.path == "/api/profile/balance":
    return httpx.Response(200, json={"balance": 1.2})
  if request.url.path == "/account/balance":
    return httpx.Response(403, json={"error": "account:usage"})
  if request.url.path == "/client/v4/graphql":
    body = json.loads(request.content)
    assert "datetimeHour_geq: $start" in body["query"], body
    assert body["variables"]["account"] == "acc", body
    assert body["variables"]["start"].endswith("T00:00:00Z"), body
    groups = [{"sum": {"totalNeurons": 1000.4}}, {"sum": {"totalNeurons": 234}}]
    data = {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": groups}]}}
    return httpx.Response(200, json={"data": data, "errors": None})
  return httpx.Response(404)


def test_neurons() -> None:
  """An empty group list with no errors is 0 neurons. An error or no account shows nothing."""
  empty = {
    "data": {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": []}]}},
    "errors": None,
  }
  assert limits.neuron_items(empty) == [("Neurons today", "10K of 10K left", 1.0)]
  small = {
    "data": {
      "viewer": {
        "accounts": [{"aiInferenceAdaptiveGroups": [{"sum": {"totalNeurons": 0.8553}}]}]
      }
    }
  }
  assert limits.neuron_items(small) == [
    ("Neurons today", "9K of 10K left", pytest.approx(0.99991, abs=1e-5))
  ], "the number and the bar show the neurons that are left"
  refused = {"data": None, "errors": [{"message": "not authorized for that account"}]}
  assert limits.neuron_items(refused) == []
  assert limits.neuron_items({"data": {"viewer": {"accounts": []}}}) == []


async def test_check(cooldowns: Cooldowns) -> None:
  """Each keyed provider with a balance endpoint shows its values. A refused read or no key shows nothing."""
  config = {
    "openrouter": {"api_key": "k"},
    "kilo": {"api_key": "k"},
    "pollinations": {"api_key": "k"},
    "cloudflare": {
      "api_key": "k",
      "api_base": "https://api.cloudflare.com/client/v4/accounts/acc/ai/v1",
    },
    "groq": {"api_key": "k"},
    "mistral": {},
  }
  store.write_store([{"id": "openrouter/a:free"}, {"id": "openrouter/b"}])
  async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
    found = limits.Limits(cooldowns, lambda: config, lambda: client, clock=lambda: NOW)
    await found.check()
  shown = {item["name"]: item["items"] for item in found.view()["providers"]}
  assert shown == {
    "openrouter": [
      ["Credit left", "$7.50 of $10.00", 0.75],
      ["Used today", "$0.25", None],
      ["Free requests today", "0 of 50 left", 0.0],
    ],
    "kilo": [["Balance", "$1.20", None]],
    "cloudflare": [
      ["Neurons today", "8K of 10K left", pytest.approx(0.87657, abs=1e-5)]
    ],
  }, shown
  ends = cooldowns.ends()
  midnight = datetime(2026, 9, 30, tzinfo=UTC).timestamp()
  assert ends == {"openrouter/a:free": midnight}, (
    "0 free requests left cools the free models"
  )
  assert found.view()["checked"] == NOW


def test_bare_headers() -> None:
  """A header set with no window in the name counts requests for a day, the OpenRouter way."""
  bare = {
    "x-ratelimit-limit": "1000",
    "x-ratelimit-remaining": "0",
    "x-ratelimit-reset": str(NOW + 3600),
  }
  rows = limits.header_rows("kilo", bare, NOW)
  assert [(r["kind"], r["span"], r["limit"], r["remaining"]) for r in rows] == [
    ("requests", "day", 1000.0, 0.0)
  ]
  assert limits.cooling_end(rows[0], NOW) == NOW + 3600, (
    "0 left holds the lane to the reset"
  )


def test_floored() -> None:
  """Counts floor to K, M or B, and stay whole below 1,000."""
  values = [0, 999, 1234, 998_765_432, 1_000_000_000]
  assert [limits.floored(v) for v in values] == ["0", "999", "1K", "998M", "1B"]


def test_advertised_resets_notify_at_the_boundary_once(tmp_path: Path) -> None:
  """TPM, RPM, RPH and RPD wait for their advertised time, then notify once with the boundary time."""
  now = [NOW]
  boundary = NOW + 60
  headers = {}
  for kind, span in (
    ("tokens", "minute"),
    ("requests", "minute"),
    ("requests", "hour"),
    ("requests", "day"),
  ):
    name = f"{kind}-{span}"
    headers[f"x-ratelimit-limit-{name}"] = "100"
    headers[f"x-ratelimit-remaining-{name}"] = "50"
    headers[f"x-ratelimit-reset-{name}"] = str(boundary)
  found = limits.Limits(clock=lambda: now[0], path=lambda: tmp_path / "models.sqlite3")
  found.observe("mistral/model#owui", httpx.Headers(headers))
  assert found.resets() == [], "an advertised boundary does not notify early"
  now[0] = boundary - 0.001
  assert found.resets() == [], "the last instant before the boundary stays quiet"
  now[0] = boundary
  events = found.resets()
  assert {
    (event["kind"], event["span"], event["provider"], event["client"])
    for event in events
  } == {
    ("tokens", "minute", "mistral", "owui"),
    ("requests", "minute", "mistral", "owui"),
    ("requests", "hour", "mistral", "owui"),
    ("requests", "day", "mistral", "owui"),
  }
  assert all(event["at"] == boundary for event in events), events
  rows = found.view()["lanes"][0]["rows"]
  assert all(row["remaining"] == row["limit"] for row in rows), (
    "a reset restores each remembered allowance"
  )
  assert all(row["reset"] is None for row in rows), (
    "the elapsed boundary leaves each remembered row"
  )
  assert found.resets() == events, "reading the due boundary again adds no event"


def test_a_newer_advertised_boundary_replaces_the_pending_one(tmp_path: Path) -> None:
  """One scope keeps its newest provider boundary, even when another model reports it."""
  now = [NOW]
  path = tmp_path / "models.sqlite3"
  found = limits.Limits(clock=lambda: now[0], path=lambda: path)

  def headers(boundary: float) -> httpx.Headers:
    return httpx.Headers(
      {
        "x-ratelimit-limit-requests-minute": "100",
        "x-ratelimit-remaining-requests-minute": "50",
        "x-ratelimit-reset-requests-minute": str(boundary),
      }
    )

  first, second = NOW + 60, NOW + 120
  found.observe("groq/a#client", headers(first))
  now[0] += 10
  found.observe("groq/b#client", headers(second))
  now[0] = first
  assert found.resets() == [], "the replaced boundary no longer notifies"
  now[0] = second
  events = found.resets()
  assert len(events) == 1 and events[0]["at"] == second, events


def test_reset_boundaries_and_deduplication_survive_a_restart(tmp_path: Path) -> None:
  """A pending reset and its one emitted event persist through separate process starts."""
  now = [NOW]
  path = tmp_path / "models.sqlite3"
  boundary = NOW + 30
  headers = httpx.Headers(
    {
      "x-ratelimit-limit-tokens-minute": "1000",
      "x-ratelimit-remaining-tokens-minute": "500",
      "x-ratelimit-reset-tokens-minute": str(boundary),
    }
  )
  limits.Limits(clock=lambda: now[0], path=lambda: path).observe("groq/model", headers)
  restored = limits.Limits(clock=lambda: now[0], path=lambda: path)
  restored.restore()
  assert restored.resets() == []
  now[0] = boundary
  assert len(restored.resets()) == 1
  again = limits.Limits(clock=lambda: now[0], path=lambda: path)
  again.restore()
  events = again.resets()
  assert len(events) == 1 and events[0]["at"] == boundary, events
  assert again.resets() == events, "the emitted boundary stays deduplicated"


def test_openrouter_and_kilo_request_resets_notify(tmp_path: Path) -> None:
  """OpenRouter's bare request headers and Kilo's counted rolling hour both notify."""
  now = [NOW]
  path = tmp_path / "models.sqlite3"
  found = limits.Limits(clock=lambda: now[0], path=lambda: path)
  openrouter_reset, kilo_reset = NOW + 30, NOW + 60
  found.observe(
    "openrouter/free#owui",
    httpx.Headers(
      {
        "x-ratelimit-limit": "50",
        "x-ratelimit-remaining": "10",
        "x-ratelimit-reset": str(openrouter_reset),
      }
    ),
  )
  found.counted = lambda _now: [
    {
      "provider": "kilo",
      "kind": "requests",
      "span": "hour",
      "limit": 200,
      "remaining": 199,
      "reset": kilo_reset,
    }
  ]
  assert found.resets() == []
  now[0] = openrouter_reset
  events = found.resets()
  assert [(event["provider"], event["client"], event["span"]) for event in events] == [
    ("openrouter", "owui", "day")
  ]
  now[0] = kilo_reset
  events = found.resets()
  assert {(event["provider"], event["client"], event["span"]) for event in events} == {
    ("openrouter", "owui", "day"),
    ("kilo", None, "hour"),
  }
  assert found.resets() == events, "the rolling boundary emits only once"


async def test_cloudflare_neurons_reset_at_utc_midnight(tmp_path: Path) -> None:
  """A successful Neurons read schedules one notification for the next UTC midnight."""
  now = [NOW]
  config = {
    "cloudflare": {
      "api_key": "k",
      "api_base": "https://api.cloudflare.com/client/v4/accounts/acc/ai/v1",
    }
  }
  async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
    found = limits.Limits(
      config=lambda: config,
      client=lambda: client,
      clock=lambda: now[0],
      path=lambda: tmp_path / "models.sqlite3",
    )
    await found.check()
    midnight = datetime(2026, 9, 30, tzinfo=UTC).timestamp()
    assert found.resets() == []
    now[0] = midnight
    events = found.resets()
  assert events == [
    {
      "at": midnight,
      "provider": "cloudflare",
      "client": None,
      "kind": "neurons",
      "span": "day",
    }
  ]


def test_rows_survive_a_restart(tmp_path: Path) -> None:
  """The lane rows and the balances of the last run come back after a restart, and `clear` drops them."""
  path = tmp_path / "models.sqlite3"
  found = limits.Limits(clock=lambda: NOW, path=lambda: path)
  found.restore()
  assert not path.exists(), "a restore with no state file writes nothing"
  found.observe("groq/llama", httpx.Headers(GROQ))
  found.balances, found.checked = {"kilo": [("Balance", "$1.20", None)]}, NOW
  found.save()
  again = limits.Limits(clock=lambda: NOW, path=lambda: path)
  again.restore()
  assert [item["model"] for item in again.view()["lanes"]] == ["groq/llama"]
  assert again.view()["providers"] == [
    {"name": "kilo", "items": [["Balance", "$1.20", None]]}
  ]
  assert again.view()["checked"] == NOW
  again.clear()
  empty = limits.Limits(clock=lambda: NOW, path=lambda: path)
  empty.restore()
  assert empty.view() == {"checked": None, "providers": [], "lanes": []}


def test_a_bad_saved_view_is_dropped(tmp_path: Path) -> None:
  """A saved view with a bad lane, card or time keeps the parts that fit and drops the rest."""
  path = tmp_path / "models.sqlite3"
  found = limits.Limits(clock=lambda: NOW, path=lambda: path)
  payload = json.dumps(
    {
      "lanes": {
        "groq/llama": {"at": NOW, "rows": [{"kind": "requests"}, "junk"]},
        "junk": 3,
        "bad": {"at": "yesterday", "rows": []},
      },
      "balances": {"kilo": [["Balance", "$1.20", None], "junk"], "nope": 4},
      "checked": "a while ago",
    }
  )
  database = found.connect()
  with database:
    database.execute(
      "INSERT INTO limits (name, payload, updated) VALUES (?, ?, ?)",
      (limits.SAVED, payload, NOW),
    )
  database.close()
  found.restore()
  assert [item["model"] for item in found.view()["lanes"]] == ["groq/llama"]
  assert found.view()["lanes"][0]["rows"] == [{"kind": "requests"}]
  assert found.view()["providers"] == [
    {"name": "kilo", "items": [["Balance", "$1.20", None]]}
  ]
  assert found.view()["checked"] is None
  database = found.connect()
  with database:
    database.execute(
      "UPDATE limits SET payload = ? WHERE name = ?", ("{", limits.SAVED)
    )
  database.close()
  cut = limits.Limits(clock=lambda: NOW, path=lambda: path)
  cut.restore()
  assert cut.view() == {"checked": None, "providers": [], "lanes": []}


async def test_check_keeps_a_failed_card(cooldowns: Cooldowns) -> None:
  """A provider that refuses a read now keeps the card of the last good read."""
  config = {"kilo": {"api_key": "k"}}
  async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
    found = limits.Limits(cooldowns, lambda: config, lambda: client, clock=lambda: NOW)
    await found.check()
    assert [item["name"] for item in found.view()["providers"]] == ["kilo"]

    def refused(_request: httpx.Request) -> httpx.Response:
      return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refused)) as other:
      found.client = lambda: other
      await found.check()
  assert [item["name"] for item in found.view()["providers"]] == ["kilo"], (
    "the last good card stays"
  )
