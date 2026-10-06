"""Tests for the endpoints of non-chat models."""

import base64
import io
import json
import struct
import tempfile
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import dashboard, store
from daedalus.server import api, media, upstream

MASTER = "test-master-key-0001"
CONFIG = {
  "mistral": {"api_key": "k", "api_base": "https://mistral.test/v1"},
  "gemini": {"api_key": "g", "api_base": "https://gemini.test/v1beta"},
  "cloudflare": {"api_key": "c", "api_base": "https://cf.test/ai/v1"},
  "groq": {"api_key": "q", "api_base": "https://groq.test/openai/v1"},
}
AUDIO = {"file": ("a.wav", b"RIFF-audio", "audio/wav")}
PNG = b"\x89PNG\r\n"


def gemini_answer(body: dict) -> httpx.Response:
  """A generateContent answer with PCM audio for speech, or with text."""
  if body.get("generationConfig", {}).get("responseModalities") == ["AUDIO"]:
    data = base64.b64encode(b"\x01\x00" * 4).decode()
    part = {"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=16000", "data": data}}
  else:
    part = {"text": "hello"}
  return httpx.Response(200, json={"candidates": [{"content": {"parts": [part]}}]})


class Upstream:
  def __init__(self) -> None:
    self.sent: list[httpx.Request] = []
    self.down: set[str] = set()
    self.limited: set[str] = set()

  def __call__(self, request: httpx.Request) -> httpx.Response:
    self.sent.append(request)
    if request.url.host in self.down:
      return httpx.Response(503, json={"error": "down"})
    if request.url.host in self.limited:
      return httpx.Response(429, json={"error": "slow"}, headers={"retry-after": "60"})
    if request.url.path.endswith(":generateContent"):
      return gemini_answer(json.loads(request.content))
    if request.url.host == "gemini.test":
      texts = [
        r["content"]["parts"][0]["text"]
        for r in json.loads(request.content)["requests"]
      ]
      return httpx.Response(
        200, json={"embeddings": [{"values": [0.5, 1.0]} for _ in texts]}
      )
    if request.url.path.endswith("/flux-1-schnell"):
      return httpx.Response(
        200, json={"result": {"image": "/9j/jpeg"}, "success": True}
      )
    if request.url.path.endswith("/stable-diffusion-xl-base-1.0"):
      return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    if request.url.path.endswith("/images/generations"):
      return httpx.Response(
        200, json={"created": 1, "data": [{"url": "https://i.test/1"}]}
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


def test_embeddings(fake: Upstream, client: TestClient) -> None:
  body = {
    "model": "mistral/mistral-embed-2312",
    "input": "hi",
    "dimensions": 8,
    "user": "u",
  }
  response = client.post("/v1/embeddings", json=body)
  assert response.status_code == 200, response.text
  assert response.json()["model"] == "mistral/mistral-embed-2312", response.json()
  sent = fake.sent[-1]
  assert str(sent.url) == "https://mistral.test/v1/embeddings", sent.url
  expected = {"model": "mistral-embed-2312", "input": "hi", "output_dimension": 8}
  assert json.loads(sent.content) == expected, "only the known fields go upstream"
  body = {
    "model": "mistral/mistral-embed-2312",
    "input": ["hi"],
    "encoding_format": "base64",
  }
  vector = client.post("/v1/embeddings", json=body).json()["data"][0]["embedding"]
  assert struct.unpack("<2f", base64.b64decode(vector)) == (0.25, -1.0), vector
  assert "encoding_format" not in json.loads(fake.sent[-1].content), "floats upstream"


def test_unlisted_model(fake: Upstream, client: TestClient) -> None:
  """A media route refuses a direct id outside the model list, with the nearest listed one."""
  count = len(fake.sent)
  response = client.post(
    "/v1/embeddings", json={"model": "mistral/mistral-embed", "input": "hi"}
  )
  assert response.status_code == 404, response.text
  error = response.json()["error"]
  assert error["type"] == "model_not_found" and error["code"] == 404, error
  assert "mistral/mistral-embed-2312" in error["message"], error
  assert len(fake.sent) == count, "the alias never reaches the upstream"


def test_gemini(fake: Upstream, client: TestClient) -> None:
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


def test_errors(fake: Upstream, client: TestClient) -> None:
  count = len(fake.sent)
  for body, status in (
    ({"model": "daedalus/auto", "input": "hi"}, 400),
    ({"model": "koinos", "input": "hi"}, 400),
    ({"model": "nobody/x", "input": "hi"}, 400),
    ({"model": "mistral/mistral-embed-2312", "input": []}, 400),
    (
      {"model": "mistral/mistral-embed-2312", "input": "hi", "encoding_format": "x"},
      400,
    ),
  ):
    response = client.post("/v1/embeddings", json=body)
    assert response.status_code == status, (body, response.text)
  assert len(fake.sent) == count, "no upstream call for a bad request"
  response = client.post(
    "/v1/embeddings", json={"model": "cloudflare/@cf/x", "input": "hi"}
  )
  assert response.status_code == 400, "the upstream status, with no fallback"
  assert len(fake.sent) == count + 1, "one attempt only"
  row = dashboard.HISTORY.latest(1)[0]
  assert (
    row["model"] == "cloudflare/@cf/x" and row["attempts"][0]["result"] == "HTTP 400"
  )
  denied = TestClient(api.app).post(
    "/v1/embeddings", json={"model": "m/x", "input": "a"}
  )
  assert denied.status_code == 401, denied.text


def test_transcriptions(fake: Upstream, client: TestClient) -> None:
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


def test_cloudflare_audio(fake: Upstream, client: TestClient) -> None:
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
    ({"model": "gemini/gemini-3.5-transcribe", "response_format": "vtt"}, AUDIO),
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


def test_speech(fake: Upstream, client: TestClient) -> None:
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
    {"model": "gemini/gemini-3.8-flash-tts", "input": "Hi", "response_format": "mp3"},
    {"model": "groq/x", "input": ""},
    {"model": "groq/x", "input": "Hi", "response_format": "ogg"},
    {"model": "daedalus/auto", "input": "Hi"},
  ):
    response = client.post("/v1/audio/speech", json=body)
    assert response.status_code == 400, (body, response.text)
  assert len(fake.sent) == count, "no upstream call for a bad request"


