"""A tool, thinking or answer loop counts as a fault, and the request goes to the next model."""

import json

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.routing import loops
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
NAMES = ("a", "b")
LINE = "Let me check the file again to be sure. "
SEEN: list[tuple[str, dict]] = []
PLAN: dict[str, tuple[str, object]] = {}


def data(chunk: dict) -> bytes:
  return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def delta(identifier: str, **fields: object) -> bytes:
  return data({"id": identifier, "choices": [{"index": 0, "delta": fields}]})


def answer(identifier: str, **message: object) -> dict:
  return {
    "id": identifier,
    "object": "chat.completion",
    "choices": [
      {"index": 0, "message": {"role": "assistant", **message}, "finish_reason": "stop"}
    ],
  }


def upstream(request: httpx.Request) -> httpx.Response:
  host = request.url.host
  SEEN.append((host, json.loads(request.content)))
  kind, value = PLAN[host.split(".")[0]]
  if kind == "json":
    return httpx.Response(200, json=value)
  return httpx.Response(200, content=b"".join(value))


def call(identifier: str) -> dict:
  return {
    "id": identifier,
    "type": "function",
    "function": {"name": "run", "arguments": '{"code": "print(1)"}'},
  }


def rounds(count: int) -> list[dict]:
  messages: list[dict] = [{"role": "user", "content": "go"}]
  for index in range(1, count + 1):
    item = call(f"call_{index}")
    messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
    messages.append({"role": "tool", "tool_call_id": item["id"], "content": "ok"})
  return messages


@pytest.fixture
async def client():
  config.set_config(
    {
      name: {
        "api_base": f"https://{name}.test/v1",
        "api_key": "k",
        "tier": {"TIER-D": ["x"]},
      }
      for name in NAMES
    }
  )
  store.write_store([{"id": f"{name}/x"} for name in NAMES])
  api.PENALTIES.clear()
  api.PENALTIES.pick = lambda: 0.0
  loops.clear()
  SEEN.clear()
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport, base_url="http://t", headers=AUTH
    ) as inner:
      yield inner
  set_client(None)
  config.set_config(None)


def hosts() -> list[str]:
  return [host for host, _ in SEEN]


def weight(model: str) -> float:
  return api.PENALTIES.weights([model])[model]


def results() -> list[tuple[str, str]]:
  return [(s["model"], s["result"]) for s in dashboard.HISTORY.latest(1)[0]["attempts"]]


async def test_tool_loop(client: httpx.AsyncClient) -> None:
  PLAN.update(
    a=("json", answer("a1", content="a")), b=("json", answer("b1", content="b"))
  )
  loops.save([f"call_{i}" for i in (1, 2, 3)], "a/x")
  body = {"model": "daedalus/moros", "messages": rounds(2)}
  response = await client.post("/v1/chat/completions", json=body)
  assert response.status_code == 200 and hosts() == ["a.test"], "2 calls are no loop"
  assert weight("a/x") == 1.0

  SEEN.clear()
  body["messages"] = rounds(3)
  response = await client.post("/v1/chat/completions", json=body)
  assert response.status_code == 200, response.text
  assert response.json()["choices"][0]["message"]["content"] == "b"
  assert hosts() == ["b.test"], "the model with the loop goes last"
  assert weight("a/x") == pytest.approx(0.5, abs=1e-3), "a fault of the call maker"
  assert dashboard.HISTORY.latest(1)[0]["loop"] == "3"


async def test_tool_loop_unknown_call(client: httpx.AsyncClient) -> None:
  PLAN.update(a=("json", answer("a1", content="a")))
  body = {"model": "daedalus/moros", "messages": rounds(3)}
  response = await client.post("/v1/chat/completions", json=body)
  assert response.status_code == 200 and hosts() == ["a.test"], SEEN
  assert dashboard.HISTORY.latest(1)[0]["loop"] == "3", "the log still shows it"
  assert weight("a/x") == 1.0 and weight("b/x") == 1.0


async def test_calls_are_kept(client: httpx.AsyncClient) -> None:
  tool = answer("a1", content=None, tool_calls=[call("kept_1")])
  PLAN.update(a=("json", tool))
  body = {"model": "daedalus/moros", "messages": [{"role": "user", "content": "go"}]}
  assert (await client.post("/v1/chat/completions", json=body)).status_code == 200
  assert loops.maker("kept_1") == "a/x"

  item = {"index": 0, **call("kept_2")}
  PLAN.update(a=("sse", [delta("a2", tool_calls=[item]), b"data: [DONE]\n\n"]))
  response = await client.post("/v1/chat/completions", json=body | {"stream": True})
  assert response.status_code == 200 and "[DONE]" in response.text
  assert loops.maker("kept_2") == "a/x"


async def test_answer_loop_without_stream(client: httpx.AsyncClient) -> None:
  PLAN.update(
    a=("json", answer("a1", content="Start. " + LINE * 6)),
    b=("json", answer("b1", content="Done.")),
  )
  body = {"model": "daedalus/moros", "messages": [{"role": "user", "content": "go"}]}
  response = await client.post("/v1/chat/completions", json=body)
  assert response.json()["choices"][0]["message"]["content"] == "Done."
  assert hosts() == ["a.test", "b.test"], SEEN
  assert results() == [("a/x", "loop"), ("b/x", "answered")], results()
  assert weight("a/x") == pytest.approx(0.5, abs=1e-3)


def output(raw: str) -> tuple[str, str]:
  chunks = [
    json.loads(line[6:])
    for line in raw.splitlines()
    if line.startswith("data: ") and line != "data: [DONE]"
  ]
  deltas = [c["choices"][0]["delta"] for c in chunks if c.get("choices")]
  thinking = "".join(d.get("reasoning_content", "") for d in deltas)
  return thinking, "".join(d.get("content") or "" for d in deltas)


async def test_answer_loop_in_stream(client: httpx.AsyncClient) -> None:
  pieces = [delta("a1", content="Start. ")] + [delta("a1", content=LINE)] * 6
  PLAN.update(
    a=("sse", pieces),
    b=("sse", [delta("b1", content="Done."), b"data: [DONE]\n\n"]),
  )
  body = {
    "model": "daedalus/moros",
    "stream": True,
    "messages": [{"role": "user", "content": "go"}],
  }
  response = await client.post("/v1/chat/completions", json=body)
  assert response.text.rstrip().endswith("data: [DONE]"), response.text
  assert output(response.text)[1] == "Start. " + LINE * 3 + "Done."
  prefix = SEEN[1][1]["messages"][-1]
  assert prefix == {"role": "assistant", "content": "Start. " + LINE}, prefix
  assert results() == [("a/x", "answered"), ("a/x", "loop"), ("b/x", "answered")]
  assert weight("a/x") == pytest.approx(0.5, abs=1e-3)


async def test_thinking_loop_in_stream(client: httpx.AsyncClient) -> None:
  pieces = [delta("a1", reasoning_content=LINE)] * 5
  PLAN.update(
    a=("sse", pieces),
    b=("sse", [delta("b1", content="Answer."), b"data: [DONE]\n\n"]),
  )
  messages = [{"role": "user", "content": "go"}]
  body = {"model": "daedalus/moros", "stream": True, "messages": messages}
  response = await client.post("/v1/chat/completions", json=body)
  thinking, text = output(response.text)
  assert thinking == LINE * 3 and text == "Answer.", (thinking, text)
  assert SEEN[1][1]["messages"] == messages, "no answer text, so no prefix"
  assert weight("a/x") == pytest.approx(0.5, abs=1e-3)
