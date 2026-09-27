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
  "groq": {"api_key": "q", "api_base": "https://groq.test/openai/v1"},
}
AUDIO = {"file": ("a.wav", b"RIFF-audio", "audio/wav")}


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
    if request.url.path.endswith("/melotts"):
      audio = base64.b64encode(b"MP3").decode()
      return httpx.Response(200, json={"result": {"audio": audio}, "success": True})
    if request.url.path.endswith("/aura-2-en"):
      return httpx.Response(200, content=b"OGG", headers={"content-type": "audio/ogg"})
    if request.url.path.endswith("/audio/speech"):
      return httpx.Response(200, content=b"WAV", headers={"content-type": "audio/wav"})
    if request.url.host == "cf.test" and "/run/" in request.url.path:
      result = {"text": "hello", "vtt": "WEBVTT\n\nhello"}
      return httpx.Response(200, json={"result": result, "success": True})
    if request.url.host == "cf.test":
      return httpx.Response(400, json={"error": "bad model"})
    if request.url.path.endswith("/audio/transcriptions"):
      return httpx.Response(200, text="hello", headers={"content-type": "text/plain"})
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


def form_fields(request: httpx.Request) -> dict[str, list[str]]:
  """The text fields of a multipart request."""
  fields: dict[str, list[str]] = {}
  for part in request.content.split(
    b"--" + request.headers["content-type"].split("=")[1].encode()
  ):
    head, _, value = part.partition(b"\r\n\r\n")
    if b"filename=" in head or b'name="' not in head:
      continue
    name = head.split(b'name="')[1].split(b'"')[0].decode()
    fields.setdefault(name, []).append(value.rstrip(b"\r\n").decode())
  return fields


def check_transcriptions(fake: Upstream, client: TestClient) -> None:
  data = {
    "model": "groq/whisper-large-v3",
    "response_format": "text",
    "prompt": "names",
    "timestamp_granularities[]": ["word", "segment"],
    "extra": "x",
  }
  response = client.post("/v1/audio/transcriptions", data=data, files=AUDIO)
  assert response.status_code == 200 and response.text == "hello", response.text
  assert response.headers["content-type"].startswith("text/plain"), response.headers
  sent = fake.sent[-1]
  assert str(sent.url) == "https://groq.test/openai/v1/audio/transcriptions", sent.url
  assert sent.headers["authorization"] == "Bearer q", "the key, and no JSON type"
  assert form_fields(sent) == {
    "response_format": ["text"],
    "prompt": ["names"],
    "timestamp_granularities[]": ["word", "segment"],
    "model": ["whisper-large-v3"],
  }, form_fields(sent)
  assert b"RIFF-audio" in sent.content, "the file goes upstream"
  data = {"model": "mistral/voxtral-mini-latest", "prompt": "names", "language": "en"}
  client.post("/v1/audio/transcriptions", data=data, files=AUDIO)
  fields = form_fields(fake.sent[-1])
  assert fields == {"language": ["en"], "model": ["voxtral-mini-latest"]}, fields


def check_cloudflare_audio(fake: Upstream, client: TestClient) -> None:
  data = {"model": "cloudflare/@cf/openai/whisper", "response_format": "vtt"}
  response = client.post("/v1/audio/transcriptions", data=data, files=AUDIO)
  assert response.status_code == 200 and response.text.startswith("WEBVTT"), (
    response.text
  )
  sent = fake.sent[-1]
  assert sent.url.path == "/ai/run/@cf/openai/whisper", sent.url
  assert sent.content == b"RIFF-audio" and sent.headers["content-type"] == "audio/wav"
  data = {"model": "cloudflare/@cf/openai/whisper-large-v3-turbo", "prompt": "names"}
  response = client.post("/v1/audio/transcriptions", data=data, files=AUDIO)
  assert response.json() == {"text": "hello"}, response.text
  expected = {
    "audio": base64.b64encode(b"RIFF-audio").decode(),
    "initial_prompt": "names",
  }
  assert json.loads(fake.sent[-1].content) == expected, fake.sent[-1].content
  count = len(fake.sent)
  for data, files in (
    ({"model": "cloudflare/@cf/openai/whisper", "response_format": "srt"}, AUDIO),
    ({"model": "gemini/gemini-2.5-flash"}, AUDIO),
    ({"model": "groq/whisper-large-v3", "response_format": "mp3"}, AUDIO),
    ({"model": "groq/whisper-large-v3"}, None),
    ({"model": "koinos"}, AUDIO),
  ):
    response = client.post("/v1/audio/transcriptions", data=data, files=files)
    assert response.status_code == 400, (data, response.text)
  headers = {"content-type": "multipart/form-data; boundary=x"}
  response = client.post("/v1/audio/transcriptions", content=b"broken", headers=headers)
  assert response.status_code == 400, response.text
  assert len(fake.sent) == count, "no upstream call for a bad request"


def check_speech(fake: Upstream, client: TestClient) -> None:
  body = {
    "model": "groq/canopylabs/orpheus-v1-english",
    "input": "Hi",
    "voice": "tara",
    "response_format": "wav",
    "stream_format": "sse",
  }
  response = client.post("/v1/audio/speech", json=body)
  assert response.status_code == 200 and response.content == b"WAV", response.text
  assert response.headers["content-type"] == "audio/wav", response.headers
  sent = fake.sent[-1]
  assert str(sent.url) == "https://groq.test/openai/v1/audio/speech", sent.url
  expected = {
    "model": "canopylabs/orpheus-v1-english",
    "input": "Hi",
    "voice": "tara",
    "response_format": "wav",
  }
  assert json.loads(sent.content) == expected, "only the known fields go upstream"
  body = {"model": "cloudflare/@cf/myshell-ai/melotts", "input": "Hi"}
  response = client.post("/v1/audio/speech", json=body)
  assert response.content == b"MP3" and response.headers["content-type"] == "audio/mpeg"
  assert json.loads(fake.sent[-1].content) == {"prompt": "Hi"}, fake.sent[-1].content
  body = {
    "model": "cloudflare/@cf/deepgram/aura-2-en",
    "input": "Hi",
    "voice": "luna",
    "response_format": "opus",
  }
  response = client.post("/v1/audio/speech", json=body)
  assert response.content == b"OGG", response.text
  sent = json.loads(fake.sent[-1].content)
  assert sent == {
    "text": "Hi",
    "encoding": "opus",
    "container": "ogg",
    "speaker": "luna",
  }
  count = len(fake.sent)
  for body in (
    {
      "model": "cloudflare/@cf/myshell-ai/melotts",
      "input": "Hi",
      "response_format": "wav",
    },
    {"model": "cloudflare/@cf/other/tts", "input": "Hi"},
    {"model": "gemini/gemini-2.5-flash-preview-tts", "input": "Hi"},
    {"model": "groq/x", "input": ""},
    {"model": "groq/x", "input": "Hi", "response_format": "ogg"},
    {"model": "daedalus/auto", "input": "Hi"},
  ):
    response = client.post("/v1/audio/speech", json=body)
    assert response.status_code == 400, (body, response.text)
  assert len(fake.sent) == count, "no upstream call for a bad request"


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
    check_transcriptions(fake, client)
    check_cloudflare_audio(fake, client)
    check_speech(fake, client)
  finally:
    media.get_config = original
    upstream.set_client(None)
  print("ok: embeddings, transcriptions and speech")


if __name__ == "__main__":
  main()
