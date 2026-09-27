from collections.abc import Mapping
from typing import Any, ClassVar

from daedalus.providers.base import OpenAIProvider, limits


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
