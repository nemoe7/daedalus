import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from daedalus import api, catalog, config

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
  body = {
    "model": "daedalus/moros",
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
  }
  response = await client.post("/v1/chat/completions", json=body)
  assert response.status_code == 200, response.text
  return output(response.text)


async def main() -> None:
  names = ("a", "b", "c")
  config.set_config(
    {
      name: {"api_base": f"https://{name}.test/v1", "api_key": "k", "tier": TIER}
      for name in names
    }
  )
  saved = catalog.MODELS_TXT
  with tempfile.TemporaryDirectory() as folder:
    catalog.MODELS_TXT = Path(folder) / "models.txt"
    catalog.MODELS_TXT.write_text("".join(f"{name}/x\n" for name in names))
    transport = httpx.MockTransport(upstream)
    async with httpx.AsyncClient(transport=transport) as upstream_client:
      api.set_client(upstream_client)
      app = httpx.ASGITransport(app=api.app)
      async with httpx.AsyncClient(transport=app, base_url="http://t") as client:
        PLAN.update(
          a=("drop", [text("a1", "Hel"), text("a1", "lo")]),
          b=("status", 429),
          c=("ok", [text("c1", " world"), b"data: [DONE]\n\n"]),
        )
        lines = await send(client)
        assert lines[-1] == "[DONE]" and lines.count("[DONE]") == 1, lines
        chunks = [json.loads(line) for line in lines[:-1]]
        joined = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        assert joined == "Hello world", joined
        assert {chunk["id"] for chunk in chunks} == {"a1"}, chunks
        assert [host for host, _ in SEEN] == ["a.test", "b.test", "c.test"], SEEN
        prefix = {"role": "assistant", "content": "Hello"}
        assert SEEN[1][1]["messages"][-1] == prefix, SEEN[1]
        assert SEEN[2][1]["messages"] == [{"role": "user", "content": "hi"}, prefix]

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
  catalog.MODELS_TXT = saved
  api.set_client(None)
  config.set_config(None)
  print("ok: stream continuation")


if __name__ == "__main__":
  asyncio.run(main())
