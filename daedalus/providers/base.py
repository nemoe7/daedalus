import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx


class ProviderError(ValueError):
  pass


ERROR_TEXT_LIMIT = 300


def hide_inputs(node: Any) -> Any:
  """The error body without `input` keys, where validation errors echo the prompt."""
  if isinstance(node, dict):
    return {key: hide_inputs(value) for key, value in node.items() if key != "input"}
  if isinstance(node, list):
    return [hide_inputs(value) for value in node]
  return node


def error_detail(raw: bytes, limit: int) -> str:
  """The full error body for the dashboard, without echoed prompt text."""
  text = raw.decode("utf-8", "replace")
  try:
    text = json.dumps(hide_inputs(json.loads(text)), ensure_ascii=False)
  except ValueError:
    pass
  return text[:limit]


def error_text(payload: Any) -> str:
  """The provider's error message from a body or an event, short and on one line."""
  if isinstance(payload, bytes):
    payload = payload.decode("utf-8", "replace")
  if isinstance(payload, str):
    try:
      payload = json.loads(payload)
    except ValueError:
      pass
  payload = hide_inputs(payload)
  found = payload
  for key in ("error", "errors", "message", 0):
    if isinstance(found, list) and found:
      found = found[0]
    if isinstance(found, dict) and key in found:
      found = found[key]
  if isinstance(found, dict | list):
    found = json.dumps(found, ensure_ascii=False)
  text = " ".join(str(found).split())
  return text[:ERROR_TEXT_LIMIT] or "no message"


def count(value: Any) -> int | None:
  """Read a positive token count from a number or a digit string."""
  if isinstance(value, bool) or not isinstance(value, (int, str)):
    return None
  text = str(value)
  return int(text) if text.isdigit() and int(text) > 0 else None


def limits(max_input: Any = None, max_output: Any = None) -> dict[str, Any]:
  """Token limit columns, with `max_tokens` as a copy of the output limit."""
  output = count(max_output)
  return {
    "max_input_tokens": count(max_input),
    "max_output_tokens": output,
    "max_tokens": output,
  }


def listed(names: Any, columns: Mapping[str, str]) -> dict[str, bool]:
  """Mark each column by its name in a full list. No list gives no values."""
  if not isinstance(names, list):
    return {}
  return {column: name in names for column, name in columns.items()}


def modalities(inputs: Any, outputs: Any) -> dict[str, bool]:
  """Media columns from full input and output modality lists."""
  found = {}
  if isinstance(inputs, list):
    found["supports_vision"] = "image" in inputs
    found["supports_pdf_input"] = "pdf" in inputs or "file" in inputs
    found["supports_audio_input"] = "audio" in inputs
  if isinstance(outputs, list):
    found["supports_audio_output"] = "audio" in outputs
  return found


def openrouter_columns(row: dict) -> dict[str, Any]:
  """Store columns from one OpenRouter-shaped discovery row."""
  top = row.get("top_provider") or {}
  architecture = row.get("architecture") or {}
  reasoning = row.get("reasoning") or {}
  return {
    **limits(
      top.get("context_length") or row.get("context_length"),
      top.get("max_completion_tokens"),
    ),
    **listed(
      row.get("supported_parameters"),
      {
        "supports_function_calling": "tools",
        "supports_tool_choice": "tool_choice",
        "supports_parallel_function_calling": "parallel_tool_calls",
        "supports_response_schema": "structured_outputs",
        "supports_reasoning": "reasoning",
        "supports_web_search": "web_search_options",
      },
    ),
    **modalities(
      architecture.get("input_modalities"), architecture.get("output_modalities")
    ),
    "reasoning_effort": reasoning.get("default_effort")
    if isinstance(reasoning, dict)
    else None,
  }


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


def known_fields(message: Any, fields: Mapping[str, frozenset[str]]) -> Any:
  """The message with only the fields that its role allows. Other roles stay as they are."""
  allowed = fields.get(message.get("role")) if isinstance(message, dict) else None
  if allowed is None:
    return message
  return {key: value for key, value in message.items() if key in allowed}


