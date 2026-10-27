import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx


class ProviderError(ValueError):
  pass


class UpstreamOverload(ProviderError):
  """Safe line names overloaded provider and leaves out relay name."""

  def __init__(self, text: str) -> None:
    super().__init__(text)
    marker = "Upstream error from "
    self.provider = (
      text.split(marker, 1)[1].split(":", 1)[0].strip() if marker in text else ""
    )

  @property
  def safe_line(self) -> str:
    """The user-facing line for one overload."""
    if self.provider:
      return f"The {self.provider} upstream is temporarily overloaded."
    return "The upstream provider is temporarily overloaded."


ERROR_TEXT_LIMIT = 300


def stream_error(text: str) -> ProviderError:
  """Classify one stream error event: an overload gets its own class, the rest keep the relay prefix."""
  if "overloaded" in text.lower():
    return UpstreamOverload(text)
  return ProviderError(f"Upstream stream error: {text}")


# One uploaded file: the file name, bytes, and media type.
Upload = tuple[str, bytes, str]


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
  try:
    text = str(value)
    parsed = int(text) if text.isdigit() else 0
  except ValueError:
    return None
  return parsed if parsed > 0 else None


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


# The mode of a model that makes no text, by its first known output modality.
OUTPUT_MODES = {
  "speech": "audio_speech",
  "image": "image_generation",
  "embeddings": "embedding",
  "video": "video_generation",
  "decisions": "decisions",
  "rerank": "rerank",
}


def output_mode(outputs: Any) -> dict[str, str]:
  """The mode column from a full output modality list: none for a text model."""
  if not isinstance(outputs, list) or not outputs or "text" in outputs:
    return {}
  for name in outputs:
    if name in OUTPUT_MODES:
      return {"mode": OUTPUT_MODES[name]}
  return {"mode": str(outputs[0])}


def free_price(row: dict) -> bool:
  """Whether each price field of one gateway row is 0."""
  pricing = row.get("pricing")
  if not isinstance(pricing, dict) or not pricing:
    return False
  for value in pricing.values():
    try:
      if float(value) != 0:
        return False
    except (TypeError, ValueError):
      return False
  return True


def free_stealth(slug: str, row: dict) -> bool:
  """Whether one gateway row is a stealth model with price 0 in each price field."""
  return slug.startswith("stealth/") and free_price(row)


ACRONYMS: Mapping[str, str] = {
  "ai": "AI",
  "asr": "ASR",
  "bge": "BGE",
  "cf": "CF",
  "cls": "CLS",
  "en": "EN",
  "es": "ES",
  "fp8": "FP8",
  "fp16": "FP16",
  "glm": "GLM",
  "gpt": "GPT",
  "hf": "HF",
  "ibm": "IBM",
  "int4": "INT4",
  "int8": "INT8",
  "it": "IT",
  "lcm": "LCM",
  "lfm": "LFM",
  "llm": "LLM",
  "lora": "LoRA",
  "m3": "M3",
  "moe": "MoE",
  "ocr": "OCR",
  "oss": "OSS",
  "qwq": "QwQ",
  "r1": "R1",
  "rag": "RAG",
  "rerank": "Rerank",
  "sdxl": "SDXL",
  "sft": "SFT",
  "tts": "TTS",
  "ui": "UI",
  "v1": "v1",
  "v2": "v2",
  "v3": "v3",
  "v4": "v4",
  "vl": "VL",
  "xl": "XL",
  "xs": "XS",
}
BRAND_WORDS: Mapping[str, str] = {
  "deepseek": "DeepSeek",
  "dreamshaper": "DreamShaper",
  "embeddinggemma": "EmbeddingGemma",
  "eurollm": "EuroLLM",
  "flux": "FLUX",
  "gemma": "Gemma",
  "glm": "GLM",
  "granite": "Granite",
  "kani": "Kani",
  "leanstral": "Leanstral",
  "llama": "Llama",
  "melotts": "MeloTTS",
  "ministral": "Ministral",
  "mistral": "Mistral",
  "nemotron": "Nemotron",
  "plamo": "PLaMo",
  "qwen": "Qwen",
  "qwen2": "Qwen2",
  "qwen2.5": "Qwen2.5",
  "qwen3": "Qwen3",
  "qwen3.5": "Qwen3.5",
  "qwen3.8": "Qwen3.8",
  "resnet": "ResNet",
  "sea": "SEA",
  "lion": "LION",
  "smart": "Smart",
  "turn": "Turn",
  "sqlcoder": "SQLCoder",
  "voxtral": "Voxtral",
  "whisper": "Whisper",
}