def test_images(fake: Upstream, client: TestClient) -> None:
  body = {"model": "groq/img", "prompt": "a cat", "n": 2, "size": "512x512", "seed": 3}
  response = client.post("/v1/images/generations", json=body)
  assert response.json()["data"] == [{"url": "https://i.test/1"}], response.text
  sent = fake.sent[-1]
  assert str(sent.url) == "https://groq.test/openai/v1/images/generations", sent.url
  expected = {"model": "img", "prompt": "a cat", "n": 2, "size": "512x512"}
  assert json.loads(sent.content) == expected, "only the known fields go upstream"
  flux = "cloudflare/@cf/black-forest-labs/flux-1-schnell"
  body = {"model": flux, "prompt": "a cat", "size": "512x512"}
  response = client.post("/v1/images/generations", json=body)
  assert response.json()["data"] == [{"url": "data:image/jpeg;base64,/9j/jpeg"}]
  assert json.loads(fake.sent[-1].content) == {"prompt": "a cat"}, "Flux 1: no size"
  sdxl = "cloudflare/@cf/stabilityai/stable-diffusion-xl-base-1.0"
  body = {
    "model": sdxl,
    "prompt": "a cat",
    "size": "512x768",
    "response_format": "b64_json",
  }
  response = client.post("/v1/images/generations", json=body)
  assert response.json()["data"] == [{"b64_json": base64.b64encode(PNG).decode()}]
  sent = json.loads(fake.sent[-1].content)
  assert sent == {"prompt": "a cat", "width": 512, "height": 768}, sent
  count = len(fake.sent)
  for body in (
    {"model": flux, "prompt": "a cat", "n": 2},
    {"model": "gemini/gemini-2.5-flash-image", "prompt": "a cat"},
    {"model": "groq/img", "prompt": ""},
    {"model": "groq/img", "prompt": "a cat", "n": 0},
    {"model": "groq/img", "prompt": "a cat", "n": True},
    {"model": "groq/img", "prompt": "a cat", "size": "big"},
    {"model": "groq/img", "prompt": "a cat", "size": "512x512x512"},
    {"model": "groq/img", "prompt": "a cat", "size": "0512x512"},
    {"model": "groq/img", "prompt": "a cat", "size": "512X512"},
    {"model": "groq/img", "prompt": "a cat", "response_format": "png"},
    {"model": "sophos", "prompt": "a cat"},
  ):
    response = client.post("/v1/images/generations", json=body)
    assert response.status_code == 400, (body, response.text)
  assert len(fake.sent) == count, "no upstream call for a bad request"


def test_valid_size() -> None:
  """auto or WIDTHxHEIGHT with whole numbers above 0."""
  for size in ("auto", "1x1", "512x512", "4096x4096"):
    assert media.valid_size(size)
  for size in ("", " auto", "512", "512x", "x512", "0x1", "1x0", "0512x512", "512X512"):
    assert not media.valid_size(size)


