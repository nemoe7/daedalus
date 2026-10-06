import base64
import io
import json
import os
import time
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from daedalus.config import SAVED
from daedalus.providers.base import OpenAIProvider, ProviderError, Upload, limits

TRANSCRIPT_FORMATS = ("json", "text", "vtt")
# The 2 URLs of the account, in the template form that the card shows as a placeholder.
ACCOUNT_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}"
# The Aura encoding and container for each OpenAI speech format.
AURA_FORMATS = {
  "mp3": ("mp3", None),
  "opus": ("opus", "ogg"),
  "aac": ("aac", None),
  "flac": ("flac", None),
  "wav": ("linear16", "wav"),
  "pcm": ("linear16", "none"),
}


def text_only(message: Any) -> Any:
  """The message with its text-part list as one string, and null content as empty text."""
  if not isinstance(message, dict):
    return message
  content = message.get("content")
  if content is None:
    return {**message, "content": ""}
  if not isinstance(content, list) or not content:
    return message
  if not all(isinstance(part, dict) and part.get("type") == "text" for part in content):
    return message
  return {
    **message,
    "content": "\n".join(str(part.get("text", "")) for part in content),
  }


TASK_MODES = {
  "Text Generation": "chat",
  "Automatic Speech Recognition": "audio_transcription",
  "Text-to-Speech": "audio_speech",
  "Text-to-Image": "image_generation",
  "Text Embeddings": "embedding",
}


def task_name(row: dict) -> Any:
  task = row.get("task")
  return task.get("name") if isinstance(task, dict) else None


# FLUX.2 on Workers AI takes up to 4 input images, each smaller than 512x512.
EDIT_INPUTS = 4
EDIT_SIDE = 511


def multipart(slug: str) -> bool:
  """Whether a Workers AI image model takes only multipart input: the FLUX.2 models."""
  return "flux-2" in slug


def smaller(upload: Upload) -> Upload:
  """The image as it is, or a PNG copy with the long side at 511 pixels."""
  name, data, _ = upload
  try:
    with Image.open(io.BytesIO(data)) as image:
      if max(image.size) <= EDIT_SIDE:
        return upload
      # A JPEG decodes at a lower scale first, which is much faster on a small CPU.
      image.draft("RGB", (EDIT_SIDE, EDIT_SIDE))
      copy = ImageOps.exif_transpose(image)
      copy.thumbnail((EDIT_SIDE, EDIT_SIDE))
      output = io.BytesIO()
      copy.save(output, format="PNG")
  except (UnidentifiedImageError, OSError, ValueError) as exc:
    raise ProviderError("Cloudflare cannot read the input image") from exc
  return f"{name.rsplit('.', 1)[0]}.png", output.getvalue(), "image/png"