def clean_display_name(raw_name: Any, slug: str = "") -> str | None:
  """Normalize an upstream model display name when it differs from the raw slug."""
  if not isinstance(raw_name, str):
    return None
  text = raw_name.strip()
  if not text:
    return None
  tail = slug.rsplit("/", 1)[-1]
  tail_bare = re.sub(r":free$", "", tail, flags=re.IGNORECASE)
  if text in (slug, tail, tail_bare):
    return None
  text = re.sub(r"\s*\(free\)\s*$", "", text, flags=re.IGNORECASE).strip()
  if ": " in text:
    text = text.split(": ", 1)[1].strip()
  if "/" in text and slug and text.lower() == slug.lower():
    return None
  parts = [part for part in slug.split("/") if part and not part.startswith("@")]
  dev_norm = re.sub(r"[^a-z0-9]", "", parts[-2].lower()) if len(parts) >= 2 else ""
  if len(dev_norm) >= 3:
    words = text.split()
    acc = ""
    for index, word in enumerate(words[:-1]):
      acc += re.sub(r"[^a-z0-9]", "", word.lower())
      if acc == dev_norm:
        text = " ".join(words[index + 1 :]).strip()
        break
      if not dev_norm.startswith(acc):
        break
  if not text or text in (slug, tail, tail_bare):
    return None
  return text


def humanize_slug(slug: str) -> str:
  """Format the model segment of a slug into a readable title."""
  tail = slug.rsplit("/", 1)[-1]
  tail = re.sub(r":free$", "", tail, flags=re.IGNORECASE)
  tail = re.sub(r"(?<=\bv\d)-(?=\d\b)", ".", tail, flags=re.IGNORECASE)
  tail = re.sub(r"(?<=\b\d)-(?=\d-\d\b)", ".", tail)
  tail = re.sub(r"(?<=\d\.\d)-(?=\d\b)", ".", tail)
  tokens = [token for token in tail.split("-") if token]
  out: list[str] = []
  for token in tokens:
    low = token.lower()
    if low in BRAND_WORDS:
      out.append(BRAND_WORDS[low])
    elif low in ACRONYMS:
      out.append(ACRONYMS[low])
    elif re.fullmatch(r"\d+(?:\.\d+)?[bmkv]", low):
      out.append(token[:-1] + token[-1].upper())
    elif re.fullmatch(r"a\d+(?:\.\d+)?b", low):
      out.append("A" + token[1:-1] + "B")
    elif re.fullmatch(r"v\d+(?:\.\d+)+", low):
      out.append("v" + token[1:])
    elif re.fullmatch(r"\d+e", low):
      out.append(token.upper())
    elif any(char.isupper() for char in token):
      out.append(token)
    else:
      out.append(token.capitalize())
  return (" ".join(out) or tail or slug).replace("SEA LION", "SEA-LION")


def openrouter_columns(row: dict) -> dict[str, Any]:
  """Store columns from one OpenRouter-shaped discovery row."""
  top = row.get("top_provider") or {}
  architecture = row.get("architecture") or {}
  reasoning = row.get("reasoning") or {}
  efforts = reasoning.get("supported_efforts")
  return {
    "name": clean_display_name(row.get("name"), str(row.get("id") or "")),
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
    **output_mode(architecture.get("output_modalities")),
    "reasoning_effort": reasoning.get("default_effort")
    if isinstance(reasoning, dict)
    else None,
    # The efforts of this model, when the gateway lists them. A stray name drops the whole list.
    "supported_efforts": efforts
    if isinstance(efforts, list)
    and efforts
    and all(isinstance(name, str) and name for name in efforts)
    else None,
  }


def first(items: Any) -> dict:
  return (
    items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
  )


def choice_rows(answer: dict, field: str) -> list[dict]:
  """The choices with object-valued messages or deltas, or a provider error."""
  found = answer.get("choices")
  if found is None:
    return []
  if not isinstance(found, list) or any(not isinstance(row, dict) for row in found):
    raise ProviderError("Invalid upstream choices")
  if any(
    row.get(field) is not None and not isinstance(row[field], dict) for row in found
  ):
    raise ProviderError(f"Invalid upstream {field}")
  return found


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


def check_wait(heard: float, wait: float | None) -> None:
  """Raise a ReadTimeout when `wait` seconds passed since the last data, with only keep-alive bytes."""
  if wait is not None and time.perf_counter() - heard > wait:
    raise httpx.ReadTimeout(f"Only keep-alive bytes for {wait:g}s")


async def events(
  response: httpx.Response, wait: float | None = None
) -> AsyncIterator[dict]:
  lines, heard = [], time.perf_counter()
  async for line in response.aiter_lines():
    if line.startswith("data:"):
      lines.append(line[5:].lstrip())
      heard = time.perf_counter()
    elif not line and lines:
      raw = "\n".join(lines)
      lines = []
      if raw == "[DONE]":
        return
      event = json.loads(raw)
      if not isinstance(event, dict):
        raise ProviderError("Invalid upstream stream event")
      yield event
    else:
      check_wait(heard, wait)
  if lines:
    raise ProviderError("Incomplete upstream stream event")


