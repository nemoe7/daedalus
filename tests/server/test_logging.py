import logging
import re

import httpx
from fastapi.testclient import TestClient

from daedalus.providers.base import error_text
from daedalus.server import api, logs, upstream

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}


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


def test_error_text() -> None:
  cases = {
    b'{"error": {"message": "Rate  limit", "code": 429}}': "Rate limit",
    b'{"errors": [{"code": 7000, "message": "No route"}], "success": false}': "No route",
    b'[{"error": {"code": 400, "message": "API key not valid"}}]': "API key not valid",
    b'{"error": "model not found"}': "model not found",
    b"<html>Bad gateway</html>": "<html>Bad gateway</html>",
    b"": "no message",
  }
  for raw, expected in cases.items():
    assert error_text(raw) == expected, (raw, error_text(raw))
  assert len(error_text("x" * 1000)) == 300, "a long body is cut"


def test_lines() -> None:
  logs.setup_logging()
  assert logging.getLogger("httpx").level == logging.WARNING, "no httpx request lines"
  record = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, "up", None, None)
  logs.short_name(record)
  assert record.name == "uvicorn", record.name
  lines = Lines()
  logging.getLogger("daedalus").addHandler(lines)
  config = {
    "first": {"api_key": "k", "api_base": "https://rejects.test/v1"},
    "second": {"api_key": "k", "api_base": "https://answers.test/v1"},
    "keyless": {"api_base": "https://answers.test/v1"},
  }
  original = api.get_config, api.chain
  api.get_config = lambda: config
  groups = [["keyless/c"], ["first/a"], ["second/b"]]
  api.chain = lambda model, body, config, key="": (groups, "daedalus/auto:TIER-B")
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  try:
    body = {"model": "daedalus/auto", "messages": [{"role": "user", "content": "hi"}]}
    response = TestClient(api.app, headers=AUTH).post("/v1/chat/completions", json=body)
  finally:
    api.get_config, api.chain = original
    upstream.set_client(None)
  assert response.status_code == 200, response.text
  shown = [" ".join(line.split()[:4]) for line in lines.lines]
  assert shown == [
    "WARN daedalus upstream keyless/c",
    "WARN daedalus upstream first/a",
    "INFO daedalus cooldown first/a",
    "INFO daedalus upstream second/b",
    "INFO daedalus POST /v1/chat/completions",
  ], lines.lines
  assert lines.lines[0].endswith("ProviderError: Missing api_key for keyless")
  assert lines.lines[1].split()[4] == "429", lines.lines
  assert lines.lines[1].endswith("s: slow"), "the provider message"
  assert lines.lines[2].endswith("cooldown first/a 60.000s reason=backoff"), lines.lines
  assert re.search(
    r"key=master model=daedalus/auto pool=deinos via=second/b pin=new ttft=\d+\.\d{3}s fallbacks=2$",
    lines.lines[4],
  )
  lines.lines.clear()
  client = TestClient(api.app)
  client.get("/")
  client.get("/ui/api/status")
  shown = [" ".join(line.split()[:5]) for line in lines.lines]
  assert shown == ["INFO daedalus GET /ui/api/status 401"], (
    "dashboard reads go to debug"
  )
