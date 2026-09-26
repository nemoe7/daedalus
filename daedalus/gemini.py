"""Translate between the Gemini generateContent shape and the OpenAI chat shape."""

import json
import logging
from typing import Any

logger = logging.getLogger("daedalus")

ROLE = {"model": "assistant", "user": "user", "system": "system", "tool": "tool"}

FINISH_REASON = {
  "stop": "STOP",
  "length": "MAX_TOKENS",
  "content_filter": "SAFETY",
  "tool_calls": "STOP",
}

UNSPECIFIED = "FINISH_REASON_UNSPECIFIED"

# Gemini config keys with no OpenAI field.
UNSUPPORTED = ("top_k", "topK")

CONFIG = {
  "temperature": "temperature",
  "top_p": "top_p",
  "topP": "top_p",
  "max_output_tokens": "max_tokens",
  "maxOutputTokens": "max_tokens",
  "stop_sequences": "stop",
  "stopSequences": "stop",
  "candidate_count": "n",
  "candidateCount": "n",
  "presence_penalty": "presence_penalty",
  "presencePenalty": "presence_penalty",
  "frequency_penalty": "frequency_penalty",
  "frequencyPenalty": "frequency_penalty",
  "seed": "seed",
}


def pick(source: Any, *names: str) -> Any:
  """The first present key, so camelCase and snake_case both work."""
  if not isinstance(source, dict):
    return None
  for name in names:
    if source.get(name) is not None:
      return source[name]
  return None


def first(items: Any) -> dict[str, Any]:
  """The first entry of a list, or an empty dict."""
  if isinstance(items, list) and items and isinstance(items[0], dict):
    return items[0]
  return {}


def loads(raw: Any) -> dict[str, Any]:
  """Tool call arguments as a dict, whatever the upstream sent."""
  if isinstance(raw, dict):
    return raw
  if isinstance(raw, str):
    try:
      value = json.loads(raw)
    except ValueError:
      return {}
    return value if isinstance(value, dict) else {}
  return {}


def parts_text(parts: Any) -> str:
  """Join the text parts of one block."""
  if not isinstance(parts, list):
    return ""
  found = [part.get("text") for part in parts if isinstance(part, dict)]
  return "\n".join(text for text in found if isinstance(text, str) and text)


class CallIds:
  """Keep one OpenAI tool call id per function name, so replies match calls."""

  def __init__(self) -> None:
    self.seen: dict[str, str] = {}

  def issue(self, name: str) -> str:
    call_id = f"call_{len(self.seen)}"
    self.seen[name] = call_id
    return call_id

  def of(self, name: str) -> str:
    return self.seen.get(name, "call_0")


def tool_calls(parts: list[dict[str, Any]], ids: CallIds) -> list[dict[str, Any]]:
  """Gemini function calls as OpenAI tool calls."""
  calls: list[dict[str, Any]] = []
  for part in parts:
    call = pick(part, "function_call", "functionCall")
    if not isinstance(call, dict):
      continue
    name = str(call.get("name", ""))
    calls.append(
      {
        "id": ids.issue(name),
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(call.get("args") or {})},
      }
    )
  return calls


def tool_messages(parts: list[dict[str, Any]], ids: CallIds) -> list[dict[str, Any]]:
  """Gemini function responses as OpenAI tool messages."""
  messages: list[dict[str, Any]] = []
  for part in parts:
    reply = pick(part, "function_response", "functionResponse")
    if not isinstance(reply, dict):
      continue
    name = str(reply.get("name", ""))
    messages.append(
      {
        "role": "tool",
        "tool_call_id": ids.of(name),
        "content": json.dumps(reply.get("response") or {}),
      }
    )
  return messages


