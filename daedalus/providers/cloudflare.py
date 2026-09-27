import base64
import json
import time
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from daedalus.providers.base import OpenAIProvider, ProviderError, limits

TRANSCRIPT_FORMATS = ("json", "text", "vtt")
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


class CloudflareProvider(OpenAIProvider):
  """Cloudflare Workers AI through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.cloudflare.com/client/v4/accounts/os.environ/CLOUDFLARE_ACCOUNT_ID/ai/v1",
    "discovery_url": "https://api.cloudflare.com/client/v4/accounts/os.environ/CLOUDFLARE_ACCOUNT_ID/ai/models/search?per_page=100",
  }

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
    """A native run request for one image. Flux 1 takes the prompt only."""
    if (payload.get("n") or 1) != 1:
      raise ProviderError("Cloudflare makes one image for each request")
    body: dict[str, Any] = {"prompt": payload["prompt"]}
    size = payload.get("size")
    if "flux-1" not in slug and size and size != "auto":
      width, height = size.split("x")
      body.update(width=int(width), height=int(height))
    return f"{self.base.removesuffix('/v1')}/run/{slug}", {"json": body}, self.headers()

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
    """Only the tasks that a Daedalus endpoint serves go into the catalog."""
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
