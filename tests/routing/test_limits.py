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
    variables = json.loads(request.content)["variables"]
    assert variables["account"] == "acc", variables
    groups = [{"sum": {"totalNeurons": 1234.4}}]
    data = {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": groups}]}}
    return httpx.Response(200, json={"data": data, "errors": None})
  return httpx.Response(404)


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
      ["Credit left", "$7.50 of $10.00"],
      ["Used today", "$0.25"],
      ["Free requests today", "0 of 50 left"],
    ],
    "kilo": [["Balance", "$1.20"]],
    "cloudflare": [["Neurons today", "1,234 of 10,000"]],
  }, shown
  ends = cooldowns.ends()
  midnight = datetime(2026, 9, 30, tzinfo=UTC).timestamp()
  assert ends == {"openrouter/a:free": midnight}, (
    "0 free requests left cools the free models"
  )
  assert found.view()["checked"] == NOW