class CloudflareProvider(OpenAIProvider):
  """Cloudflare Workers AI through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "account_id": "env:CLOUDFLARE_ACCOUNT_ID",
    "api_base": f"{ACCOUNT_URL}/ai/v1",
    "discovery_url": f"{ACCOUNT_URL}/ai/models/search?per_page=100",
  }
  transcript_formats: ClassVar[tuple[str, ...]] = TRANSCRIPT_FORMATS

  @classmethod
  def configure(cls, config: dict[str, Any]) -> dict[str, Any]:
    """The config with the account id filled into its 2 URL templates, from the config, the store or the env.

    Without an account id, a URL template drops out: a request cannot use it.
    """
    merged = dict(config)
    account_id = str(merged.get("account_id") or "").strip()
    if not account_id:
      account_id = str(
        SAVED.get("CLOUDFLARE_ACCOUNT_ID")
        or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        or ""
      ).strip()
      if account_id:
        merged["account_id"] = account_id
    for key in ("api_base", "discovery_url"):
      value = str(merged.get(key) or "")
      if "{account_id}" not in value:
        continue
      if account_id:
        merged[key] = value.replace("{account_id}", account_id)
      else:
        merged.pop(key)
    return merged

  def __init__(self, name: str, config: Mapping) -> None:
    super().__init__(name, type(self).configure(dict(config)))

  def body(self, slug: str, payload: dict) -> dict:
    """The OpenAI body, with string content where Workers AI models need it."""
    return {
      **payload,
      "model": slug,
      "messages": [text_only(m) for m in payload["messages"]],
    }

  def transcribe_request(
    self, slug: str, fields: dict[str, Any], audio: tuple[str, bytes, str]
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """A native run request, because the OpenAI-compatible API has no audio endpoint."""
    if (fields.get("response_format") or "json") not in TRANSCRIPT_FORMATS:
      raise ProviderError("Cloudflare transcriptions support json, text and vtt only")
    url = f"{self.base.removesuffix('/v1')}/run/{slug}"
    _, content, media = audio
    if "large-v3" not in slug:
      headers = {
        **self.auth(self.key),
        "content-type": media or "application/octet-stream",
      }
      return url, {"content": content}, headers
    body = {"audio": base64.b64encode(content).decode()}
    for field, native in (("language", "language"), ("prompt", "initial_prompt")):
      if fields.get(field):
        body[native] = fields[field]
    return url, {"json": body}, self.headers()

  def transcription(
    self, response: httpx.Response, fields: dict[str, Any]
  ) -> tuple[bytes, str]:
    """The OpenAI answer from the native result, in the requested format."""
    result = response.json().get("result") or {}
    text, form = result.get("text"), fields.get("response_format") or "json"
    if not isinstance(text, str) or (
      form == "vtt" and not isinstance(result.get("vtt"), str)
    ):
      raise ProviderError("Invalid transcription answer")
    if form == "text":
      return text.encode(), "text/plain; charset=utf-8"
    if form == "vtt":
      return result["vtt"].encode(), "text/vtt; charset=utf-8"
    return json.dumps({"text": text}).encode(), "application/json"

  def speech_request(
    self, slug: str, payload: dict
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """A native run request for a MeloTTS or an Aura model."""
    url = f"{self.base.removesuffix('/v1')}/run/{slug}"
    form = payload.get("response_format") or "mp3"
    if "melotts" in slug:
      if form != "mp3":
        raise ProviderError("MeloTTS answers in mp3 only")
      return url, {"json": {"prompt": payload["input"]}}, self.headers()
    if "aura" not in slug:
      raise ProviderError("Cloudflare speech supports MeloTTS and Aura models only")
    encoding, container = AURA_FORMATS[form]
    body = {"text": payload["input"], "encoding": encoding}
    if container:
      body["container"] = container
    if payload.get("voice"):
      body["speaker"] = payload["voice"]
    return url, {"json": body}, self.headers()

  def speech(self, response: httpx.Response, payload: dict) -> tuple[bytes, str]:
    """The audio, from base64 JSON or from the raw answer."""
    media = response.headers.get("content-type", "audio/mpeg")
    if not media.startswith("application/json"):
      return response.content, media
    audio = (response.json().get("result") or {}).get("audio")
    if not isinstance(audio, str):
      raise ProviderError("Invalid speech answer")
    return base64.b64decode(audio), "audio/mpeg"

  def image_request(
    self, slug: str, payload: dict
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """A native run request for one image. Flux 1 takes the prompt only, and FLUX.2 takes only multipart."""
    if (payload.get("n") or 1) != 1:
      raise ProviderError("Cloudflare makes one image for each request")
    body: dict[str, Any] = {"prompt": payload["prompt"]}
    size = payload.get("size")
    if "flux-1" not in slug and size and size != "auto":
      width, height = size.split("x")
      body.update(width=int(width), height=int(height))
    url = f"{self.base.removesuffix('/v1')}/run/{slug}"
    if multipart(slug):
      fields = {key: (None, str(value)) for key, value in body.items()}
      return url, {"files": fields}, self.auth(self.key)
    return url, {"json": body}, self.headers()

  def edit_request(
    self, slug: str, fields: dict[str, Any], images: list[Upload], mask: Upload | None
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """A native multipart run request for one edit, with small copies of the input images."""
    if not multipart(slug):
      raise ProviderError("This Cloudflare model cannot edit images")
    if mask is not None:
      raise ProviderError("Cloudflare takes no mask")
    if len(images) > EDIT_INPUTS:
      raise ProviderError(f"Cloudflare takes up to {EDIT_INPUTS} input images")
    url, content, headers = self.image_request(slug, fields)
    for index, image in enumerate(images):
      content["files"][f"input_image_{index}"] = smaller(image)
    return url, content, headers

  def images(self, response: httpx.Response, payload: dict) -> dict:
    """The OpenAI answer, with a data URL when the client wants a URL."""
    if response.headers.get("content-type", "").startswith("application/json"):
      encoded = (response.json().get("result") or {}).get("image")
      if not isinstance(encoded, str):
        raise ProviderError("Invalid images answer")
    else:
      encoded = base64.b64encode(response.content).decode()
    if payload.get("response_format") == "b64_json":
      item = {"b64_json": encoded}
    else:
      media = "image/png" if encoded.startswith("iVBOR") else "image/jpeg"
      item = {"url": f"data:{media};base64,{encoded}"}
    return {"created": int(time.time()), "data": [item]}

  @staticmethod
  def discoverable(row: dict) -> bool:
    """Only the tasks that a daedalus endpoint serves go into the catalog."""
    return task_name(row) in TASK_MODES

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """Store columns from one Cloudflare row. Properties show true values only."""
    found = {
      item.get("property_id"): item.get("value")
      for item in row.get("properties") or []
      if isinstance(item, dict)
    }
    effort = found.get("reasoning_effort")
    return {
      "mode": TASK_MODES.get(task_name(row)),
      **limits(found.get("context_window")),
      "reasoning_effort": effort.get("default_effort")
      if isinstance(effort, dict)
      else None,
      "supports_function_calling": found.get("function_calling") == "true" or None,
      "supports_reasoning": found.get("reasoning") == "true" or None,
      "supports_vision": found.get("vision") == "true" or None,
    }