class OpenAIProvider:
  """A provider that serves the OpenAI Chat Completions API."""

  unsupported: tuple[str, ...] = ()
  defaults: ClassVar[Mapping[str, str]] = {"api_type": "openai"}
  # The message fields for each role, for a provider that rejects other fields. Empty: all fields.
  message_fields: ClassVar[Mapping[str, frozenset[str]]] = {}
  # The name of the embeddings field that sets the vector size.
  dimensions_field: ClassVar[str] = "dimensions"
  # The transcription form fields that the provider accepts, apart from model and file.
  transcribe_fields: ClassVar[tuple[str, ...]] = (
    "language",
    "prompt",
    "response_format",
    "temperature",
    "timestamp_granularities[]",
  )
  # The speech request fields that the provider accepts, apart from model.
  speech_fields: ClassVar[tuple[str, ...]] = (
    "input",
    "voice",
    "instructions",
    "response_format",
    "speed",
  )
  # The image request fields that the provider accepts, apart from model.
  image_fields: ClassVar[tuple[str, ...]] = (
    "prompt",
    "n",
    "size",
    "quality",
    "style",
    "response_format",
    "background",
    "output_format",
    "output_compression",
    "moderation",
  )

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

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """Store columns from one discovery row. The base class reads none."""
    return {}

  @staticmethod
  def discoverable(row: dict) -> bool:
    """Whether one discovery row can go into the catalog. The base class keeps all rows."""
    return True

  @staticmethod
  def auth(key: str) -> dict[str, str]:
    """The header that carries the API key."""
    return {"Authorization": f"Bearer {key}"}

  def headers(self) -> dict[str, str]:
    return {"content-type": "application/json", **self.auth(self.key)}

  def url(self, slug: str, payload: dict) -> str:
    return self.base + "/chat/completions"

  def body(self, slug: str, payload: dict) -> dict:
    if not self.message_fields:
      return {**payload, "model": slug}
    messages = [known_fields(m, self.message_fields) for m in payload["messages"]]
    return {**payload, "model": slug, "messages": messages}

  def request(self, slug: str, payload: dict) -> tuple[str, dict, dict[str, str]]:
    if not isinstance(payload.get("messages"), list):
      raise ProviderError("messages must be a list")
    for field in self.unsupported:
      if payload.get(field) is not None:
        raise ProviderError(f"Unsupported native field: {field}")
    return self.url(slug, payload), self.body(slug, payload), self.headers()

  def completion(self, answer: dict, model: str) -> dict:
    return answer

  def embed_request(self, slug: str, payload: dict) -> tuple[str, dict, dict[str, str]]:
    """The upstream embeddings request, which always asks for float vectors."""
    body = {"model": slug, "input": payload["input"]}
    if payload.get("dimensions") is not None:
      body[self.dimensions_field] = payload["dimensions"]
    return self.base + "/embeddings", body, self.headers()

  def transcribe_request(
    self, slug: str, fields: dict[str, Any], audio: tuple[str, bytes, str]
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """The upstream multipart request, with only the fields that the provider accepts."""
    data = {
      key: value for key, value in fields.items() if key in self.transcribe_fields
    }
    content = {"data": {**data, "model": slug}, "files": {"file": audio}}
    return self.base + "/audio/transcriptions", content, self.auth(self.key)

  def transcription(
    self, response: httpx.Response, fields: dict[str, Any]
  ) -> tuple[bytes, str]:
    """The answer body and its media type, as the provider sends them."""
    return response.content, response.headers.get("content-type", "application/json")

  def speech_request(
    self, slug: str, payload: dict
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """The upstream speech request, with only the fields that the provider accepts."""
    body = {key: value for key, value in payload.items() if key in self.speech_fields}
    return (
      self.base + "/audio/speech",
      {"json": {**body, "model": slug}},
      self.headers(),
    )

  def speech(self, response: httpx.Response, payload: dict) -> tuple[bytes, str]:
    """The audio and its media type, as the provider sends them."""
    return response.content, response.headers.get("content-type", "audio/mpeg")

  def image_request(
    self, slug: str, payload: dict
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """The upstream image request, with only the fields that the provider accepts."""
    body = {key: value for key, value in payload.items() if key in self.image_fields}
    url = self.base + "/images/generations"
    return url, {"json": {**body, "model": slug}}, self.headers()

  def images(self, response: httpx.Response, payload: dict) -> dict:
    """The OpenAI images answer, as the provider sends it."""
    answer = response.json()
    if not isinstance(answer, dict) or not isinstance(answer.get("data"), list):
      raise ProviderError("Invalid images answer")
    return answer

  def embeddings(self, answer: dict, model: str) -> dict:
    """The OpenAI embeddings answer, with the Daedalus model name."""
    if not isinstance(answer.get("data"), list):
      raise ProviderError("Invalid embeddings answer")
    return {**answer, "model": model}

  async def stream(
    self, response: httpx.Response, model: str, include_usage: bool
  ) -> AsyncIterator[bytes]:
    try:
      async for chunk in response.aiter_bytes():
        if chunk:
          yield chunk
    finally:
      await response.aclose()