def known_fields(message: Any, fields: Mapping[str, frozenset[str]]) -> Any:
  """The message with only the fields that its role allows. Other roles stay as they are."""
  allowed = fields.get(message.get("role")) if isinstance(message, dict) else None
  if allowed is None:
    return message
  return {key: value for key, value in message.items() if key in allowed}


def effort_text(value: object) -> str | None:
  """The effort of a `reasoning_effort` value, which can be a string or an object."""
  if isinstance(value, dict):
    value = value.get("effort")
  return value if isinstance(value, str) else None


class OpenAIProvider:
  """A provider that serves the OpenAI Chat Completions API."""

  unsupported: tuple[str, ...] = ()
  # True when the provider documents `stream_options.include_usage`, so daedalus asks for the count.
  stream_usage: ClassVar[bool] = False
  defaults: ClassVar[Mapping[str, str]] = {"api_type": "openai"}
  # The message fields for each role, for a provider that rejects other fields. Empty: all fields.
  message_fields: ClassVar[Mapping[str, frozenset[str]]] = {}
  # The name of embeddings field that sets the vector size.
  dimensions_field: ClassVar[str] = "dimensions"
  # The transcription form fields that the provider accepts, apart from model and file.
  transcribe_fields: ClassVar[tuple[str, ...]] = (
    "language",
    "prompt",
    "response_format",
    "temperature",
    "timestamp_granularities[]",
  )
  # The transcription formats that the provider accepts.
  transcript_formats: ClassVar[tuple[str, ...]] = (
    "json",
    "text",
    "srt",
    "verbose_json",
    "vtt",
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

  @classmethod
  def configure(cls, config: Mapping[str, Any]) -> dict[str, Any]:
    """The config with values that this provider derives. The base class derives none."""
    return dict(config)

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
    """Store columns from one discovery row."""
    name = clean_display_name(
      row.get("title") or row.get("name"), str(row.get("id") or "")
    )
    return {"name": name} if name else {}

  @staticmethod
  def discoverable(row: dict) -> bool:
    """Whether one discovery row can go into the catalog. The base class keeps all rows."""
    return True

  @staticmethod
  def exclude_exempt(slug: str, row: dict) -> bool:
    """Whether one discovery row passes `exclude`. The base class exempts no row."""
    return False

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

  def effort(self, payload: dict) -> str | None:
    """The reasoning effort in an upstream body, as text for the dashboard."""
    return effort_text(payload.get("reasoning_effort"))

  def embed_request(self, slug: str, payload: dict) -> tuple[str, dict, dict[str, str]]:
    """The upstream embeddings request, which always asks for float vectors."""
    body = {"model": slug, "input": payload["input"]}
    if payload.get("dimensions") is not None:
      body[self.dimensions_field] = payload["dimensions"]
    return self.base + "/embeddings", body, self.headers()

  def transcribe_request(
    self, slug: str, fields: dict[str, Any], audio: tuple[str, bytes, str]
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """The upstream multipart request, with only fields that the provider accepts."""
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
    """The upstream speech request, with only fields that the provider accepts."""
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
    """The upstream image request, with only fields that the provider accepts."""
    body = {key: value for key, value in payload.items() if key in self.image_fields}
    url = self.base + "/images/generations"
    return url, {"json": {**body, "model": slug}}, self.headers()

  def edit_request(
    self, slug: str, fields: dict[str, Any], images: list[Upload], mask: Upload | None
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """The upstream multipart edit request, with only fields that the provider accepts."""
    data = {key: value for key, value in fields.items() if key in self.image_fields}
    name = "image[]" if len(images) > 1 else "image"
    files = [(name, image) for image in images]
    if mask is not None:
      files.append(("mask", mask))
    content = {"data": {**data, "model": slug}, "files": files}
    return self.base + "/images/edits", content, self.auth(self.key)

  def images(self, response: httpx.Response, payload: dict) -> dict:
    """The OpenAI images answer, as the provider sends it."""
    answer = response.json()
    if not isinstance(answer, dict) or not isinstance(answer.get("data"), list):
      raise ProviderError("Invalid images answer")
    return answer

  def embeddings(self, answer: dict, model: str) -> dict:
    """The OpenAI embeddings answer, with the daedalus model name."""
    if not isinstance(answer.get("data"), list):
      raise ProviderError("Invalid embeddings answer")
    return {**answer, "model": model}

  async def stream(
    self,
    response: httpx.Response,
    model: str,
    include_usage: bool,
    wait: float | None = None,
  ) -> AsyncIterator[bytes]:
    """The raw SSE bytes. `sse_data` applies the wait, because it sees comments."""
    try:
      async for chunk in response.aiter_bytes():
        if chunk:
          yield chunk
    finally:
      await response.aclose()
