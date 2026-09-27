import logging
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import api, store


class Lines(logging.Handler):
  def __init__(self) -> None:
    super().__init__()
    self.lines: list[str] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.lines.append(record.getMessage())


def answer(request: httpx.Request) -> httpx.Response:
  message = {"role": "assistant", "content": request.url.host}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def check_estimate() -> None:
  image = {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64," + "A" * 9000},
  }
  text = {"type": "text", "text": "abcd" * 10}
  body = {"messages": [{"role": "user", "content": [text, image]}]}
  assert api.input_tokens(body) == 15, "57 characters; the image data does not count"
  tools = [{"type": "function", "function": {"name": "abc"}}]
  assert api.input_tokens({"messages": [], "tools": tools}) == 3, "tools count"
  assert api.input_tokens({"messages": [{"content": "abcde"}]}) == 2, "round up"


def check_limits(database: Path) -> None:
  rows = [
    {"id": "a/1", "max_input_tokens": 10},
    {"id": "b/1"},
    {"id": "c/1", "max_input_tokens": "1000"},
    {"id": "d/1", "max_input_tokens": 0},
  ]
  store.write_store(rows, database)
  assert store.input_limits() == {"a/1": 10, "c/1": 1000}, store.input_limits()


def check_requests() -> None:
  config = {
    name: {"api_key": "k", "api_base": f"https://{name}.test/v1"} for name in "abc"
  }
  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  api.PENALTIES.clear()
  lines = Lines()
  api.logger.addHandler(lines)
  client = TestClient(api.app)
  large = {
    "model": "daedalus/deinos",
    "messages": [{"role": "user", "content": "x" * 400}],
  }
  try:
    api.chain = lambda model, body, config: ([["a/1", "b/1"]], None)
    response = client.post("/v1/chat/completions", json=large)
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "b.test"
    assert any(line.startswith("skip a/1: input ~") for line in lines.lines), (
      lines.lines
    )
    assert api.PENALTIES.weights(["a/1"])["a/1"] == 1.0, "a skip is not a fault"
    small = {**large, "messages": [{"role": "user", "content": "x"}]}
    response = client.post("/v1/chat/completions", json=small)
    assert response.json()["choices"][0]["message"]["content"] == "a.test"
    api.chain = lambda model, body, config: ([["a/1"]], None)
    response = client.post("/v1/chat/completions", json=large)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "context_length_exceeded"
    api.chain = lambda model, body, config: ([[]], None)
    response = client.post("/v1/chat/completions", json=large)
    assert response.status_code == 502, "an empty chain is not a context error"
  finally:
    api.get_config, api.chain = original
    api.set_client(None)
    api.logger.removeHandler(lines)


def main() -> None:
  with tempfile.TemporaryDirectory() as name:
    store.MODELS_DB = Path(name) / "models.sqlite3"
    api.PENALTIES.pick = lambda: 0.0
    check_estimate()
    check_limits(store.MODELS_DB)
    check_requests()
  print("ok: context windows")


if __name__ == "__main__":
  main()
