"""Tests for the image edit endpoint and its pool of image input models."""

import base64
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from daedalus import dashboard, store
from daedalus.server import api, media, upstream

MASTER = "test-master-key-0001"
CONFIG = {
  "cloudflare": {"api_key": "c", "api_base": "https://cf.test/ai/v1"},
  "groq": {"api_key": "q", "api_base": "https://groq.test/openai/v1"},
}
KLEIN = "cloudflare/@cf/black-forest-labs/flux-2-klein-4b"
ROWS = [
  {"id": KLEIN, "mode": "image_generation", "supports_vision": True},
  {"id": "groq/img", "mode": "image_generation", "supports_vision": True},
  {"id": "cloudflare/@cf/black-forest-labs/flux-1-schnell", "mode": "image_generation"},
]


def picture(width: int, height: int) -> bytes:
  """A PNG image of one size."""
  output = io.BytesIO()
  Image.new("RGB", (width, height), "red").save(output, format="PNG")
  return output.getvalue()


def parts(request: httpx.Request) -> dict[str, bytes]:
  """Each part of a multipart request by its field name."""
  boundary = request.headers["content-type"].split("boundary=")[1].encode()
  found = {}
  for part in request.read().split(b"--" + boundary):
    head, _, value = part.partition(b"\r\n\r\n")
    if b'name="' in head:
      found[head.split(b'name="')[1].split(b'"')[0].decode()] = value[:-2]
  return found


class Upstream:
  def __init__(self) -> None:
    self.sent: list[httpx.Request] = []
    self.down: set[str] = set()

  def __call__(self, request: httpx.Request) -> httpx.Response:
    self.sent.append(request)
    if request.url.host in self.down:
      return httpx.Response(503, json={"error": "down"})
    if request.url.path.endswith("/flux-2-klein-4b"):
      return httpx.Response(200, json={"result": {"image": "iVBORcf"}, "success": True})
    if request.url.path.endswith("/images/edits"):
      return httpx.Response(200, json={"created": 1, "data": [{"b64_json": "groq"}]})
    return httpx.Response(400, json={"error": "bad path"})


@pytest.fixture(scope="module")
def fake():
  fake = Upstream()
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(fake)))
  yield fake
  upstream.set_client(None)


@pytest.fixture(scope="module")
def client(fake: Upstream):
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(media, "get_config", lambda: CONFIG)
    patch.setattr(api, "get_config", lambda: CONFIG)
    yield TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})


def edit(
  client: TestClient, prompt: str, image: bytes, **fields: str
) -> httpx.Response:
  """Send one edit request with one input image."""
  form = {"model": "daedalus/photos", "prompt": prompt, **fields}
  files = {"image": ("in.png", image, "image/png")}
  return client.post("/v1/images/edits", data=form, files=files)


def test_edits(fake: Upstream, client: TestClient) -> None:
  """Only image input models edit. Cloudflare gets a small copy, and the others get the original."""
  store.write_store(ROWS)
  large = picture(1024, 800)
  fake.down = {"groq.test"}
  response = edit(client, "make it blue", large, size="1024x1024", n="1")
  assert response.status_code == 200, response.text
  assert response.json()["data"] == [{"url": "data:image/png;base64,iVBORcf"}]
  row = dashboard.HISTORY.latest(1)[0]
  tried = {attempt["model"] for attempt in row["attempts"]}
  assert tried <= {KLEIN, "groq/img"}, "a model without image input does not edit"
  sent = next(r for r in fake.sent if r.url.host == "cf.test")
  form = parts(sent)
  assert form["prompt"] == b"make it blue" and form["width"] == b"1024", form.keys()
  with Image.open(io.BytesIO(form["input_image_0"])) as image:
    assert image.size == (511, 399), image.size

  fake.sent.clear()
  fake.down = {"cf.test"}
  response = edit(client, "make it green", large, response_format="b64_json")
  assert response.json()["data"] == [{"b64_json": "groq"}], response.text
  sent = next(r for r in fake.sent if r.url.host == "groq.test")
  assert sent.url.path == "/openai/v1/images/edits", sent.url
  form = parts(sent)
  assert form["image"] == large, "the other providers get the original image"
  assert form["model"] == b"img" and form["prompt"] == b"make it green", form.keys()
  fake.down = set()


def test_edit_errors(client: TestClient) -> None:
  """An edit needs an image, and a model that can edit it."""
  small = picture(64, 64)
  response = client.post(
    "/v1/images/edits", data={"model": "daedalus/photos", "prompt": "x"}
  )
  assert response.status_code == 400 and "image file" in response.text, response.text
  response = client.post(
    "/v1/images/edits",
    data={"model": "cloudflare/@cf/black-forest-labs/flux-1-schnell", "prompt": "x"},
    files={"image": ("in.png", small, "image/png")},
  )
  assert response.status_code == 400 and "cannot edit" in response.text, response.text
  response = edit(client, "x", small, n="two")
  assert response.status_code == 400 and "n must be" in response.text, response.text
  response = client.post(
    "/v1/images/edits",
    data={"model": KLEIN, "prompt": "x"},
    files={"image": ("in.png", base64.b64decode("AAAA"), "image/png")},
  )
  assert response.status_code == 400 and "cannot read" in response.text, response.text
