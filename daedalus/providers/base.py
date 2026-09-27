import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx


class ProviderError(ValueError):
  pass


def first(items: Any) -> dict:
  return (
    items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
  )


def frame(data: dict) -> bytes:
  return b"data: " + json.dumps(data).encode() + b"\n\n"


def tool_call(name: str, arguments: Any, identifier: str | None = None) -> dict:
  return {
    "id": identifier or "call_" + uuid.uuid4().hex,
    "type": "function",
    "function": {"name": name, "arguments": json.dumps(arguments)},
  }


def function_call(call: Any) -> tuple[str, str, dict]:
  """Validate one assistant tool call and return its id, name, and arguments."""
  if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
    raise ProviderError("Invalid tool call")
  function = call["function"]
  identifier, name = call.get("id"), function.get("name")
  if not isinstance(identifier, str) or not isinstance(name, str):
    raise ProviderError("A tool call requires an id and a name")
  arguments = json.loads(function.get("arguments") or "{}")
  if not isinstance(arguments, dict):
    raise ProviderError("Function arguments must be a JSON object")
  return identifier, name, arguments


def data_url(url: str) -> tuple[str, str]:
  """Split a base64 data URL into its MIME type and data."""
  prefix, separator, data = url.partition(",")
  if not url.startswith("data:") or not separator or not prefix.endswith(";base64"):
    raise ProviderError("Images require base64 data URLs")
  return prefix[5:-7], data


def system_text(parts: list[dict]) -> list[str]:
  if any("text" not in part for part in parts):
    raise ProviderError("System instructions must contain text")
  return [part["text"] for part in parts]


async def events(response: httpx.Response) -> AsyncIterator[dict]:
  lines = []
  async for line in response.aiter_lines():
    if line.startswith("data:"):
      lines.append(line[5:].lstrip())
    elif not line and lines:
      raw = "\n".join(lines)
      lines = []
      if raw == "[DONE]":
        return
      event = json.loads(raw)
      if not isinstance(event, dict):
        raise ProviderError("Invalid upstream stream event")
      yield event
  if lines:
    raise ProviderError("Incomplete upstream stream event")


class Chunks:
  """Build OpenAI chat completion chunks for one translated stream."""

  def __init__(self, model: str) -> None:
    self.identifier = "chatcmpl-" + uuid.uuid4().hex
    self.created = int(time.time())
    self.model = model

  def chunk(self, delta: dict, reason: str | None = None, choice: int = 0) -> bytes:
    return frame(
      {
        "id": self.identifier,
        "object": "chat.completion.chunk",
        "created": self.created,
        "model": self.model,
        "choices": [{"index": choice, "delta": delta, "finish_reason": reason}],
      }
    )

  def end(self, counts: dict | None, include_usage: bool) -> bytes:
    tail = b""
    if include_usage and counts is not None:
      tail = frame(
        {
          "id": self.identifier,
          "object": "chat.completion.chunk",
          "created": self.created,
          "model": self.model,
          "choices": [],
          "usage": counts,
        }
      )
    return tail + b"data: [DONE]\n\n"


class OpenAIProvider:
  """A provider that serves the OpenAI Chat Completions API."""

  unsupported: tuple[str, ...] = ()
  defaults: ClassVar[Mapping[str, str]] = {"api_type": "openai"}

  def __init__(self, name: str, config: Mapping) -> None:
    base = str(config.get("api_base") or "").rstrip("/")
    parsed = urlsplit(base)
    if (
      parsed.scheme not in {"https", "http"}
      or not parsed.netloc
      or parsed.query
      or parsed.fragment
    ):
      raise ProviderError(f"Invalid api_base for {name}")
    if parsed.username or parsed.password:
      raise ProviderError(f"Credentials in api_base for {name}")
    key = config.get("api_key")
    if not isinstance(key, str) or not key:
      raise ProviderError(f"Missing api_key for {name}")
    self.name, self.base, self.key = name, base, key

  def headers(self) -> dict[str, str]:
    return {"content-type": "application/json", "authorization": f"Bearer {self.key}"}

  def url(self, slug: str, payload: dict) -> str:
    return self.base + "/chat/completions"

  def body(self, slug: str, payload: dict) -> dict:
    return {**payload, "model": slug}

  def request(self, slug: str, payload: dict) -> tuple[str, dict, dict[str, str]]:
    if not isinstance(payload.get("messages"), list):
      raise ProviderError("messages must be a list")
    for field in self.unsupported:
      if payload.get(field) is not None:
        raise ProviderError(f"Unsupported native field: {field}")
    return self.url(slug, payload), self.body(slug, payload), self.headers()

  def completion(self, answer: dict, model: str) -> dict:
    return answer

  async def stream(
    self, response: httpx.Response, model: str, include_usage: bool
  ) -> AsyncIterator[bytes]:
    try:
      async for chunk in response.aiter_bytes():
        if chunk:
          yield chunk
    finally:
      await response.aclose()
