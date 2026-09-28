"""Tests of the rate-limit cooldowns."""

import json
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.routing import cooldowns
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"
# 2026-09-28 04:00 UTC, which is 2026-09-27 21:00 in Los Angeles (PDT).
NOW = datetime(2026, 9, 28, 4, 0, tzinfo=UTC).timestamp()
GEMINI_DAILY = {
  "error": {
    "code": 429,
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [
          {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
        ],
      },
      {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "1s"},
    ],
  }
}
GEMINI_MINUTE = {
  "error": {
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}],
      },
      {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "38s"},
    ]
  }
}
CLOUDFLARE_DAILY = {"errors": [{"message": "daily free allocation", "code": 4006}]}


def test_rules() -> None:
  assert cooldowns.duration("38s") == 38 and cooldowns.duration("250ms") == 0.25
  assert cooldowns.duration("2m59.5s") == 179.5 and cooldowns.duration("1h") == 3600
  assert cooldowns.duration("soon") is None and cooldowns.duration("5sx") is None
  pacific = datetime(2026, 9, 28, 7, 0, tzinfo=UTC).timestamp()
  assert cooldowns.daily_end("gemini/g", GEMINI_DAILY, NOW) == ("gemini/g", pacific)
  utc = datetime(2026, 9, 29, 0, 0, tzinfo=UTC).timestamp()
  assert cooldowns.daily_end("cloudflare/@cf/m", CLOUDFLARE_DAILY, NOW) == (
    "cloudflare/*",
    utc,
  ), "a Cloudflare daily limit covers all Cloudflare models"
  assert cooldowns.daily_end("gemini/g", GEMINI_MINUTE, NOW) is None
  assert cooldowns.daily_end("groq/g", CLOUDFLARE_DAILY, NOW) is None
  assert cooldowns.reset_seconds({"retry-after": "7"}, {}, NOW) == 7
  assert cooldowns.reset_seconds({"x-ratelimit-reset": str(NOW + 90)}, {}, NOW) == 90
  assert cooldowns.reset_seconds({"x-ratelimit-reset": "2m59.5s"}, {}, NOW) == 179.5
  date = format_datetime(datetime.fromtimestamp(NOW + 120, UTC), usegmt=True)
  assert cooldowns.reset_seconds({"retry-after": date}, {}, NOW) == 120
  assert cooldowns.reset_seconds({}, GEMINI_MINUTE, NOW) == 38
  assert cooldowns.reset_seconds({"retry-after": "0"}, {}, NOW) is None


def test_store() -> None:
  clock = {"now": NOW}
  cool = cooldowns.Cooldowns(lambda: store.MODELS_DB, lambda: clock["now"])
  daily = json.dumps(GEMINI_DAILY).encode()
  found = cool.start("gemini/g", {}, daily)
  assert found == {"seconds": 3 * 3600, "reason": "daily"}, (
    "rule 1 before the 1 s delay"
  )
  spans = [cool.start("groq/g", {}, b"")["seconds"] for _ in range(10)]
  assert spans[:4] == [60, 120, 240, 480] and spans[-1] == 21600, spans
  cool.succeeded("groq/g")
  assert cool.start("groq/g", {}, b"")["seconds"] == 60, "a success sets it back"
  assert cool.start("groq/g", {"retry-after": "5"}, b"") == {
    "seconds": 5,
    "reason": "reset",
  }
  assert cool.start("groq/g", {}, b"")["seconds"] == 120, "a reset keeps the backoff"
  cool.start("cloudflare/@cf/a", {}, json.dumps(CLOUDFLARE_DAILY).encode())
  ends = cool.ends()
  assert cool.until("cloudflare/@cf/b", ends) == ends["cloudflare/*"], ends
  clock["now"] = NOW + 4 * 3600
  assert "gemini/g" not in cool.ends(), "an ended cooldown leaves"
  cool.clear()


def answer(request: httpx.Request) -> httpx.Response:
  if request.url.host == "limited.test":
    return httpx.Response(429, json={"error": "slow"}, headers={"retry-after": "30"})
  message = {"role": "assistant", "content": "hi"}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def test_requests() -> None:
  config = {
    "first": {"api_key": "k", "api_base": "https://limited.test/v1"},
    "second": {"api_key": "k", "api_base": "https://answers.test/v1"},
  }
  sent: list[str] = []

  def recorded(request: httpx.Request) -> httpx.Response:
    sent.append(request.url.host)
    return answer(request)

  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config, key="": (
    ([[model]], None)
    if "/" in model and "daedalus" not in model
    else ([["first/a", "second/b"]], "daedalus/koinos")
  )
  pick, api.PENALTIES.pick = api.PENALTIES.pick, lambda: 0.0
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(recorded)))
  client = TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
  body = {"model": "daedalus/koinos", "messages": [{"role": "user", "content": "hi"}]}
  try:
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    assert sent == ["limited.test", "answers.test"], sent
    weight = api.PENALTIES.weights(["first/a"])["first/a"]
    assert round(weight, 3) == 0.75, "a 429 is not a fault"
    sent.clear()
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    assert sent == ["answers.test"], "a model in a cooldown leaves the chain"
    sent.clear()
    direct = {**body, "model": "first/a"}
    response = client.post("/v1/chat/completions", json=direct)
    assert response.status_code == 429 and sent == [], "no upstream request"
    assert response.json()["error"]["type"] == "rate_limit_exceeded", response.text
    assert 29 <= int(response.headers["retry-after"]) <= 30, response.headers
    api.PENALTIES.pin("", "daedalus/koinos", "second/b")
    api.COOLDOWNS.start("second/b", {"retry-after": "600"}, b"")
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 429 and sent == [], "each model is in a cooldown"
    assert 29 <= int(response.headers["retry-after"]) <= 30, "the first end"
  finally:
    api.get_config, api.chain = original
    api.PENALTIES.pick = pick
    upstream.set_client(None)
    api.COOLDOWNS.clear()


def test_pin() -> None:
  api.PENALTIES.pin("k", "daedalus/koinos", "second/b")
  api.COOLDOWNS.start("second/b", {"retry-after": "600"}, b"")
  pin = api.Tracker("k", "daedalus/koinos")
  pin.cooled(api.COOLDOWNS.ends())
  assert api.PENALTIES.pinned("k", "daedalus/koinos") is None, "the pin leaves"
  assert pin.dropped, "the next answer logs pin=moved"
  api.COOLDOWNS.clear()
