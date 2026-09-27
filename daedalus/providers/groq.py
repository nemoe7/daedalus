from collections.abc import Mapping
from typing import Any, ClassVar

from daedalus.providers.base import OpenAIProvider, limits, listed, modalities


class GroqProvider(OpenAIProvider):
  """Groq through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.groq.com/openai/v1",
    "discovery_url": "https://api.groq.com/openai/v1/models",
  }
  # Groq rejects all other message fields, for example reasoning_content.
  message_fields: ClassVar[Mapping[str, frozenset[str]]] = {
    "system": frozenset({"role", "content", "name"}),
    "user": frozenset({"role", "content", "name"}),
    "assistant": frozenset({"role", "content", "name", "tool_calls"}),
    "tool": frozenset({"role", "content", "tool_call_id"}),
  }

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """Store columns from one Groq row."""
    return {
      **limits(row.get("context_window"), row.get("max_completion_tokens")),
      **listed(
        row.get("supported_features"),
        {
          "supports_function_calling": "tools",
          "supports_response_schema": "structured_outputs",
          "supports_reasoning": "reasoning",
        },
      ),
      **modalities(row.get("input_modalities"), row.get("output_modalities")),
    }