def test_gemini_audio(fake: Upstream, client: TestClient) -> None:
  body = {"model": "gemini/gemini-3.8-flash-tts", "input": "Hi", "voice": "Kore"}
  body["instructions"] = "Say calmly"
  response = client.post("/v1/audio/speech", json=body)
  assert response.status_code == 200, response.text
  assert response.headers["content-type"] == "audio/wav", response.headers
  with wave.open(io.BytesIO(response.content)) as audio:
    assert (audio.getframerate(), audio.getnframes()) == (16000, 4), (
      "rate from the MIME type"
    )
  sent = fake.sent[-1]
  assert sent.url.path == "/v1beta/models/gemini-3.8-flash-tts:generateContent"
  sent_body = json.loads(sent.content)
  assert sent_body["contents"][0]["parts"][0]["text"] == "Say calmly: Hi", sent_body
  voice = sent_body["generationConfig"]["speechConfig"]["voiceConfig"]
  assert voice == {"prebuiltVoiceConfig": {"voiceName": "Kore"}}, voice
  body = {
    "model": "gemini/gemini-3.8-flash-tts",
    "input": "Hi",
    "response_format": "pcm",
  }
  response = client.post("/v1/audio/speech", json=body)
  assert response.content == b"\x01\x00" * 4, "raw PCM"
  assert "speechConfig" not in json.loads(fake.sent[-1].content)["generationConfig"]
  body["response_format"] = "mp3"
  assert client.post("/v1/audio/speech", json=body).status_code == 400
  model = "gemini/gemini-3.5-transcribe"
  fields = {"model": model, "language": "en", "response_format": "text"}
  response = client.post("/v1/audio/transcriptions", data=fields, files=AUDIO)
  assert response.status_code == 200 and response.text == "hello", response.text
  parts = json.loads(fake.sent[-1].content)["contents"][0]["parts"]
  inline = {"mimeType": "audio/wav", "data": base64.b64encode(b"RIFF-audio").decode()}
  assert parts[0] == {"inlineData": inline}, parts
  assert "The language is en." in parts[1]["text"], parts
  response = client.post("/v1/audio/transcriptions", data={"model": model}, files=AUDIO)
  assert response.json() == {"text": "hello"}, response.text
  fields = {"model": model, "response_format": "srt"}
  response = client.post("/v1/audio/transcriptions", data=fields, files=AUDIO)
  assert response.status_code == 400, response.text


def test_model_list(client: TestClient) -> None:
  rows = [
    {"id": "groq/whisper-large-v3", "mode": "audio_transcription"},
    {
      "id": "groq/llama",
      "mode": "chat",
      "max_input_tokens": 128000,
      "max_output_tokens": 32768,
      "supports_function_calling": 1,
      "supports_reasoning": 0,
    },
    {
      "id": "mistral/mistral-embed-2312",
      "mode": "embedding",
      "max_input_tokens": 8192,
    },
    {"id": "kilo/new", "max_input_tokens": 262144, "supports_reasoning": 1},
  ]
  tiers = {"api_key": "k", "tier": {"TIER-A": ["*"]}}
  with tempfile.TemporaryDirectory() as name:
    original, store.MODELS_DB = store.MODELS_DB, Path(name) / "models.sqlite3"
    config, api.get_config = api.get_config, lambda: {"groq": tiers, "kilo": tiers}
    try:
      store.write_store(rows)
      data = client.get("/v1/models").json()["data"]
    finally:
      store.MODELS_DB, api.get_config = original, config
  names = [row["id"] for row in data]
  found = {row["id"]: row for row in data}
  sophos = {
    "id": "daedalus/sophos",
    "object": "model",
    "owned_by": "daedalus",
    "max_input_tokens": 262144,
    "max_output_tokens": 32768,
    "supports_function_calling": True,
    "supports_reasoning": True,
  }
  assert found["daedalus/sophos"] == sophos, found["daedalus/sophos"]
  assert found["daedalus/auto"] == {**sophos, "id": "daedalus/auto"}, (
    "auto copies sophos"
  )
  assert set(found["daedalus/moros"]) == {"id", "object", "owned_by"}, "no members"
  assert found["groq/llama"]["supports_reasoning"] is False, found["groq/llama"]
  assert "max_input_tokens" not in found["mistral/mistral-embed-2312"], (
    "chat models only"
  )
  assert names[:5] == ["daedalus/auto", *api.router.POOLS], names
  assert names[5:7] == ["groq/llama", "kilo/new"], "chat models first"
  assert names[7] == "daedalus/graphos", "a media pool with members is listed"
  assert sorted(names[8:]) == [
    "groq/whisper-large-v3",
    "mistral/mistral-embed-2312",
  ], names


