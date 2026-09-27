from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider, openrouter_columns


class KiloProvider(OpenAIProvider):
  """Kilo Gateway through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.kilo.ai/api/gateway",
    "discovery_url": "https://api.kilo.ai/api/gateway/models",
  }

  columns = staticmethod(openrouter_columns)
