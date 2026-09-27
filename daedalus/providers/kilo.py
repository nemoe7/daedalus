from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider


class KiloProvider(OpenAIProvider):
  """Kilo Gateway through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.kilo.ai/api/gateway",
    "discovery_url": "https://api.kilo.ai/api/gateway/models",
  }