def test_empty_pool(client: TestClient, own_store) -> None:
  body = {"model": "daedalus/photos", "prompt": "a cat"}
  response = client.post("/v1/images/generations", json=body)
  assert response.status_code == 400, response.text
  assert response.json()["error"]["message"] == "photos has no models", response.text
  # The other media pool answers the same text, and a pool of the other endpoint gets the hint.
  response = client.post(
    "/v1/audio/transcriptions", data={"model": "daedalus/graphos"}, files=AUDIO
  )
  assert response.json()["error"]["message"] == "graphos has no models", response.text
  response = client.post(
    "/v1/images/generations", json={"model": "daedalus/graphos", "prompt": "a cat"}
  )
  assert response.json()["error"]["message"] == (
    "This endpoint needs a provider/slug model or photos"
  ), response.text


def test_pools(fake: Upstream, client: TestClient, own_store) -> None:
  store.write_store(POOL_ROWS)
  pick, api.PENALTIES.pick = api.PENALTIES.pick, lambda: 0.0
  try:
    row = transcribe(client, b"RIFF-one")
    assert row["via"] == "mistral/voxtral" and row["fallbacks"] == "0", row
    fake.down.add("mistral.test")
    row = transcribe(client, b"RIFF-two")
    fake.down.clear()
    assert [a["result"] for a in row["attempts"]] == ["HTTP 503", "answered"], row
    assert row["fallbacks"] == "1" and row.get("retry") is None, row
    weights = api.PENALTIES.weights(["mistral/voxtral", "groq/whisper"])
    weights = {m: round(w, 3) for m, w in weights.items()}
    assert weights == {"mistral/voxtral": 0.5, "groq/whisper": 1.0}, weights
    assert row["attempts"][0]["weight_change"] == {"from": 1.0, "to": 0.5}, row
    assert "weight_change" not in row["attempts"][1], row
    row = transcribe(client, b"RIFF-two")
    assert (row["via"], row["retry"]) == ("mistral/voxtral", "1"), row
    row = transcribe(client, b"RIFF-two")
    assert (row["via"], row["retry"]) == ("groq/whisper", "2"), "the list starts again"
    row = transcribe(client, b"RIFF-three")
    assert row.get("retry") is None, "new content is not a try again"
    body = {"model": "daedalus/photos", "prompt": "a cat", "n": 2}
    response = client.post("/v1/images/generations", json=body)
    assert response.json()["data"] == [{"url": "https://i.test/1"}], response.text
    results = [a["result"] for a in dashboard.HISTORY.latest(1)[0]["attempts"]]
    assert results == ["skipped", "answered"], "Flux 1 makes 1 image only"
    login = {"username": "admin", "password": MASTER}
    assert client.post("/ui/api/login", json=login).status_code == 200
    pools = client.get("/ui/api/pools").json()
    found = {pool["name"]: pool for pool in pools}
    assert found["daedalus/photos"]["mode"] == "image_generation", found
    ids = [m["id"] for m in found["daedalus/graphos"]["members"]]
    assert ids == ["mistral/voxtral", "groq/whisper"], ids
    models = {row["id"]: row for row in client.get("/ui/api/models").json()}
    voxtral = models["mistral/voxtral"]
    assert isinstance(voxtral["weight"], float), "media pool models have weights"
  finally:
    api.PENALTIES.pick = pick
  assert_media_cooldown(fake, client)
  assert_media_pacing(fake, client)
  count = len(fake.sent)
  for path, body in (
    ("/v1/images/generations", {"model": "daedalus/graphos", "prompt": "a cat"}),
    ("/v1/embeddings", {"model": "daedalus/photos", "input": "hi"}),
  ):
    response = client.post(path, json=body)
    assert response.status_code == 400, (body, response.text)
  assert len(fake.sent) == count, "a pool of another endpoint is not sent"


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


POOL_ROWS = [
  {"id": "mistral/voxtral", "mode": "audio_transcription"},
  {"id": "groq/whisper", "mode": "audio_transcription"},
  {"id": "cloudflare/@cf/black-forest-labs/flux-1-schnell", "mode": "image_generation"},
  {"id": "groq/img", "mode": "image_generation"},
]


