from collections.abc import Mapping
from typing import Any, ClassVar

from daedalus.providers.base import OpenAIProvider, limits


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


class CloudflareProvider(OpenAIProvider):
  """Cloudflare Workers AI through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "os.environ/CLOUDFLARE_API_BASE",
    # Discovery gets text-generation models only. Remove the task filter when Daedalus supports multimodal input.
    "discovery_url": "https://api.cloudflare.com/client/v4/accounts/os.environ/CLOUDFLARE_ACCOUNT_ID/ai/models/search?per_page=100&task=Text%20Generation",
  }

  def body(self, slug: str, payload: dict) -> dict:
    """The OpenAI body, with string content where Workers AI models need it."""
    return {
      **payload,
      "model": slug,
      "messages": [text_only(m) for m in payload["messages"]],
    }

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """Store columns from one Cloudflare row. Properties show true values only."""
    found = {
      item.get("property_id"): item.get("value")
      for item in row.get("properties") or []
      if isinstance(item, dict)
    }
    effort = found.get("reasoning_effort")
    task = (row.get("task") or {}).get("name")
    return {
      "mode": "chat" if task == "Text Generation" else None,
      **limits(found.get("context_window")),
      "reasoning_effort": effort.get("default_effort")
      if isinstance(effort, dict)
      else None,
      "supports_function_calling": found.get("function_calling") == "true" or None,
      "supports_reasoning": found.get("reasoning") == "true" or None,
      "supports_vision": found.get("vision") == "true" or None,
    }
