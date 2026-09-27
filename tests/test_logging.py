import logging

import httpx
from fastapi.testclient import TestClient

from daedalus import api


class Lines(logging.Handler):
  def __init__(self) -> None:
    super().__init__()
    self.lines: list[str] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.lines.append(f"{record.levelname} {record.name} {record.getMessage()}")


def answer(request: httpx.Request) -> httpx.Response:
  if request.url.host == "rejects.test":
    return httpx.Response(429, json={"error": "slow"})
  message = {"role": "assistant", "content": "hi"}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def main() -> None:
  api.setup_logging()
  assert logging.getLogger("httpx").level == logging.WARNING, "no httpx request lines"
  record = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, "up", None, None)
  api.short_name(record)
  assert record.name == "uvicorn", record.name
  lines = Lines()
  logging.getLogger("daedalus").addHandler(lines)
  config = {
    "first": {"api_key": "k", "api_base": "https://rejects.test/v1"},
    "second": {"api_key": "k", "api_base": "https://answers.test/v1"},
  }
  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config: ["first/a", "second/b"]
  api.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  try:
    body = {"model": "daedalus/auto", "messages": [{"role": "user", "content": "hi"}]}
    response = TestClient(api.app).post("/v1/chat/completions", json=body)
  finally:
    api.get_config, api.chain = original
    api.set_client(None)
  assert response.status_code == 200, response.text
  shown = [" ".join(line.split()[:4]) for line in lines.lines]
  assert shown == [
    "WARN daedalus upstream first/a",
    "INFO daedalus upstream second/b",
    "INFO daedalus POST /v1/chat/completions",
  ], lines.lines
  assert lines.lines[0].split()[4] == "429", lines.lines
  assert lines.lines[2].endswith("model=daedalus/auto via=second/b"), lines.lines
  print("ok: log lines")


if __name__ == "__main__":
  main()
