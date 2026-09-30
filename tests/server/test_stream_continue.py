import json
import tempfile
from pathlib import Path

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}

TIER = {"TIER-D": ["x"]}
SEEN: list[tuple[str, dict]] = []
PLAN: dict[str, list] = {}


class Dropped(httpx.AsyncByteStream):
  def __init__(self, parts: list[bytes]) -> None:
    self.parts = parts

  async def __aiter__(self):
    for part in self.parts:
      yield part
    raise httpx.ReadError("connection dropped")


def data(chunk: dict) -> bytes:
  return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def text(identifier: str, content: str) -> bytes:
  return data(
    {"id": identifier, "choices": [{"index": 0, "delta": {"content": content}}]}
  )


def upstream(request: httpx.Request) -> httpx.Response:
  host = request.url.host
  SEEN.append((host, json.loads(request.content)))
  kind, parts = PLAN[host.split(".")[0]]
  if kind == "status":
    return httpx.Response(parts, json={"error": {"message": "no"}})
  if kind == "drop":
    return httpx.Response(200, stream=Dropped(parts))
  return httpx.Response(200, content=b"".join(parts))


def output(raw: str) -> list[str]:
  return [line[6:] for line in raw.splitlines() if line.startswith("data: ")]


async def send(client: httpx.AsyncClient) -> list[str]:
  api.PENALTIES.clear()
  api.PENALTIES.pick = lambda: 0.0
  body = {
    "model": "daedalus/auto",
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
  }
  response = await client.post("/v1/chat/completions", json=body)
  assert response.status_code == 200, response.text
  return output(response.text)


async def test_stream_continue(monkeypatch: pytest.MonkeyPatch) -> None:
  transitions: list[dict] = []
  live_update = dashboard.live_update

  def capture(request, **fields):
    if fields.get("transition"):
      transitions.append(fields["transition"])
    live_update(request, **fields)

  monkeypatch.setattr(dashboard, "live_update", capture)
  names = ("a", "b", "c")
  config.set_config(
    {
      name: {"api_base": f"https://{name}.test/v1", "api_key": "k", "tier": TIER}
      for name in names
    }
  )
  saved = store.MODELS_DB
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    store.write_store([{"id": f"{name}/x"} for name in names])
    transport = httpx.MockTransport(upstream)
    async with httpx.AsyncClient(transport=transport) as upstream_client:
      set_client(upstream_client)
      app = httpx.ASGITransport(app=api.app)
      async with httpx.AsyncClient(
        transport=app, base_url="http://t", headers=AUTH
      ) as client:
        PLAN.update(
          a=("drop", [text("a1", "Hel"), text("a1", "lo")]),
          b=("status", 429),
          c=("ok", [text("c1", " world"), b"data: [DONE]\n\n"]),
        )
        lines = await send(client)
        assert lines[-1] == "[DONE]" and lines.count("[DONE]") == 1, lines
        assert transitions == [
          {
            "from_pool": "moros",
            "from_model": "a/x",
            "to_pool": "moros",
            "to_model": "b/x",
            "reason": "err",
          },
          {
            "from_pool": "moros",
            "from_model": "b/x",
            "to_pool": "moros",
            "to_model": "c/x",
            "reason": "lmt",
          },
        ], transitions
        chunks = [json.loads(line) for line in lines[:-1]]
        joined = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        assert joined == "Hello world", joined
        assert {chunk["id"] for chunk in chunks} == {"a1"}, chunks
        assert [host for host, _ in SEEN] == ["a.test", "b.test", "c.test"], SEEN
        prefix = {"role": "assistant", "content": "Hello"}
        assert SEEN[1][1]["messages"][-1] == prefix, SEEN[1]
        assert SEEN[2][1]["messages"] == [{"role": "user", "content": "hi"}, prefix]
        history = dashboard.HISTORY.latest(1)[0]
        assert history["transition"] == transitions[-1], history
        steps = [(s["model"], s["result"]) for s in history["attempts"]]
        assert steps == [
          ("a/x", "answered"),
          ("a/x", "stream failed"),
          ("b/x", "HTTP 429"),
          ("c/x", "answered"),
        ], steps
        attempts = dashboard.HISTORY.latest(1)[0]["attempts"]
        assert attempts[1]["weight_change"] == {"from": 1.0, "to": 0.5}, attempts
        assert attempts[2]["weight_change"] == {"from": 1.0, "to": 0.75}, attempts
        assert (
          "weight_change" not in attempts[0] and "weight_change" not in attempts[3]
        ), attempts
        cooled = attempts[2]["cooldown"]
        assert cooled == {"seconds": 60.0, "reason": "backoff"}, cooled
        assert "b/x" in api.COOLDOWNS.ends(), (
          "a 429 in a continuation starts a cooldown"
        )
        api.COOLDOWNS.clear()

        SEEN.clear()
        call = {"index": 0, "id": "call_1", "function": {"name": "f", "arguments": "{"}}
        tool = data(
          {"id": "a2", "choices": [{"index": 0, "delta": {"tool_calls": [call]}}]}
        )
        PLAN["a"] = ("drop", [tool])
        lines = await send(client)
        assert "[DONE]" not in lines, lines
        assert json.loads(lines[-1])["error"]["type"] == "upstream_error", lines
        assert [host for host, _ in SEEN] == ["a.test"], SEEN

        SEEN.clear()
        PLAN.update(
          a=("ok", [text("a3", "partial"), data({"error": {"message": "late"}})]),
          b=("drop", [text("b3", " more")]),
          c=("status", 500),
        )
        lines = await send(client)
        assert "[DONE]" not in lines, lines
        assert json.loads(lines[-1])["error"]["type"] == "upstream_error", lines
        assert SEEN[2][1]["messages"][-1]["content"] == "partial more", SEEN[2]

        SEEN.clear()
        PLAN.update(
          a=("ok", [text("a4", "cut")]),
          b=("ok", [text("b4", " off"), b"data: [DONE]\n\n"]),
        )
        lines = await send(client)
        assert lines[-1] == "[DONE]", lines
        assert [host for host, _ in SEEN] == ["a.test", "b.test"], SEEN
  store.MODELS_DB = saved
  set_client(None)
  config.set_config(None)
