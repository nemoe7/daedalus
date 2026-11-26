from collections.abc import Mapping
from typing import Any, ClassVar

from daedalus.providers.base import OpenAIProvider, clean_display_name, count


class VireonixProvider(OpenAIProvider):
  """Vireonix through its OpenAI-compatible API, with no API key."""

  keyless: ClassVar[bool] = True
  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://vireonix.ai/v1",
    "discovery_url": "https://vireonix.ai/v1/models",
  }

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """Store columns from one Vireonix row."""
    name = clean_display_name(
      row.get("display_name") or row.get("name"), str(row.get("id") or "")
    )
    result = {"name": name} if name else {}
    context = count(row.get("context_window"))
    if context is not None:
      result["max_input_tokens"] = context
    return result
