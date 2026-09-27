from collections.abc import Mapping
from typing import Any, ClassVar

from daedalus.providers.base import OpenAIProvider, limits

# The message fields that Mistral accepts for each role. It rejects all other fields.
MESSAGE_FIELDS = {
  "system": frozenset({"role", "content"}),
  "user": frozenset({"role", "content"}),
  "assistant": frozenset({"role", "content", "tool_calls", "prefix"}),
  "tool": frozenset({"role", "content", "tool_call_id", "name"}),
}


def known_fields(message: Any) -> Any:
  """The message without the fields that Mistral does not accept."""
  if not isinstance(message, dict):
    return message
  fields = MESSAGE_FIELDS.get(message.get("role"))
  if fields is None:
    return message
  return {key: value for key, value in message.items() if key in fields}


class MistralProvider(OpenAIProvider):
  """Mistral through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.mistral.ai/v1",
    "discovery_url": "https://api.mistral.ai/v1/models",
  }

  def body(self, slug: str, payload: dict) -> dict:
    """The OpenAI body, without message fields that Mistral rejects."""
    return {
      **payload,
      "model": slug,
      "messages": [known_fields(m) for m in payload["messages"]],
    }

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
