from collections.abc import Mapping
from typing import Any, ClassVar

from daedalus.providers.base import (
  OpenAIProvider,
  limits,
)


def free_row(row: dict) -> bool:
  """Whether every price of every pricing band of one row is 0."""
  bands = row.get("pricing")
  if not isinstance(bands, list) or not bands:
    return False
  for band in bands:
    if not isinstance(band, dict):
      return False
    for key in ("input_price", "cached_price", "output_price"):
      try:
        if float(band.get(key)) != 0:
          return False
      except (TypeError, ValueError):
        return False
  return True


class RequestyProvider(OpenAIProvider):
  """Requesty through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://router.requesty.ai/v1",
    "discovery_url": "https://router.requesty.ai/v1/models",
  }
  stream_usage: ClassVar[bool] = True

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """Store columns from one Requesty row."""
    reasoning = row.get("reasoning") or {}
    efforts = reasoning.get("supported_efforts")
    return {
      **limits(row.get("context_window"), row.get("max_output_tokens")),
      "supports_function_calling": row.get("supports_tool_calling"),
      "supports_response_schema": row.get("supports_output_json_schema"),
      "supports_reasoning": row.get("supports_reasoning"),
      "supports_web_search": row.get("supports_web_search"),
      "supports_vision": row.get("supports_vision"),
      "supported_efforts": efforts
      if isinstance(efforts, list)
      and efforts
      and all(isinstance(name, str) and name for name in efforts)
      else None,
    }

  @staticmethod
  def discoverable(row: dict) -> bool:
    """Whether one row is a free model: every price of every band is 0."""
    return free_row(row)
