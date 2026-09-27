"""Runnable check for the endpoints of models that do not chat. Run: python tests/test_media.py"""

import base64
import json
import os
import struct

import httpx
from fastapi.testclient import TestClient

from daedalus import dashboard
from daedalus.server import api, media, upstream

MASTER = "test-master-key-0001"
os.environ["DAEDALUS_MASTER_KEY"] = MASTER
CONFIG = {
  "mistral": {"api_key": "k", "api_base": "https://mistral.test/v1"},
  "gemini": {"api_key": "g", "api_base": "https://gemini.test/v1beta"},
  "cloudflare": {"api_key": "c", "api_base": "https://cf.test/ai/v1"},
}


class Upstream:
  def __init__(self) -> None:
    self.sent: list[httpx.Request] = []

  def __call__(self, request: httpx.Request) -> httpx.Response:
    self.sent.append(request)
    if request.url.host == "gemini.test":
      texts = [
        r["content"]["parts"][0]["text"]
        for r in json.loads(request.content)["requests"]
      ]
      return httpx.Response(
        200, json={"embeddings": [{"values": [0.5, 1.0]} for _ in texts]}
      )
    if request.url.host == "cf.test":
      return httpx.Response(400, json={"error": "bad model"})
    item = {"object": "embedding", "index": 0, "embedding": [0.25, -1.0]}
    return httpx.Response(200, json={"object": "list", "data": [item], "model": "x"})


def check_embeddings(fake: Upstream, client: TestClient) -> None:
  body = {"model": "mistral/mistral-embed", "input": "hi", "dimensions": 8, "user": "u"}
  response = client.post("/v1/embeddings", json=body)
  assert response.status_code == 200, response.text
  assert response.json()["model"] == "mistral/mistral-embed", response.json()
  sent = fake.sent[-1]
  assert str(sent.url) == "https://mistral.test/v1/embeddings", sent.url
  expected = {"model": "mistral-embed", "input": "hi", "output_dimension": 8}
  assert json.loads(sent.content) == expected, "only the known fields go upstream"
  body = {
    "model": "mistral/mistral-embed",
    "input": ["hi"],
    "encoding_format": "base64",
  }
  vector = client.post("/v1/embeddings", json=body).json()["data"][0]["embedding"]
  assert struct.unpack("<2f", base64.b64decode(vector)) == (0.25, -1.0), vector
  assert "encoding_format" not in json.loads(fake.sent[-1].content), "floats upstream"


def check_gemini(fake: Upstream, client: TestClient) -> None:
  body = {"model": "gemini/gemini-embedding-001", "input": ["a", "b"], "dimensions": 2}
  response = client.post("/v1/embeddings", json=body)
  assert response.status_code == 200, response.text
  sent = fake.sent[-1]
  assert sent.url.path == "/v1beta/models/gemini-embedding-001:batchEmbedContents"
  assert sent.headers["x-goog-api-key"] == "g", sent.headers
  first = json.loads(sent.content)["requests"][0]
  assert first == {
    "model": "models/gemini-embedding-001",
    "content": {"parts": [{"text": "a"}]},
    "outputDimensionality": 2,
  }, first
  data = response.json()["data"]
  assert [item["index"] for item in data] == [0, 1] and data[1]["embedding"] == [
    0.5,
    1.0,
  ]
  body = {"model": "gemini/gemini-embedding-001", "input": [1, 2]}
  response = client.post("/v1/embeddings", json=body)
  assert response.status_code == 400, "Gemini takes no tokens"


def check_errors(fake: Upstream, client: TestClient) -> None:
  count = len(fake.sent)
  for body, status in (
    ({"model": "daedalus/auto", "input": "hi"}, 400),
    ({"model": "koinos", "input": "hi"}, 400),
    ({"model": "nobody/x", "input": "hi"}, 400),
    ({"model": "mistral/mistral-embed", "input": []}, 400),
    ({"model": "mistral/mistral-embed", "input": "hi", "encoding_format": "x"}, 400),
  ):
    response = client.post("/v1/embeddings", json=body)
    assert response.status_code == status, (body, response.text)
  assert len(fake.sent) == count, "no upstream call for a bad request"
  response = client.post(
    "/v1/embeddings", json={"model": "cloudflare/@cf/x", "input": "hi"}
  )
  assert response.status_code == 400, "the upstream status, with no fallback"
  assert len(fake.sent) == count + 1, "one attempt only"
  row = dashboard.RECENT[0]
  assert (
    row["model"] == "cloudflare/@cf/x" and row["attempts"][0]["result"] == "HTTP 400"
  )
  denied = TestClient(api.app).post(
    "/v1/embeddings", json={"model": "m/x", "input": "a"}
  )
  assert denied.status_code == 401, denied.text


def main() -> None:
  fake = Upstream()
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(fake)))
  original = media.get_config
  media.get_config = lambda: CONFIG
  client = TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
  try:
    check_embeddings(fake, client)
    check_gemini(fake, client)
    check_errors(fake, client)
  finally:
    media.get_config = original
    upstream.set_client(None)
  print("ok: embeddings for OpenAI-compatible and Gemini providers")


if __name__ == "__main__":
  main()
