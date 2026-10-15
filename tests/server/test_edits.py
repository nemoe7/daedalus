"""Tests for the image edit endpoint and its pool of image input models."""

import base64
import io
import sys

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
  """Image edits use capable models, resize Cloudflare inputs, and keep other inputs unchanged."""
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


@pytest.mark.parametrize("model, status", [(KLEIN, 400), ("daedalus/photos", 502)])
def test_image_size_error(
  client: TestClient, fake: Upstream, monkeypatch, model, status
) -> None:
  """An image above the size guard follows the media error path."""
  store.write_store(ROWS[:1])
  try:
    image = picture(3, 3)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 4)
    fake.sent.clear()
    response = edit(client, "x", image, model=model)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == status
    assert not fake.sent
    assert Image.MAX_IMAGE_PIXELS == 4
  finally:
    store.write_store(ROWS)


def test_image_size_error_keeps_pool_fallback(
  client: TestClient, fake: Upstream, monkeypatch
) -> None:
  """An image-size failure skips one provider but keeps pool fallback."""
  store.write_store(ROWS[:2])
  try:
    image = picture(3, 3)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 4)
    models_for = media.models_for

    def ordered(*args):
      assert set(models_for(*args)) == {KLEIN, "groq/img"}
      return [KLEIN, "groq/img"]

    monkeypatch.setattr(media, "models_for", ordered)
    fake.sent.clear()
    fake.down.clear()
    response = edit(client, "x", image)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == [{"b64_json": "groq"}]
    assert [request.url.host for request in fake.sent] == ["groq.test"]
    assert parts(fake.sent[0])["image"] == image
    attempts = dashboard.HISTORY.latest(1)[0]["attempts"]
    assert attempts[0]["model"] == KLEIN and attempts[0]["result"] == "skipped"
    assert Image.MAX_IMAGE_PIXELS == 4
  finally:
    store.write_store(ROWS)


def test_edit_count_conversion(fake: Upstream, client: TestClient) -> None:
  limit = sys.get_int_max_str_digits()
  if not limit:
    pytest.skip("The integer conversion limit is off.")
  store.write_store(ROWS)
  small = picture(3, 3)
  control = edit(client, "count limits", small, model="groq/img", n="0")
  assert control.status_code == 400
  count = len(fake.sent)
  for raw in ("1" * (limit + 1), "١" * (limit + 1), "-1", "1.0", "²"):
    response = edit(client, "count limits", small, model="groq/img", n=raw)
    assert response.status_code == 400, response.text
    assert response.json() == control.json()
  assert len(fake.sent) == count
  for raw, expected in (("1", 1), ("01", 1), ("١", 1), ("２", 2)):
    response = edit(client, "count limits", small, model="groq/img", n=raw)
    assert response.status_code == 200, response.text
    assert parts(fake.sent[-1])["n"] == str(expected).encode()


@pytest.mark.parametrize(
  "variant", ["mask", "mask_presence", "boundaries", "order", "fields"]
)
def test_edit_retry_inputs(
  fake: Upstream, client: TestClient, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
  monkeypatch.setattr(media, "REPEATS", media.retries.Retries())
  store.write_store([{**ROWS[1], "id": name} for name in ("groq/img", "groq/other")])
  monkeypatch.setattr(fake, "down", set())
  a, b, c = picture(64, 64), picture(64, 65), picture(65, 64)
  prompt = f"retry {variant}"
  first = ([a, b], None, prompt)
  changed = ([b, a], None, prompt)
  if variant in ("mask", "mask_presence"):
    masks = []
    for alpha in (0, 255):
      output = io.BytesIO()
      Image.new("RGBA", (64, 64), (0, 0, 0, alpha)).save(output, format="PNG")
      masks.append(output.getvalue())
    initial_mask = masks[0] if variant == "mask" else None
    first, changed = ([a], initial_mask, prompt), ([a], masks[1], prompt)
  elif variant == "boundaries":
    first, changed = ([a + b, c], None, prompt), ([a, b + c], None, prompt)
  elif variant == "fields":
    changed = ([a, b], None, prompt + " changed")
  answered = {}
  try:
    for label, content, retry in (
      ("first", first, None),
      ("changed", changed, None),
      ("changed", changed, "1"),
      ("first", first, "1"),
    ):
      images, mask, text = content
      files = [("image[]", ("in.png", image, "image/png")) for image in images]
      if mask is not None:
        files.append(("mask", ("mask.png", mask, "image/png")))
      response = client.post(
        "/v1/images/edits",
        data={"model": "daedalus/photos", "prompt": text},
        files=files,
      )
      assert response.status_code == 200, response.text
      row = dashboard.HISTORY.latest(1)[0]
      assert row.get("retry") == retry, row
      if retry is None:
        answered[label] = row["via"]
      else:
        assert row["via"] != answered[label], row
  finally:
    store.write_store(ROWS)


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
  assert response.status_code == 400, response.text
  assert response.json()["error"] == {
    "message": "The model cannot take this request",
    "type": "invalid_request_error",
    "code": 400,
  }, response.text
  response = edit(client, "x", small, n="two")
  assert response.status_code == 400 and "n must be" in response.text, response.text
  response = client.post(
    "/v1/images/edits",
    data={"model": KLEIN, "prompt": "x"},
    files={"image": ("in.png", base64.b64decode("AAAA"), "image/png")},
  )
  assert response.status_code == 400, response.text
  assert response.json()["error"] == {
    "message": "The model cannot take this request",
    "type": "invalid_request_error",
    "code": 400,
  }, response.text
