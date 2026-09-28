from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider, free_stealth, openrouter_columns


class KiloProvider(OpenAIProvider):
  """Kilo Gateway through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.kilo.ai/api/gateway",
    "discovery_url": "https://api.kilo.ai/api/gateway/models",
  }

  columns = staticmethod(openrouter_columns)
  exclude_exempt = staticmethod(free_stealth)

  @staticmethod
  def discoverable(row: dict) -> bool:
    """Only models that make text: the gateway accepts only `/chat/completions`."""
    return "mode" not in openrouter_columns(row)
