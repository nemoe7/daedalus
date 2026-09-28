"""Runnable check that the dashboard keeps the asked and the sent reasoning effort. Run: python tests/dashboard/test_effort_history.py"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import httpx

from daedalus import config, dashboard, providers, store
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
os.environ["DAEDALUS_MASTER_KEY"] = MASTER

ANSWER = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
ROWS = [
  {"id": "mistral/thinker", "mode": "chat", "supports_reasoning": 1},
  {"id": "mistral/fails", "mode": "chat", "supports_reasoning": 1},
  {"id": "groq/plain", "mode": "chat", "supports_reasoning": 0},
]


def upstream(request: httpx.Request) -> httpx.Response:
  if json.loads(request.content)["model"] == "fails":
    return httpx.Response(400, json={"error": {"message": "no"}})
  return httpx.Response(200, json=ANSWER)


async def last_row(client: httpx.AsyncClient, model: str, effort: str | None) -> dict:
  """Send 1 request, and return the dashboard row that it made."""
  body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
  if effort is not None:
    body["reasoning_effort"] = effort
  await client.post("/v1/chat/completions", json=body)
  return dashboard.HISTORY.latest(1)[0]


async def main() -> None:
  config.set_config(
    {
      "mistral": {"api_base": "https://mistral.test/v1", "api_key": "k"},
      "groq": {"api_base": "https://groq.test/v1", "api_key": "k"},
    }
  )
  saved = store.MODELS_DB
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    store.write_store(ROWS)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
      set_client(outer)
      app = httpx.ASGITransport(app=api.app)
      async with httpx.AsyncClient(
        transport=app, base_url="http://t", headers=AUTH
      ) as client:
        row = await last_row(client, "mistral/thinker", "low")
        assert row["effort"] == "low", row
        assert row["attempts"][0]["effort"] == "high", row["attempts"]
        row = await last_row(client, "mistral/fails", "none")
        assert row["attempts"][0]["result"] == "HTTP 400", row["attempts"]
        assert row["attempts"][0]["effort"] == "none", "a failed attempt keeps it"
        row = await last_row(client, "groq/plain", "medium")
        assert row["effort"] == "medium", row
        assert row["attempts"][0]["effort"] is None, "a dropped effort is null"
        row = await last_row(client, "mistral/thinker", None)
        assert row["effort"] is None, row
        assert "effort" not in row["attempts"][0], row["attempts"]
      set_client(None)
    store.MODELS_DB = saved
  body = {"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "low"}
  gemini = {"gemini": {"api_key": "k"}}
  provider, _, payload, _ = providers.prepare("gemini/gemini-3.7-flash", body, gemini)
  assert provider.effort(payload) == "thinkingLevel=low", payload
  provider, _, payload, _ = providers.prepare("gemini/gemini-2.5-flash", body, gemini)
  assert provider.effort(payload) == "thinkingBudget=1024", payload
  print("ok: asked and sent reasoning effort in the dashboard rows")


if __name__ == "__main__":
  asyncio.run(main())
