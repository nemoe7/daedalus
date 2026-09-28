import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, ClassVar

import httpx

from daedalus.providers.base import OpenAIProvider, frame, limits

# Mistral takes only high or none. The other OpenAI values map to the nearest of the 2.
EFFORTS: Mapping[str, str] = {
  "none": "none",
  "minimal": "none",
  "low": "high",
  "medium": "high",
  "high": "high",
  "xhigh": "high",
}


def plain(message: Any) -> Any:
  """The message or delta with a string content, and its thinking chunks in `reasoning_content`."""
  if not isinstance(message, dict) or not isinstance(message.get("content"), list):
    return message
  text, thinking = [], []
  for chunk in message["content"]:
    if not isinstance(chunk, dict):
      continue
    if chunk.get("type") == "text":
      text.append(str(chunk.get("text") or ""))
    elif chunk.get("type") == "thinking":
      parts = chunk.get("thinking")
      for part in parts if isinstance(parts, list) else []:
        if isinstance(part, dict):
          thinking.append(str(part.get("text") or ""))
  found = {**message, "content": "".join(text)}
  if thinking:
    found["reasoning_content"] = "".join(thinking)
  return found


def chunked(message: Any) -> Any:
  """An assistant message with its `reasoning_content` as a thinking chunk before the text."""
  if not isinstance(message, dict) or message.get("role") != "assistant":
    return message
  thinking = message.get("reasoning_content")
  if not isinstance(thinking, str) or not thinking:
    return message
  content = message.get("content")
  if isinstance(content, list):
    rest = content
  elif isinstance(content, str) and content:
    rest = [{"type": "text", "text": content}]
  else:
    rest = []
  chunk = {"type": "thinking", "thinking": [{"type": "text", "text": thinking}]}
  return {**message, "content": [chunk, *rest]}


def plain_choices(answer: dict, key: str) -> dict:
  """The answer with each choice message or delta made plain."""
  choices = answer.get("choices")
  if not isinstance(choices, list):
    return answer
  return {
    **answer,
    "choices": [
      {**c, key: plain(c.get(key))} if isinstance(c, dict) else c for c in choices
    ],
  }


class MistralProvider(OpenAIProvider):
  """Mistral through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.mistral.ai/v1",
    "discovery_url": "https://api.mistral.ai/v1/models",
  }

  # Mistral rejects all other message fields, for example reasoning_content.
  message_fields: ClassVar[Mapping[str, frozenset[str]]] = {
    "system": frozenset({"role", "content"}),
    "user": frozenset({"role", "content"}),
    "assistant": frozenset({"role", "content", "tool_calls", "prefix"}),
    "tool": frozenset({"role", "content", "tool_call_id", "name"}),
  }
  dimensions_field: ClassVar[str] = "output_dimension"
  transcribe_fields: ClassVar[tuple[str, ...]] = ("language", "temperature")

  def body(self, slug: str, payload: dict) -> dict:
    messages = [chunked(message) for message in payload["messages"]]
    found = super().body(slug, {**payload, "messages": messages})
    effort = found.get("reasoning_effort")
    if isinstance(effort, str) and effort in EFFORTS:
      found["reasoning_effort"] = EFFORTS[effort]
    return found

  def completion(self, answer: dict, model: str) -> dict:
    return plain_choices(answer, "message")

  async def stream(
    self, response: httpx.Response, model: str, include_usage: bool
  ) -> AsyncIterator[bytes]:
    try:
      async for line in response.aiter_lines():
        if not line.startswith("data:"):
          continue
        raw = line[5:].strip()
        if raw == "[DONE]":
          yield b"data: [DONE]\n\n"
          continue
        event = json.loads(raw)
        yield frame(plain_choices(event, "delta") if isinstance(event, dict) else event)
    finally:
      await response.aclose()

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """Store columns from one Mistral row, whose capabilities are booleans."""
    found = row.get("capabilities") or {}
    return {
      "mode": "chat" if found.get("completion_chat") is True else None,
      **limits(row.get("max_context_length")),
      "supports_function_calling": found.get("function_calling"),
      "supports_reasoning": found.get("reasoning"),
      "supports_vision": found.get("vision"),
      "supports_audio_input": found.get("audio"),
    }