def transcribe(client: TestClient, audio: bytes) -> dict:
  """Send one graphos request, and return its dashboard row."""
  files = {"file": ("a.wav", audio, "audio/wav")}
  response = client.post(
    "/v1/audio/transcriptions",
    data={"model": "daedalus/graphos", "response_format": "text"},
    files=files,
  )
  assert response.status_code == 200, response.text
  return dashboard.HISTORY.latest(1)[0]


def assert_media_cooldown(fake: Upstream, client: TestClient) -> None:
  form = {"model": "mistral/voxtral", "response_format": "text"}
  files = {"file": ("a.wav", b"RIFF-five", "audio/wav")}
  fake.limited.add("mistral.test")
  response = client.post("/v1/audio/transcriptions", data=form, files=files)
  fake.limited.clear()
  assert response.status_code == 429, response.text
  cooled = dashboard.HISTORY.latest(1)[0]["attempts"][0]["cooldown"]
  assert cooled == {"seconds": 60, "reason": "reset"}, cooled
  count = len(fake.sent)
  response = client.post("/v1/audio/transcriptions", data=form, files=files)
  assert response.status_code == 429 and len(fake.sent) == count, "no upstream call"
  assert response.json()["error"]["type"] == "rate_limit_exceeded", response.text
  row = transcribe(client, b"RIFF-six")
  assert [a["model"] for a in row["attempts"]] == ["groq/whisper"], row
  api.COOLDOWNS.clear()


def assert_media_pacing(fake: Upstream, client: TestClient) -> None:
  form = {"model": "mistral/voxtral", "response_format": "text"}
  files = {"file": ("a.wav", b"RIFF-seven", "audio/wav")}
  original = store.pace_limits
  store.pace_limits = lambda: {"mistral/voxtral": (1.0, None)}
  api.PACING.clear()
  try:
    response = client.post("/v1/audio/transcriptions", data=form, files=files)
    assert response.status_code == 200, response.text
    count = len(fake.sent)
    response = client.post("/v1/audio/transcriptions", data=form, files=files)
    assert response.status_code == 429 and len(fake.sent) == count, "rpm 1 reached"
    row = transcribe(client, b"RIFF-eight")
    assert [a["model"] for a in row["attempts"]] == ["groq/whisper"], row
  finally:
    store.pace_limits = original
    api.PACING.clear()


# The direct ids of this file, listed in the store, so the Models-page gate lets them answer.
MODELS = [
  {"id": "mistral/mistral-embed-2312", "mode": "embedding"},
  {"id": "mistral/voxtral", "mode": "audio_transcription"},
  {"id": "mistral/voxtral-mini-latest", "mode": "audio_transcription"},
  {"id": "gemini/gemini-embedding-001", "mode": "embedding"},
  {"id": "gemini/gemini-3.8-flash-tts", "mode": "audio_speech"},
  {"id": "gemini/gemini-3.5-transcribe", "mode": "audio_transcription"},
  {"id": "gemini/gemini-2.5-flash-image", "mode": "image_generation"},
  {"id": "groq/whisper-large-v3", "mode": "audio_transcription"},
  {"id": "groq/canopylabs/orpheus-v1-english", "mode": "audio_speech"},
  {"id": "groq/img", "mode": "image_generation"},
  {"id": "cloudflare/@cf/x", "mode": "embedding"},
  {"id": "cloudflare/@cf/openai/whisper", "mode": "audio_transcription"},
  {"id": "cloudflare/@cf/openai/whisper-large-v3-turbo", "mode": "audio_transcription"},
  {"id": "cloudflare/@cf/myshell-ai/melotts", "mode": "audio_speech"},
  {"id": "cloudflare/@cf/deepgram/aura-2-en", "mode": "audio_speech"},
  {"id": "cloudflare/@cf/other/tts", "mode": "audio_speech"},
  {
    "id": "cloudflare/@cf/black-forest-labs/flux-1-schnell",
    "mode": "image_generation",
  },
  {
    "id": "cloudflare/@cf/stabilityai/stable-diffusion-xl-base-1.0",
    "mode": "image_generation",
  },
]


@pytest.fixture
def own_store():
  """A state file of its own, so a pool test decides its own rows and their order."""
  with tempfile.TemporaryDirectory() as name:
    original, store.MODELS_DB = store.MODELS_DB, Path(name) / "models.sqlite3"
    try:
      yield
    finally:
      store.MODELS_DB = original


@pytest.fixture(scope="module")
def fake():
  fake = Upstream()
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(fake)))
  yield fake
  upstream.set_client(None)


@pytest.fixture(scope="module")
def client(fake: Upstream):
  store.write_store(MODELS)
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(media, "get_config", lambda: CONFIG)
    patch.setattr(api, "get_config", lambda: CONFIG)
    yield TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