def image_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
  """Gemini inline data as OpenAI image content parts."""
  images: list[dict[str, Any]] = []
  for part in parts:
    blob = pick(part, "inline_data", "inlineData")
    if not isinstance(blob, dict):
      continue
    mime = str(pick(blob, "mime_type", "mimeType") or "image/png")
    data = str(blob.get("data") or "")
    images.append(
      {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
    )
  return images


def block(content: dict[str, Any], ids: CallIds) -> list[dict[str, Any]]:
  """One Gemini content block as one or more OpenAI messages."""
  role = ROLE.get(str(content.get("role") or "user"), "user")
  parts = [part for part in content.get("parts") or [] if isinstance(part, dict)]
  text = parts_text(parts)
  out: list[dict[str, Any]] = []
  calls = tool_calls(parts, ids)
  if calls:
    message: dict[str, Any] = {"role": "assistant", "tool_calls": calls}
    if text:
      message["content"] = text
    out.append(message)
  else:
    images = image_parts(parts)
    if images:
      lead = [{"type": "text", "text": text}] if text else []
      out.append({"role": role, "content": [*lead, *images]})
    elif text:
      out.append({"role": role, "content": text})
  out.extend(tool_messages(parts, ids))
  return out


def to_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
  """Gemini contents as OpenAI messages."""
  ids = CallIds()
  messages: list[dict[str, Any]] = []
  text = parts_text(
    pick(pick(body, "system_instruction", "systemInstruction"), "parts")
  )
  if text:
    messages.append({"role": "system", "content": text})
  for content in body.get("contents") or []:
    if isinstance(content, dict):
      messages.extend(block(content, ids))
  return messages


def response_format(config: dict[str, Any]) -> dict[str, Any] | None:
  """A Gemini response mime type and schema as an OpenAI response format."""
  if pick(config, "response_mime_type", "responseMimeType") != "application/json":
    return None
  schema = pick(
    config,
    "response_json_schema",
    "responseJsonSchema",
    "response_schema",
    "responseSchema",
  )
  if not isinstance(schema, dict):
    return {"type": "json_object"}
  return {"type": "json_schema", "json_schema": {"name": "response", "schema": schema}}


def tools(config: dict[str, Any]) -> list[dict[str, Any]]:
  """Gemini function declarations as OpenAI tools."""
  found: list[dict[str, Any]] = []
  for tool in config.get("tools") or []:
    if not isinstance(tool, dict):
      continue
    declared = pick(tool, "function_declarations", "functionDeclarations") or []
    for entry in declared:
      if not isinstance(entry, dict):
        continue
      function: dict[str, Any] = {"name": entry.get("name", "")}
      if entry.get("description"):
        function["description"] = entry["description"]
      parameters = pick(entry, "parameters", "parameters_schema", "parametersSchema")
      if parameters:
        function["parameters"] = parameters
      found.append({"type": "function", "function": function})
  return found


def to_openai(model: str, body: dict[str, Any]) -> dict[str, Any]:
  """A Gemini generateContent request as an OpenAI chat request."""
  payload: dict[str, Any] = {"model": model, "messages": to_messages(body)}
  config = pick(body, "generation_config", "generationConfig")
  config = config if isinstance(config, dict) else {}
  for key, target in CONFIG.items():
    if config.get(key) is not None:
      payload[target] = config[key]
  for key in UNSUPPORTED:
    if config.get(key) is not None:
      logger.info("gemini %s has no OpenAI field, so it is dropped", key)
  fmt = response_format(config)
  if fmt is not None:
    payload["response_format"] = fmt
  declared = tools(config)
  if declared:
    payload["tools"] = declared
  return payload


def usage(answer: dict[str, Any]) -> dict[str, Any]:
  """OpenAI usage counts as Gemini usage metadata."""
  counts = answer.get("usage") or {}
  return {
    "promptTokenCount": counts.get("prompt_tokens", 0),
    "candidatesTokenCount": counts.get("completion_tokens", 0),
    "totalTokenCount": counts.get("total_tokens", 0),
  }


def candidate(message: dict[str, Any], choice: dict[str, Any]) -> dict[str, Any]:
  """One OpenAI choice as one Gemini candidate."""
  parts: list[dict[str, Any]] = []
  text = message.get("content")
  if isinstance(text, str) and text:
    parts.append({"text": text})
  for call in message.get("tool_calls") or []:
    if not isinstance(call, dict):
      continue
    function = call.get("function") or {}
    parts.append(
      {
        "functionCall": {
          "name": function.get("name", ""),
          "args": loads(function.get("arguments")),
        }
      }
    )
  result: dict[str, Any] = {
    "content": {"parts": parts, "role": "model"},
    "index": choice.get("index", 0),
  }
  reason = choice.get("finish_reason")
  if reason:
    result["finishReason"] = FINISH_REASON.get(str(reason), UNSPECIFIED)
  return result


def to_gemini(answer: dict[str, Any], model: str) -> dict[str, Any]:
  """An OpenAI chat answer as a Gemini generateContent answer."""
  choice = first(answer.get("choices"))
  return {
    "candidates": [candidate(choice.get("message") or {}, choice)],
    "usageMetadata": usage(answer),
    "modelVersion": answer.get("model") or model,
    "responseId": answer.get("id") or "",
  }


class StreamCalls:
  """Collect streamed tool call fragments, and emit whole calls at the end."""

  def __init__(self) -> None:
    self.slots: dict[int, dict[str, str]] = {}

  def take(self, delta: dict[str, Any]) -> None:
    """Add one chunk of a streamed tool call."""
    for fragment in delta.get("tool_calls") or []:
      if not isinstance(fragment, dict):
        continue
      slot = self.slots.setdefault(
        int(fragment.get("index") or 0), {"name": "", "arguments": ""}
      )
      function = fragment.get("function") or {}
      if function.get("name"):
        slot["name"] = str(function["name"])
      if function.get("arguments"):
        slot["arguments"] += str(function["arguments"])

  def drain(self) -> list[dict[str, Any]]:
    """The collected calls as Gemini function call parts, in stream order."""
    found = [
      {"functionCall": {"name": slot["name"], "args": loads(slot["arguments"])}}
      for _, slot in sorted(self.slots.items())
    ]
    self.slots.clear()
    return found


def chunk_to_gemini(
  chunk: dict[str, Any], model: str, calls: StreamCalls | None = None
) -> dict[str, Any]:
  """One OpenAI stream chunk as one Gemini stream chunk."""
  choice = first(chunk.get("choices"))
  delta = choice.get("delta") or {}
  parts: list[dict[str, Any]] = []
  text = delta.get("content")
  if isinstance(text, str) and text:
    parts.append({"text": text})
  reason = choice.get("finish_reason")
  if calls is not None:
    calls.take(delta)
    if reason == "tool_calls":
      parts.extend(calls.drain())
  result: dict[str, Any] = {
    "candidates": [
      {"content": {"parts": parts, "role": "model"}, "index": choice.get("index", 0)}
    ],
    "modelVersion": chunk.get("model") or model,
  }
  if reason:
    result["candidates"][0]["finishReason"] = FINISH_REASON.get(
      str(reason), UNSPECIFIED
    )
  if chunk.get("usage"):
    result["usageMetadata"] = usage(chunk)
  return result
